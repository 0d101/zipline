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

from zipline.test.algorithms import TestAlgorithm
from zipline.sources import SpecificEquityTrades
from zipline.finance.trading import TransactionSimulator, OrderDataSource, \
TradeSimulationClient, TradingEnvironment
from zipline.simulator import AddressAllocator, Simulator
from zipline.monitor import Controller
from zipline.lines import SimulatedTrading
from zipline.protocol_utils import namedict

DEFAULT_TIMEOUT = 15 # seconds

allocator = AddressAllocator(1000)

class FinanceTestCase(TestCase):

    leased_sockets = defaultdict(list)

    def setUp(self):
        qutil.configure_logging()
        self.zipline_test_config = {
            'allocator':allocator,
            'sid':133
        }

    @timed(DEFAULT_TIMEOUT)
    def test_factory(self):
        trading_environment = factory.create_trading_environment()
        trade_source = factory.create_daily_trade_source(
            [133],
            200,
            trading_environment
        )
        prev = None
        for trade in trade_source.event_list:
            if prev:
                self.assertTrue(trade.dt > prev.dt)
            prev = trade
      
    @timed(DEFAULT_TIMEOUT)
    def test_trading_environment(self):
        benchmark_returns, treasury_curves = \
        factory.load_market_data()
        
        env = TradingEnvironment(
            benchmark_returns,
            treasury_curves,
            period_start = datetime(2008, 1, 1, tzinfo = pytz.utc),
            period_end = datetime(2008, 12, 31, tzinfo = pytz.utc),
            capital_base = 100000,
            max_drawdown = 0.50
        )      
        #holidays taken from: http://www.nyse.com/press/1191407641943.html
        new_years   = datetime(2008, 1, 1, tzinfo = pytz.utc)
        mlk_day     = datetime(2008, 1, 21, tzinfo = pytz.utc)
        presidents  = datetime(2008, 2, 18, tzinfo = pytz.utc)
        good_friday = datetime(2008, 3, 21, tzinfo = pytz.utc)
        memorial_day= datetime(2008, 5, 26, tzinfo = pytz.utc)
        july_4th    = datetime(2008, 7, 4, tzinfo = pytz.utc)
        labor_day   = datetime(2008, 9, 1, tzinfo = pytz.utc)
        tgiving     = datetime(2008, 11, 27, tzinfo = pytz.utc)
        christmas   = datetime(2008, 5, 25, tzinfo = pytz.utc)
        a_saturday  = datetime(2008, 8, 2, tzinfo = pytz.utc)
        a_sunday    = datetime(2008, 10, 12, tzinfo = pytz.utc)
        holidays = [
            new_years, 
            mlk_day, 
            presidents, 
            good_friday, 
            memorial_day, 
            july_4th, 
            labor_day, 
            tgiving, 
            christmas,
            a_saturday,
            a_sunday
        ]
        
        for holiday in holidays:
            self.assertTrue(not env.is_trading_day(holiday))
        
        first_trading_day = datetime(2008, 1, 2, tzinfo = pytz.utc)
        last_trading_day  = datetime(2008, 12, 31, tzinfo = pytz.utc)
        workdays = [first_trading_day, last_trading_day]
        
        for workday in workdays:
            self.assertTrue(env.is_trading_day(workday))
        
        self.assertTrue(env.last_close.month == 12)
        self.assertTrue(env.last_close.day == 31)
        
    @timed(DEFAULT_TIMEOUT)
    def test_orders(self):
        
        # Simulation
        # ----------
        zipline = SimulatedTrading.create_test_zipline(**self.zipline_test_config)
        zipline.simulate(blocking=True)

        self.assertTrue(zipline.sim.ready())
        self.assertFalse(zipline.sim.exception)

        # TODO: Make more assertions about the final state of the components.
        self.assertEqual(zipline.sim.feed.pending_messages(), 0, \
            "The feed should be drained of all messages, found {n} remaining." \
            .format(n=zipline.sim.feed.pending_messages()))


    @timed(DEFAULT_TIMEOUT)
    def test_performance(self): 
        #provide enough trades to ensure all orders are filled.
        self.zipline_test_config['order_count'] = 100
        self.zipline_test_config['trade_count'] = 200
        zipline = SimulatedTrading.create_test_zipline(**self.zipline_test_config)
        zipline.simulate(blocking=True)

        self.assertEqual(
            zipline.sim.feed.pending_messages(), 
            0, 
            "The feed should be drained of all messages, found {n} remaining." \
            .format(n=zipline.sim.feed.pending_messages())
        )
        
        self.assertEqual(
            zipline.sim.merge.pending_messages(), 
            0, 
            "The merge should be drained of all messages, found {n} remaining." \
            .format(n=zipline.sim.merge.pending_messages())
        )

        self.assertEqual(
            zipline.algorithm.count,
            zipline.algorithm.incr,
            "The test algorithm should send as many orders as specified.")
            
        order_source = zipline.sources[zp.FINANCE_COMPONENT.ORDER_SOURCE]
        self.assertEqual(
            order_source.sent_count, 
            zipline.algorithm.count, 
            "The order source should have sent as many orders as the algo."
        )
        
        transaction_sim = zipline.transforms[zp.TRANSFORM_TYPE.TRANSACTION]
        self.assertEqual(
            transaction_sim.txn_count,
            zipline.trading_client.perf.txn_count,
            "The perf tracker should handle the same number of transactions \
            as the simulator emits."
        ) 
        
        self.assertEqual(
            len(zipline.get_positions()), 
            1, 
            "Portfolio should have one position."
        )
        
        SID = self.zipline_test_config['sid']
        self.assertEqual(
            zipline.get_positions()[SID]['sid'], 
            SID, 
            "Portfolio should have one position in " + str(SID)
        )
        
        self.assertEqual(
            zipline.sources['flat'].count,
            self.zipline_test_config['trade_count'],
            "The simulated trade source should send all trades."
        )
        
        self.assertEqual(
            zipline.algorithm.frame_count,
            self.zipline_test_config['trade_count'],
            "The algorithm should receive all trades."
            )
    
    @timed(DEFAULT_TIMEOUT)  
    def test_sid_filter(self):
        """Ensure the algorithm's filter prevents events from arriving."""
        # create a test algorithm whose filter will not match any of the
        # trade events sourced inside the zipline.
        order_amount = 100
        order_count = 100
        no_match_sid = 222
        test_algo = TestAlgorithm(
            no_match_sid,
            order_amount,
            order_count
        )
        
        self.zipline_test_config['trade_count'] = 200
        self.zipline_test_config['algorithm'] = test_algo
        
        zipline = SimulatedTrading.create_test_zipline(
            **self.zipline_test_config
        )
       
        zipline.simulate(blocking=True)
        #check that the algorithm received no events
        self.assertEqual(
            0,
            test_algo.frame_count,
            "The algorithm should not receive any events due to filtering."
        )

    
    # TODO: write a test that proves orders expire without being filled.
    @timed(DEFAULT_TIMEOUT)
    def test_transaction_sim(self):
        
        # create a scenario where order size and trade size are equal
        # so that orders must be spread out over several trades.
        params ={
            'trade_count':360,
            'trade_amount':100,
            'trade_interval': timedelta(minutes=1),
            'order_count':2,
            'order_amount':100,
            'order_interval': timedelta(minutes=1),
            # because we placed an order for 100 shares, and the volume
            # of each trade is 100, the simulator should spread the order
            # into 4 trades of 25 shares per order.
            'expected_txn_count':8,
            'expected_txn_volume':2 * 100
        }
        
        self.transaction_sim(**params)
        
        # create a scenario where order.amount <<< trade.volume
        # to test that several orders can be covered properly by one trade.
        params2 ={
            'trade_count':6,
            'trade_amount':100,
            'trade_interval': timedelta(hours=1),
            'order_count':24,
            'order_amount':1,
            'order_interval': timedelta(minutes=1),
            # because we placed an orders totaling less than 25% of one trade
            # the simulator should produce just one transaction.
            'expected_txn_count':1,
            'expected_txn_volume':24 * 1
        }
        self.transaction_sim(**params2)
        
        # create a scenario where orders expire without being filled
        # entirely
        params3 = {
            'trade_count':100,
            'trade_amount':100,
            'trade_delay': timedelta(minutes=5),
            'trade_interval': timedelta(days=1),
            'order_count':3,
            'order_amount':1000,
            'order_interval': timedelta(minutes=30),
            # because we placed an orders totaling less than 25% of one trade
            # the simulator should produce just one transaction.
            'expected_txn_count' : 1,
            'expected_txn_volume' : 25
        }
        self.transaction_sim(**params3)
        
    def transaction_sim(self, **params):
        trade_count = params['trade_count']
        trade_amount = params['trade_amount']
        trade_interval = params['trade_interval']
        trade_delay = params.get('trade_delay')
        order_count = params['order_count']
        order_amount = params['order_amount']
        order_interval = params['order_interval']
        expected_txn_count = params['expected_txn_count']
        expected_txn_volume = params['expected_txn_volume']
        
        trading_environment = factory.create_trading_environment()
        trade_sim = TransactionSimulator()
        price = [10.1] * trade_count
        volume = [100] * trade_count
        start_date = trading_environment.first_open
        sid = 1

        generated_trades = factory.create_trade_history( 
            sid, 
            price, 
            volume, 
            trade_interval, 
            trading_environment 
        )
        
        for i in range(order_count):
            order = namedict(
            {
                'sid':sid,
                'amount':order_amount,
                'type':zp.DATASOURCE_TYPE.ORDER,
                'dt' : start_date + i * order_interval
            })

            sim_state = trade_sim.transform(order)
        
            # there should not be a new transaction from an order.
            self.assertTrue(sim_state['name'] == trade_sim.get_id)
            self.assertTrue(sim_state['value'] == None)
        
        # there should now be one open order list stored under the sid
        oo = trade_sim.open_orders
        self.assertEqual(len(oo), 1)
        self.assertTrue(oo.has_key(sid))
        order_list = oo[sid]
        self.assertEqual(order_count, len(order_list))
        
        for order in order_list:
            self.assertEqual(order.sid, sid)
            self.assertEqual(order.amount, order_amount)
        
        transactions = []
        for trade in generated_trades:
            if trade_delay:
                trade.dt = trade.dt + trade_delay
                
            sim_state = trade_sim.transform(trade)
            
            self.assertEqual(sim_state['name'], trade_sim.get_id)

            if sim_state['value']:
                transactions.append(sim_state['value'])         
            
                
        total_volume = 0
        for txn in transactions:
            total_volume += txn.amount
            
        self.assertEqual(total_volume, expected_txn_volume)    
        self.assertEqual(len(transactions), expected_txn_count)
        
        # the open orders should now be empty
        oo = trade_sim.open_orders
        self.assertTrue(oo.has_key(sid))
        order_list = oo[sid]
        self.assertEqual(0, len(order_list))
        
        
        