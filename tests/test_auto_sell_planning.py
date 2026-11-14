import unittest
from web.auto_sell import decide


def buy(size='10',cost='9',matched=None,failed='0',status='LIVE'):
    return {'assessment':'active','order_status':status,'confirmed_size':size,'confirmed_notional':cost,
            'size_matched':matched or size,'failed_size':failed}

def sell(size='10',price='.95',matched='0',confirmed='0',failed='0',status='LIVE',assessment='active',cancel='none',state='accepted'):
    return {'size':size,'price':price,'state':state,'cancel_state':cancel,
            'evidence':{'assessment':assessment,'order_status':status,'size_matched':matched,'confirmed_size':confirmed,'failed_size':failed}}

def plan(b=None,s=None,sold='0',offset='.05',tick='.01',minimum='5',enabled=True):
    return decide(buy=b or buy(),current_sell=s,prior_sold=sold,offset=offset,tick=tick,minimum=minimum,enabled=enabled)

class AutoSellPlanningTests(unittest.TestCase):
    def test_submit_and_average_round_up(self):
        self.assertEqual(plan()['action'],'submit');self.assertEqual(plan()['price'],'0.95')
        self.assertEqual(plan(b=buy('6','5.44'),minimum='1')['price'],'0.96')
    def test_rounding_never_puts_target_below_exact_average(self):
        result=plan(b=buy('1','0.900000000000000001'),minimum='1')
        self.assertEqual(result['price'],'0.96')
        self.assertEqual(plan(b=buy('1','1e-999999'),minimum='1')['action'],'halt')
    def test_minimum_size_and_upper_tick(self):
        self.assertEqual(plan(b=buy('4.99','4.491'))['action'],'wait')
        self.assertEqual(plan(b=buy('10','9.6'))['action'],'halt')
        self.assertEqual(plan(b=buy('0','0'))['action'],'wait')
    def test_no_unconfirmed_shares(self):
        r=plan(b=buy('4','3.6',matched='10'),minimum='1');self.assertEqual((r['action'],r['size']),('submit','4'))
    def test_new_partial_fill_cancels_only_once(self):
        self.assertEqual(plan(b=buy('10','8.8'),s=sell(size='5',price='.95'))['action'],'cancel')
        self.assertEqual(plan(b=buy('10','8.8'),s=sell(size='5',price='.95',cancel='unknown'))['action'],'wait')
        self.assertEqual(plan(b=buy('10','8.8'),s=sell(size='5',price='.95',cancel='requested'))['action'],'wait')
    def test_sell_partially_filled_cancelled_then_new_quantity(self):
        s=sell(size='10',matched='3',confirmed='3',status='CANCELED',assessment='terminal',cancel='requested')
        self.assertEqual(plan(s=s)['sold'],'3')
        self.assertEqual(plan(sold='3')['size'],'7')
    def test_manual_cancellation_does_not_recreate(self):
        self.assertEqual(plan(s=sell(status='CANCELED',assessment='terminal'))['action'],'halt')
    def test_unknown_post_or_trade_data_never_sends_again(self):
        self.assertEqual(plan(s=sell(state='unknown'))['action'],'wait')
        self.assertEqual(plan(s=sell(state='pending'))['action'],'wait')
        self.assertEqual(plan(s=sell(matched='5',confirmed='3',status='CANCELED',assessment='terminal',cancel='requested'))['action'],'halt')
        self.assertEqual(plan(b=buy('2','1',matched='1'))['action'],'halt')
        self.assertEqual(plan(sold='11')['action'],'halt')
    def test_disabled_keeps_sale_and_stops_side_effects(self):
        self.assertEqual(plan(s=sell(),enabled=False)['action'],'paused')
    def test_unchanged_open_order(self):
        self.assertEqual(plan(s=sell())['action'],'wait')
        self.assertEqual(plan(s=sell(matched='2',confirmed='2'))['action'],'wait')

if __name__=='__main__':unittest.main()
