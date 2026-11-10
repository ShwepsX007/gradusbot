import os, tempfile, unittest
from unittest.mock import patch
import database as db
from web import book_history as h
from web import app as web


class BookHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_FILE;db.DB_FILE=os.path.join(self.tmp.name,'test.db')
        db.init_db();web.init_web_db();self.now=1700000100
    def tearDown(self):db.DB_FILE=self.old;self.tmp.cleanup()
    def payload(self,t,bid='.4',ask='.6'):
        return {'pairs':[{'yes':'1','no':'2'}], 'books':{'1':{'bids':[] if bid is None else [{'price':bid}], 'asks':[] if ask is None else [{'price':ask}], 'fetched_at':t}, '2':None}}
    def test_actual_samples_duplicates_null_sides_and_period(self):
        t=self.now
        h.record(self.payload(t),t);h.record(self.payload(t),t)
        self.assertEqual(h.read('1','1h',t),[{'t':float(t),'bid':.4,'ask':.6}])
        h.record(self.payload(t+31,bid=None,ask='.8'),t+31)
        h.record(self.payload(t+62,bid='.9',ask=None),t+62)
        rows=h.read('1','1h',t+62);self.assertEqual(len(rows),3);self.assertIsNone(rows[1]['bid']);self.assertIsNone(rows[2]['ask'])
        self.assertEqual(h.read('2','1d',t),[]);self.assertEqual(h.read('1','1h',t+7200),[])
        # Same 30-second bucket only keeps the latest real observation.
        h.record(self.payload(t+63,bid='.7'),t+63);self.assertEqual(len(h.read('1','1d',t+63)),3);self.assertEqual(h.read('1','1d',t+63)[-1]['bid'],.7)
    def test_unconfirmed_stale_and_invalid_data_not_recorded(self):
        data=self.payload(self.now-121);data['books']['999']={'bids':[{'price':'.2'}],'asks':[],'fetched_at':self.now}
        h.record(data,self.now);self.assertEqual(h.read('1','max',self.now),[]);self.assertEqual(h.read('999','max',self.now),[])
        data=self.payload(self.now,'NaN','2');h.record(data,self.now);row=h.read('1','1d',self.now)[0];self.assertIsNone(row['bid']);self.assertIsNone(row['ask'])
    def test_retention_and_size_pruning(self):
        with db.get_db() as c:
            c.executemany('INSERT INTO web_book_history VALUES(?,?,?,?,?)', [('1',i,self.now-h.RETENTION-100+i,.1,.9) for i in range(5)])
        with patch.object(h,'MAX_ROWS',2):
            h.record(self.payload(self.now),self.now)
            h.record(self.payload(self.now+31),self.now+1000) # old snapshot: rejected
            h.record(self.payload(self.now+1000),self.now+1000)
        with db.get_db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM web_book_history').fetchone()[0],2)
    def test_books_write_failure_does_not_break_book_response(self):
        config=dict(web.app.config);web._books_cache.clear()
        try:
            web.app.config.update(WEB_AUTH_CONFIGURED=True,WEB_USERNAME='test',WEB_PASSWORD_HASH='hash')
            client=web.app.test_client()
            with client.session_transaction() as s:s['auth_user']='test';s['auth_version']=web.auth_version()
            with patch.object(web,'event_for_station',return_value={'title':'x'}),patch.object(web,'fetch_event_books',return_value=self.payload(self.now)),patch.object(h,'record',side_effect=RuntimeError('write failed')):
                self.assertEqual(client.get('/api/market/event/books').status_code,200)
        finally:web.app.config.clear();web.app.config.update(config);web._books_cache.clear()

if __name__=='__main__':unittest.main()
