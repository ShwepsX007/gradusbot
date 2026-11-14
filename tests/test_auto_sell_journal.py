import os
import tempfile
import threading
import unittest
from decimal import Decimal
import database as db
from web import auto_sell as a

B='a'*32
W='0x'+'2'*40

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_FILE;db.DB_FILE=os.path.join(self.tmp.name,'test.db')
        a.init_journal();a.add_plan(B,W,'101','.05')
    def tearDown(self):
        db.DB_FILE=self.old;self.tmp.cleanup()
    def state(self):
        with db.get_db() as c:return [dict(r) for r in c.execute('SELECT * FROM web_auto_sell_actions').fetchall()]
    def test_persist_before_submission_and_no_blind_retry_after_crash(self):
        a.reserve_action(B,'b'*32,{'action':'submit','price':'.95','size':'5'})
        self.assertEqual(self.state()[0]['state'],'pending')
        a.recover_interrupted_actions();self.assertEqual(self.state()[0]['state'],'unknown')
        with self.assertRaises(a.EvidenceError):a.reserve_action(B,'c'*32,{'action':'submit','price':'.95','size':'5'})
        a.record_resolution('b'*32,'proved')
        a.reserve_action(B,'c'*32,{'action':'cancel'},'0x'+'3'*64)
        self.assertEqual(self.state()[1]['kind'],'cancel')
    def test_cancel_unknown_cannot_be_reissued(self):
        a.reserve_action(B,'b'*32,{'action':'cancel'},'0x'+'3'*64)
        a.record_resolution('b'*32,'unknown')
        with self.assertRaises(a.EvidenceError):a.reserve_action(B,'c'*32,{'action':'cancel'},'0x'+'3'*64)
        with self.assertRaises(a.EvidenceError):a.record_resolution('b'*32,'not_sent')
    def test_atomic_concurrent_reservation(self):
        results=[];barrier=threading.Barrier(2)
        def claim(n):
            barrier.wait()
            try:a.reserve_action(B,n*32,{'action':'submit','price':'.95','size':'5'});results.append('ok')
            except a.EvidenceError:results.append('blocked')
        ts=[threading.Thread(target=claim,args=(n,)) for n in ('b','c')]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertCountEqual(results,['ok','blocked']);self.assertEqual(len(self.state()),1)
    def test_malformed_decisions_never_claim(self):
        for decision in ({'action':'submit','price':'1','size':'5'},{'action':'cancel'},{'action':'wait'}):
            with self.assertRaises(a.EvidenceError):a.reserve_action(B,'b'*32,decision)
        self.assertEqual(self.state(),[])
    def test_retirement_is_idempotent_and_requires_terminal_proven_sell(self):
        a.reserve_action(B,'b'*32,{'action':'submit','price':'.95','size':'10'})
        evidence={'assessment':'terminal','size_matched':'3','confirmed_size':'3','failed_size':'0'}
        with self.assertRaises(a.EvidenceError):a.retire_sale('b'*32,evidence)
        a.record_resolution('b'*32,'proved')
        with self.assertRaises(a.EvidenceError):a.retire_sale('b'*32,{**evidence,'assessment':'active'})
        with self.assertRaises(a.EvidenceError):a.retire_sale('b'*32,{**evidence,'confirmed_size':'2'})
        a.retire_sale('b'*32,evidence);a.retire_sale('b'*32,evidence)
        self.assertEqual(a.confirmed_sold(B),Decimal('3'))
        with self.assertRaises(a.EvidenceError):a.retire_sale('b'*32,{**evidence,'size_matched':'4','confirmed_size':'4'})
        self.assertEqual(a.confirmed_sold(B),Decimal('3'))
    def test_simulated_partial_reprice_cancel_settle_and_lost_response(self):
        def buy(n,c): return {'assessment':'active','size_matched':n,'confirmed_size':n,'failed_size':'0','confirmed_notional':c}
        initial=a.decide(buy=buy('5','4.5'),prior_sold='0',current_sell=None,offset='.05',tick='.01',minimum='1')
        a.reserve_action(B,'1'*32,initial);a.record_resolution('1'*32,'proved')
        observed={'assessment':'active','order_status':'LIVE','size_matched':'2','confirmed_size':'2','failed_size':'0'}
        child={'state':'accepted','size':'5','price':'.95','cancel_state':'none','evidence':observed}
        changed=a.decide(buy=buy('10','8.8'),prior_sold='0',current_sell=child,offset='.05',tick='.01',minimum='1')
        self.assertEqual(changed['action'],'cancel')
        a.reserve_action(B,'2'*32,changed,'0x'+'9'*64);a.record_resolution('2'*32,'unknown')
        child['cancel_state']='unknown'
        self.assertEqual(a.decide(buy=buy('10','8.8'),prior_sold='0',current_sell=child,offset='.05',tick='.01',minimum='1')['action'],'wait')
        # Another share filled while the cancel was in flight; only confirmed
        # cancellation and settlement permit a replacement for the remaining 7.
        terminal={'assessment':'terminal','order_status':'CANCELED','size_matched':'3','confirmed_size':'3','failed_size':'0'}
        child['evidence']=terminal
        self.assertEqual(a.decide(buy=buy('10','8.8'),prior_sold='0',current_sell=child,offset='.05',tick='.01',minimum='1')['action'],'retire')
        a.record_resolution('2'*32,'proved');a.retire_sale('1'*32,terminal)
        replacement=a.decide(buy=buy('10','8.8'),prior_sold=a.confirmed_sold(B),current_sell=None,offset='.05',tick='.01',minimum='1')
        self.assertEqual((replacement['action'],replacement['price'],replacement['size']),('submit','0.93','7'))
        a.reserve_action(B,'3'*32,replacement);a.recover_interrupted_actions()
        with self.assertRaises(a.EvidenceError):a.reserve_action(B,'4'*32,replacement)

    def test_cannot_modify_resolved_action(self):
        a.reserve_action(B,'b'*32,{'action':'submit','price':'.95','size':'5'})
        a.record_resolution('b'*32,'not_sent')
        with self.assertRaises(a.EvidenceError):a.record_resolution('b'*32,'unknown')

if __name__=='__main__':unittest.main()
