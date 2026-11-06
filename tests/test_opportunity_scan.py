import json
import os
import tempfile
import time
import unittest
from datetime import datetime,timezone
from unittest.mock import Mock,patch

from web import app as web, opportunity_scan as gap
from web.temperature_scan import event_summary


def market(label,price,yes,no,accepting=True):
    return {'groupItemTitle':label,'outcomes':['No','Yes'],'outcomePrices':[str(1-price),str(price)],
            'clobTokenIds':[str(no),str(yes)],'active':True,'closed':False,'acceptingOrders':accepting}


def sample(n=0,slug=None):
    day=datetime.now(timezone.utc).strftime('%B-%d-%Y').lower().replace('-0','-')
    event={'slug':slug or f'highest-temperature-in-london{n}-on-{day}','title':f'London {n}',
           'resolutionSource':'https://www.wunderground.com/history/daily/gb/london/EGLL',
           'active':True,'closed':False,'markets':[market('27°C',.98,100+n*10,101+n*10),
                 market('28°C',.01,102+n*10,103+n*10),market('29°C or higher',.001,104+n*10,105+n*10)]}
    data=event_summary(event);data['timezone']='Europe/London';data['quotes_at']=time.time();return data


def settings(**kw):
    return {'price':97,'leader_min':97,'gap':1,'gap_units':'C','side':'no','reference':'leader','quote':'ask','query':'',**kw}


def book(bid='0.95',ask='0.97',t=None):
    return {'bids':[{'price':bid,'size':'15'}] if bid else [],'asks':[{'price':ask,'size':'20'}] if ask else [],'fetched_at':t or time.time()}


class GapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=web.db.DB_FILE;self.old_config=dict(web.app.config)
        web.db.DB_FILE=os.path.join(self.tmp.name,'db');web.db.init_db();web.init_web_db();web._opp_sessions.clear()
    def tearDown(self):
        web.db.DB_FILE=self.old;web.app.config.clear();web.app.config.update(self.old_config);self.tmp.cleanup()
    def test_temperature_ranges_and_unbounded_gap(self):
        self.assertEqual(gap.temperature_range('27°C'),(27,27))
        self.assertEqual(gap.temperature_range('27°C or below'),(None,27))
        self.assertEqual(gap.temperature_range('29°C or higher'),(29,None))
        a,b=gap.temperature_range('80-81°F');self.assertAlmostEqual(a,26.6666667);self.assertAlmostEqual(b,27.2222222)
        self.assertEqual(gap.temperature_range('-10--8°C'),(-10,-8))
        self.assertEqual(gap.range_gap((None,27),(29,None)),2)
        self.assertEqual(gap.range_gap((None,27),(26,30)),0)
        for label in ['27', 'between 27 and 28°C', 'Market 27°C on 2026','1°C - 2°F','99 kittens']:
            self.assertIsNone(gap.temperature_range(label))
    def test_market_labels_mapping_and_active_candidates(self):
        event=sample();rows,skips=gap.prepare([event],settings())
        self.assertEqual([r['label'] for r in rows],['29°C or higher','28°C'])
        self.assertEqual([r['token'] for r in rows],['105','103'])
        self.assertEqual(rows[0]['gap'],2);self.assertEqual(rows[0]['leader']['yes_price'],.98)
        both,_=gap.prepare([event],settings(side='both',gap=2));self.assertEqual([r['side'] for r in both],['no','yes'])
        fahrenheit,_=gap.prepare([event],settings(gap=1.8,gap_units='F'));self.assertEqual(len(fahrenheit),2)
        self.assertFalse(gap.prepare([event],settings(leader_min=99))[0])
        event['opportunity_markets'][1]['accepting']=False
        self.assertEqual(len(gap.prepare([event],settings())[0]),1)
        event['opportunity_markets'][1]['yes_price']=.98
        self.assertEqual(len(gap.prepare([event],settings())[0]),0) # tied leaders are not guessed
    def test_yes_no_bids_asks_and_missing_book(self):
        candidates,_=gap.prepare([sample()],settings(side='both'))
        books={c['token']:book('0.95','0.99') for c in candidates};books[candidates[1]['token']]=book('0.95','0.97')
        rows,unavailable=gap.matched_rows(candidates,books,settings(side='both'))
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['ask_entry']);self.assertEqual(unavailable,0)
        rows,_=gap.matched_rows(candidates,books,settings(side='both',quote='either'))
        self.assertEqual(len(rows),4);self.assertTrue(any(not r['ask_entry'] for r in rows))
        rows,_=gap.matched_rows(candidates,books,settings(side='both',quote='both'))
        self.assertEqual(len(rows),1)
        del books[candidates[0]['token']];_,unavailable=gap.matched_rows(candidates,books,settings(side='both'))
        self.assertEqual(unavailable,1)
    def test_unknown_or_truncated_leader_is_never_invented(self):
        original={'markets':[market('27°C',.98,1,2),{'active':True,'acceptingOrders':True,'outcomes':['Yes','No']}]}
        _,uncertain=gap.scan_markets(original);self.assertTrue(uncertain)
        e=sample();e['opportunity_truncated']=True
        rows,info=gap.prepare([e],settings());self.assertFalse(rows);self.assertEqual(info['truncated_events'],1)
    def test_observed_uses_local_day_saved_metar_not_bot_history(self):
        e=sample();now=time.time();sid=web.db.add_station('London','','','EGLL','metar')
        with web.db.get_db() as c:
            c.execute('INSERT INTO web_metar_archive(icao,obs_time,temp) VALUES(?,?,?)',('EGLL',int(now)-60,25))
            c.execute('INSERT INTO web_metar_history(station_id,obs_time,temp) VALUES(?,?,?)',(sid,int(now)-30,26))
        rows,info=gap.prepare([e],settings(reference='observed'),now=now)
        self.assertEqual(rows[0]['reference']['temp'],26);self.assertEqual(rows[0]['reference']['extreme'],'max')
        self.assertEqual(rows[0]['reference']['kind'],'observed');self.assertEqual(rows[0]['reference']['count'],2)
        self.assertTrue(any(r['label']=='27°C' for r in rows)) # leader itself may be distant from observed maximum
        self.assertIsNone(gap.observed_extreme(e,now=now+7201))
    def test_observed_local_day_uses_dst_boundaries(self):
        event={'station_code':'KJFK','timezone':'America/New_York','date':'2026-11-01','slug':'highest-temperature-in-new-york-on-november-1-2026'}
        stamp=lambda s:int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())
        values=[('2026-11-01T03:59:00',50),('2026-11-01T04:01:00',10),('2026-11-01T05:30:00',15),('2026-11-01T06:30:00',16),('2026-11-02T04:59:00',14),('2026-11-02T05:01:00',60)]
        with web.db.get_db() as c:c.executemany('INSERT INTO web_metar_archive(icao,obs_time,temp) VALUES(?,?,?)',[('KJFK',stamp(t),v) for t,v in values])
        result=gap.observed_extreme(event,now=stamp('2026-11-02T06:00:00'))
        self.assertEqual(result['temp'],16);self.assertEqual(result['count'],4)
    def test_missing_token_is_counted_and_never_guessed(self):
        event=sample();event['opportunity_markets'][1]['tokens']=None
        rows,skipped=gap.prepare([event],settings())
        self.assertEqual(skipped['no_tokens'],1);self.assertEqual(len(rows),1)
    def test_public_batch_is_bounded_does_not_trade_and_handles_errors(self):
        data=[{'asset_id':'1','bids':[{'price':'0.95','size':'2'}],'asks':[{'price':'0.97','size':'4'}]}]
        r=Mock();r.iter_content.return_value=[json.dumps(data).encode()]
        with patch.object(gap.requests,'post',return_value=r) as post:
            out=gap.books_for(['1','2'])
        self.assertEqual(out['1']['asks'][0]['price'],'0.97');self.assertIsNone(out['2']);self.assertEqual(post.call_count,1)
        self.assertEqual(post.call_args.args[0],'https://clob.polymarket.com/books');self.assertFalse(post.call_args.kwargs['allow_redirects']);r.close.assert_called_once()
        r=Mock();r.raise_for_status.side_effect=gap.requests.HTTPError()
        with patch.object(gap.requests,'post',return_value=r) as post:
            self.assertIsNone(gap.books_for(['1'])['1']);self.assertEqual(post.call_count,1)
    def test_api_auth_pagination_limits_and_no_automatic_order(self):
        web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='tester',WEB_PASSWORD_HASH='hash')
        client=web.app.test_client();self.assertEqual(client.get('/api/temperature-opportunities').status_code,401)
        with client.session_transaction() as sess:sess.update(auth_user='tester',auth_version=web.auth_version(),csrf='csrf')
        events=[sample(i) for i in range(51)]
        with patch.object(web,'temperature_scan_data',return_value={'events':events,'incomplete':True,'scanned_at':time.time()}),patch.object(gap,'books_for',side_effect=lambda ids:{t:book() for t in ids}) as books:
            bad=client.get('/api/temperature-opportunities?price=nan');self.assertEqual(bad.status_code,400)
            res=client.get('/api/temperature-opportunities?side=no&price=97&gap=1&leader_min=97&reference=leader&quote=ask&gap_units=C')
            self.assertEqual(res.status_code,200);data=res.get_json();self.assertEqual(data['checked'],100);self.assertEqual(data['total_candidates'],102)
            self.assertTrue(data['catalog_incomplete']);self.assertEqual(len(data['rows']),100);self.assertEqual(len(books.call_args.args[0]),100)
            page=client.get('/api/temperature-opportunities',query_string={'session':data['session'],'offset':data['next_offset']}).get_json()
            self.assertEqual(page['checked'],2);self.assertIsNone(page['next_offset']);self.assertEqual(books.call_count,2)
            self.assertEqual(client.get('/api/temperature-opportunities',query_string={'session':data['session'],'offset':1}).status_code,400)
    def test_stale_gamma_candidates_not_sent_to_clob(self):
        e=sample();e['quotes_at']=time.time()-121
        web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='tester',WEB_PASSWORD_HASH='hash')
        c=web.app.test_client()
        with c.session_transaction() as s:s.update(auth_user='tester',auth_version=web.auth_version(),csrf='csrf')
        with patch.object(web,'temperature_scan_data',return_value={'events':[e],'incomplete':False,'scanned_at':time.time()}),patch.object(gap,'books_for',return_value={}) as books:
            result=c.get('/api/temperature-opportunities').get_json()
        self.assertEqual(result['stale_leader'],2);self.assertFalse(result['rows']);self.assertEqual(books.call_args.args[0],[])
