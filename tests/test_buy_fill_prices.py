"""Read-only evidence for future automation. No tests send orders."""
import unittest
from decimal import Decimal
from unittest.mock import Mock
from web import order_reconciliation as recon

ORDER='0x'+'1'*64
WALLET='0x'+'2'*40
CREDENTIALS={'api_key':'test-key'}
ROW={'wallet':WALLET,'asset':'12','side':'BUY','execution_mode':'limit',
     'price':'0.95','size':'10','order_type':'GTC'}


def order(ids, matched):
    return {'id':ORDER,'asset_id':'12','side':'BUY','maker_address':WALLET,'owner':'test-key',
            'original_size':'10','size_matched':matched,'price':'0.95','status':'LIVE',
            'associate_trades':ids}


def taker(trade_id, quantity, price, state='CONFIRMED'):
    return {'id':trade_id,'taker_order_id':ORDER,'asset_id':'12','side':'BUY','owner':'test-key',
            'size':quantity,'price':price,'status':state}


def maker(trade_id, quantity, price, state='CONFIRMED'):
    return {'id':trade_id,'taker_order_id':'0x'+'3'*64,'asset_id':'other', 'price':'0.10','status':state,
            'maker_orders':[{'order_id':ORDER,'asset_id':'12','side':'BUY','owner':'test-key',
                             'maker_address':WALLET,'matched_amount':quantity,'price':price}]}


class BuyFillPriceTests(unittest.TestCase):
    def setUp(self):
        self.client=Mock()
        self.trades={'a':taker('a','2','0.90'), 'b':maker('b','3','0.80'),
                     'c':taker('c','1','0.70','MINED')}
        self.client.get_order.return_value=order(['a','b','c'],'6')
        self.client.get_trades.side_effect=lambda p,only_first_page: [self.trades[p.id]]
    def inspect(self):
        return recon.inspect_order(self.client,ROW,CREDENTIALS,ORDER,include_buy_prices=True)
    def test_weighted_confirmed_partial_maker_taker_and_unsettled(self):
        result=self.inspect()
        self.assertEqual(result['assessment'],'active')
        self.assertEqual(Decimal(result['confirmed_size']),Decimal('5'))
        self.assertEqual(Decimal(result['confirmed_notional']),Decimal('4.2'))
        self.assertEqual(Decimal(result['confirmed_notional'])/Decimal(result['confirmed_size']),Decimal('.84'))
        self.client.post_order.assert_not_called();self.client.cancel_order.assert_not_called()
    def test_missing_or_invalid_confirmed_price_fails_closed(self):
        for bad in (None, '', 'NaN', '-1', '0', '1', '0.96', '0.1234567'):
            with self.subTest(price=bad):
                self.trades['a']['price']=bad
                with self.assertRaises(ValueError): self.inspect()
        self.trades['a']['price']='0.9'
        del self.trades['b']['maker_orders'][0]['price']
        with self.assertRaises(ValueError): self.inspect()
    def test_failed_fill_does_not_count_as_bought(self):
        self.trades['c']['status']='FAILED'
        result=self.inspect();self.assertEqual(Decimal(result['failed_size']),1)
        self.assertEqual(Decimal(result['confirmed_notional']),Decimal('4.2'))
    def test_incomplete_or_duplicate_trades_fail_closed(self):
        self.client.get_order.return_value['associate_trades']=['a','a']
        with self.assertRaises(ValueError):self.inspect()
        self.client.get_order.return_value=order(['a','b'],'6')
        with self.assertRaises(ValueError):self.inspect()
    def test_default_reconciliation_does_not_require_prices(self):
        del self.trades['a']['price'];del self.trades['b']['maker_orders'][0]['price']
        evidence=recon.inspect_order(self.client,ROW,CREDENTIALS,ORDER)
        self.assertNotIn('confirmed_notional',evidence)
    def test_sell_price_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            recon.inspect_order(self.client,{**ROW,'side':'SELL'},CREDENTIALS,ORDER,include_buy_prices=True)
        self.client.get_order.assert_not_called()

if __name__=='__main__': unittest.main()
