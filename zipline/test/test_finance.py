"""Tests for the zipline.finance package"""
import datetime
import mock
import pytz
import zipline.host_settings
from unittest2 import TestCase
import zipline.test.factory as factory
import zipline.util as qutil
import zipline.db as db
import zipline.finance.risk as risk
import zipline.protocol as zp

from zipline.test.client import TestTradingClient
from zipline.test.dummy import ThreadPoolExecutorMixin
from zipline.sources import SpecificEquityTrades


class FinanceTestCase(ThreadPoolExecutorMixin, TestCase):
    
    def test_trade_protocol(self):
        trades = factory.create_trade_history(133,    
                                            [10.0,10.0,10.0,10.0], 
                                            [100,100,100,100], 
                                            datetime.datetime.strptime("02/15/2012","%m/%d/%Y"),
                                            datetime.timedelta(days=1))
        for trade in trades:
            msg = zp.TRADE_FRAME("fake_source", zp.namedict(trade))
            recovered_trade = zp.DATASOURCE_UNFRAME(msg)
            self.assertTrue(recovered_trade.type == "TRADE")
            self.assertTrue(recovered_trade.source_id == "fake_source")
            del(recovered_trade.__dict__['type'])
            del(recovered_trade.__dict__['source_id'])
            self.assertEqual(zp.namedict(trade), recovered_trade)
            
    def test_trade_feed_protocol(self):
        trades = factory.create_trade_history(133,    
                                            [10.0,10.0,10.0,10.0], 
                                            [100,100,100,100], 
                                            datetime.datetime.strptime("02/15/2012","%m/%d/%Y"),
                                            datetime.timedelta(days=1))
        for trade in trades:
            #simulate data source sending frame
            msg = zp.DATASOURCE_FRAME(zp.namedict(trade))
            #feed unpacking frame
            recovered_trade = zp.DATASOURCE_UNFRAME(msg)
            #feed sending frame
            feed_msg = zp.FEED_FRAME(recovered_trade)
            #transform unframing
            recovered_feed = zp.FEED_UNFRAME(feed_msg)
            #do a transform
            trans_msg = zp.TRANSFORM_FRAME('helloworld', 2345.6)
            #simulate passthrough transform -- passthrough shouldn't even unpack the msg, just resend.
            passthrough_msg = zp.TRANSFORM_FRAME('PASSTHROUGH', feed_msg)
            #merge unframes transform and passthrough
            trans_recovered = zp.TRANSFORM_UNFRAME(trans_msg)
            pt_recovered = zp.TRANSFORM_UNFRAME(passthrough_msg)
            #simulated merge
            pt_recovered.PASSTHROUGH.merge(trans_recovered)
            #frame the merged event
            merged_msg = zp.MERGE_FRAME(pt_recovered.PASSTHROUGH)
            #unframe the merge and validate values
            event = zp.MERGE_UNFRAME(merged_msg)
    
    def test_trading_calendar(self):
        known_trading_day = datetime.datetime.strptime("02/24/2012","%m/%d/%Y")
        known_holiday     = datetime.datetime.strptime("02/20/2012", "%m/%d/%Y") #president's day
        saturday          = datetime.datetime.strptime("02/25/2012", "%m/%d/%Y")
        self.assertTrue(risk.trading_calendar.is_trading_day(known_trading_day))
        self.assertFalse(risk.trading_calendar.is_trading_day(known_holiday))
        self.assertFalse(risk.trading_calendar.is_trading_day(saturday))
    
    def test_orders(self):

        # Just verify sending and receiving orders.
        # --------------

        # Allocate sockets for the simulator components
        sockets = self.allocate_sockets(6)

        addresses = {
            'sync_address'   : sockets[0],
            'data_address'   : sockets[1],
            'feed_address'   : sockets[2],
            'merge_address'  : sockets[3],
            'result_address' : sockets[4],
            'order_address'  : sockets[5]
        }

        sim = self.get_simulator(addresses)
        con = self.get_controller()

        # Simulation Components
        # ---------------------

        set1 = SpecificEquityTrades("flat-133",factory.create_trade_history(133,    
                                                                            [10.0,10.0,10.0,10.0], 
                                                                            [100,100,100,100], 
                                                                            datetime.datetime.strptime("02/15/2012","%m/%d/%Y"),
                                                                            datetime.timedelta(days=1)))
        client = TestTradingClient()

        sim.register_components([set1, client])
        sim.register_controller( con )

        # Simulation
        # ----------
        sim.simulate()

        # Stop Running
        # ------------

        # TODO: less abrupt later, just shove a StopIteration
        # down the pipe to make it stop spinning
        sim.cuc._Thread__stop()

        self.assertEqual(sim.feed.pending_messages(), 0,
            "The feed should be drained of all messages, found {n} remaining."
            .format(n=sim.feed.pending_messages())
        )
        