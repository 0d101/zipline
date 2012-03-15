"""Tests for the zipline.finance package"""
import mock
import pytz

from unittest2 import TestCase
from datetime import datetime, timedelta
from collections import defaultdict

from nose.tools import timed

import zipline.test.factory as factory
import zipline.util as qutil
import zipline.finance.risk as risk
import zipline.protocol as zp
import zipline.finance.performance as perf

from zipline.test.client import TestAlgorithm
from zipline.sources import SpecificEquityTrades
from zipline.finance.trading import TransactionSimulator, OrderDataSource, \
TradeSimulationClient
from zipline.simulator import AddressAllocator, Simulator
from zipline.monitor import Controller

DEFAULT_TIMEOUT = 5 # seconds

allocator = AddressAllocator(1000)

class FinanceTestCase(TestCase):

    leased_sockets = defaultdict(list)

    def setUp(self):
        qutil.configure_logging()
        self.benchmark_returns, self.treasury_curves = \
        factory.load_market_data()

        self.trading_environment = risk.TradingEnvironment(
            self.benchmark_returns,
            self.treasury_curves
        )

        self.allocator = allocator

    def allocate_sockets(self, n):
        """
        Allocate sockets local to this test case, track them so
        we can gc after test run.
        """

        assert isinstance(n, int)
        assert n > 0

        leased = self.allocator.lease(n)

        self.leased_sockets[self.id()].extend(leased)
        return leased

    @timed(DEFAULT_TIMEOUT)
    def test_trade_feed_protocol(self):

        # TODO: Perhaps something more self-documenting for variables names?
        sid = 133
        price = [10.0] * 4
        volume = [100] * 4

        start_date = datetime.strptime("02/15/2012","%m/%d/%Y")
        one_day_td = timedelta(days=1)

        trades = factory.create_trade_history(
            sid, 
            price, 
            volume, 
            start_date, 
            one_day_td, 
            self.trading_environment
        )

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
            #simulate passthrough transform -- passthrough shouldn't even
            # unpack the msg, just resend.

            passthrough_msg = zp.TRANSFORM_FRAME(zp.TRANSFORM_TYPE.PASSTHROUGH,\
                    feed_msg)

            #merge unframes transform and passthrough
            trans_recovered = zp.TRANSFORM_UNFRAME(trans_msg)
            pt_recovered = zp.TRANSFORM_UNFRAME(passthrough_msg)
            #simulated merge
            pt_recovered.PASSTHROUGH.merge(trans_recovered)
            #frame the merged event
            merged_msg = zp.MERGE_FRAME(pt_recovered.PASSTHROUGH)
            #unframe the merge and validate values
            event = zp.MERGE_UNFRAME(merged_msg)

            #check the transformed value, should only be in event, not trade.
            self.assertTrue(event.helloworld == 2345.6)
            event.delete('helloworld')

            self.assertEqual(zp.namedict(trade), event)

    @timed(DEFAULT_TIMEOUT)
    def test_order_protocol(self):
        #client places an order
        now = datetime.utcnow().replace(tzinfo=pytz.utc)
        order = zp.namedict({
            'dt':now,
            'sid':133,
            'amount':100
        })
        order_msg = zp.ORDER_FRAME(order)

        #order datasource receives
        order = zp.ORDER_UNFRAME(order_msg)
        self.assertEqual(order.sid, 133)
        self.assertEqual(order.amount, 100)
        self.assertEqual(order.dt, now)
        
        #order datasource datasource frames the order
        order_event = zp.namedict({
            "sid"        : order.sid,
            "amount"     : order.amount,
            "dt"         : order.dt,
            "source_id"  : zp.FINANCE_COMPONENT.ORDER_SOURCE,
            "type"       : zp.DATASOURCE_TYPE.ORDER
        })


        order_ds_msg = zp.DATASOURCE_FRAME(order_event)

        #transaction transform unframes
        recovered_order = zp.DATASOURCE_UNFRAME(order_ds_msg)

        self.assertEqual(now, recovered_order.dt)

        #create a transaction from the order
        txn = zp.namedict({
            'sid'        : recovered_order.sid,
            'amount'     : recovered_order.amount,
            'dt'         : recovered_order.dt,
            'price'      : 10.0,
            'commission' : 0.50
        })

        #frame that transaction
        txn_msg = zp.TRANSFORM_FRAME(zp.TRANSFORM_TYPE.TRANSACTION, txn)

        #unframe
        recovered_tx = zp.TRANSFORM_UNFRAME(txn_msg).TRANSACTION
        self.assertEqual(recovered_tx.sid, 133)
        self.assertEqual(recovered_tx.amount, 100)

    @timed(DEFAULT_TIMEOUT)
    def test_orders(self):

        # Just verify sending and receiving orders.
        # --------------

        # Allocate sockets for the simulator components
        sockets = self.allocate_sockets(8)

        addresses = {
            'sync_address'   : sockets[0],
            'data_address'   : sockets[1],
            'feed_address'   : sockets[2],
            'merge_address'  : sockets[3],
            'result_address' : sockets[4],
            'order_address'  : sockets[5]
        }

        con = Controller(
            sockets[6],
            sockets[7],
            logging = qutil.LOGGER
        )

        sim = Simulator(addresses)

        # Simulation Components
        # ---------------------

        # TODO: Perhaps something more self-documenting for variables names?
        sid = 133
        price = [10.1] * 16
        volume = [100] * 16
        start_date = datetime.strptime("02/1/2012","%m/%d/%Y")
        start_date = start_date.replace(tzinfo=pytz.utc)
        trade_time_increment = timedelta(days=1)

        trade_history = factory.create_trade_history( 
            sid, 
            price, 
            volume, 
            start_date, 
            trade_time_increment, 
            self.trading_environment 
        )

        set1 = SpecificEquityTrades("flat-133", trade_history)
        
        self.trading_environment.period_start = trade_history[0].dt
        self.trading_environment.period_end = trade_history[-1].dt
        self.trading_environment.capital_base = 10000
        
        trading_client = TradeSimulationClient(self.trading_environment)
        #client will send 10 orders for 100 shares of 133
        test_algo = TestAlgorithm(133, 100, 10, trading_client)

        order_source = OrderDataSource()
        transaction_sim = TransactionSimulator()

        sim.register_components([
            trading_client, 
            order_source, 
            transaction_sim, 
            set1
        ])
        sim.register_controller( con )

        # Simulation
        # ----------
        sim_context = sim.simulate()
        sim_context.join()

        self.assertTrue(sim.ready())
        self.assertFalse(sim.exception)

        # TODO: Make more assertions about the final state of the components.
        self.assertEqual(sim.feed.pending_messages(), 0, \
            "The feed should be drained of all messages, found {n} remaining." \
            .format(n=sim.feed.pending_messages()))


    @timed(DEFAULT_TIMEOUT)
    def test_performance(self): 

        # verify order -> transaction -> portfolio position.
        # --------------

        # Allocate sockets for the simulator components
        sockets = self.allocate_sockets(8)

        addresses = {
            'sync_address'   : sockets[0],
            'data_address'   : sockets[1],
            'feed_address'   : sockets[2],
            'merge_address'  : sockets[3],
            'result_address' : sockets[4],
            'order_address'  : sockets[5]
        }

        con = Controller(
            sockets[6],
            sockets[7],
            logging = qutil.LOGGER
        )

        sim = Simulator(addresses)

        # Simulation Components
        # ---------------------

        # TODO: Perhaps something more self-documenting for variables names?
        trade_count = 100
        sid = 133
        price = [10.1] * trade_count
        volume = [100] * trade_count
        start_date = datetime.strptime("02/1/2012","%m/%d/%Y")
        start_date = start_date.replace(tzinfo=pytz.utc)
        trade_time_increment = timedelta(days=1)

        trade_history = factory.create_trade_history( 
            sid, 
            price, 
            volume, 
            start_date, 
            trade_time_increment, 
            self.trading_environment 
        )
        
        
        self.trading_environment.period_start = trade_history[0].dt
        self.trading_environment.period_end = trade_history[-1].dt
        self.trading_environment.capital_base = 10000
        
        set1 = SpecificEquityTrades("flat-133", trade_history)

        #client sill send 10 orders for 100 shares of 133
        trading_client = TradeSimulationClient(self.trading_environment)
        test_algo = TestAlgorithm(133, 100, 10, trading_client)

        order_source = OrderDataSource()
        transaction_sim = TransactionSimulator()
        
        sim.register_components([
            trading_client, 
            order_source, 
            transaction_sim, 
            set1, 
            ])
        sim.register_controller( con )

        # Simulation
        # ----------
        sim_context = sim.simulate()
        sim_context.join()

        self.assertEqual(
            sim.feed.pending_messages(), 
            0, 
            "The feed should be drained of all messages, found {n} remaining." \
            .format(n=sim.feed.pending_messages())
        )
        
        self.assertEqual(
            sim.merge.pending_messages(), 
            0, 
            "The merge should be drained of all messages, found {n} remaining." \
            .format(n=sim.merge.pending_messages())
        )

        self.assertEqual(
            test_algo.count,
            test_algo.incr,
            "The test algorithm should send as many orders as specified.")
            
        self.assertEqual(
            order_source.sent_count, 
            test_algo.count, 
            "The order source should have sent as many orders as the algo."
        )
            
        self.assertEqual(
            transaction_sim.txn_count,
            trading_client.perf.txn_count,
            "The perf tracker should handle the same number of transactions \
            as the simulator emits."
        ) 
        
        self.assertEqual(
            len(trading_client.perf.cumulative_performance.positions), 
            1, 
            "Portfolio should have one position."
        )
        
        self.assertEqual(
            trading_client.perf.cumulative_performance.positions[133].sid, 
            133, 
            "Portfolio should have one position in 133."
        )
