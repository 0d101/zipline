import logging
from collections import Counter

from zipline.core.component import Component
from zipline.components.aggregator import Aggregate
import zipline.protocol as zp

from zipline.protocol import CONTROL_PROTOCOL, COMPONENT_TYPE, \
    CONTROL_FRAME, CONTROL_UNFRAME

LOGGER = logging.getLogger('ZiplineLogger')

class Feed(Aggregate):
    """
    Connects to N PULL sockets, publishing all messages received to a
    PUB socket. Published messages are guaranteed to be in chronological
    order based on message property dt. Expects to be instantiated in
    one execution context (thread, process, etc) and run in another.
    """

    def init(self):
        self.sent_count             = 0
        self.received_count         = 0
        self.draining               = False
        self.ds_finished_counter    = 0

        # Depending on the size of this, might want to use a data
        # structure with better asymptotics.
        self.data_buffer            = {}

        # source_id -> integer count
        self.sent_counters          = Counter()
        self.recv_counters          = Counter()

    @property
    def get_id(self):
        return "FEED"

    # -------
    # Sockets
    # -------

    def open(self):
        self.pull_socket = self.bind_data()
        self.feed_socket = self.bind_feed()

    # -------------
    # Core Methods
    # -------------

    def unframe(self, msg):
        return zp.DATASOURCE_UNFRAME(msg)

    def frame(self, event):
        return zp.FEED_FRAME(event)

    # -------------
    # Flow Control
    # -------------

    def drain(self):
        """
        Send all messages in the buffer.
        """
        self.draining = True
        while self.pending_messages() > 0:
            self.send_next()

    def send_next(self):
        """
        Send the (chronologically) next message in the buffer.
        """
        if not (self.is_full() or self.draining):
            return

        event = self.next()
        if(event != None):
            self.feed_socket.send(self.frame(event), self.zmq.NOBLOCK)
            self.sent_counters[event.source_id] += 1
            self.sent_count += 1

    def append(self, event):
        """
        Add an event to the buffer for the source specified by
        source_id.
        """
        self.data_buffer[event.source_id].append(event)
        self.recv_counters[event.source_id] += 1
        self.received_count += 1

    def next(self):
        """
        Get the next message in chronological order.
        """
        if not(self.is_full() or self.draining):
            return

        cur_source = None
        earliest_source = None
        earliest_event = None
        #iterate over the queues of events from all sources
        #(1 queue per datasource)
        for events in self.data_buffer.values():
            if len(events) == 0:
                continue
            cur_source = events
            first_in_list = events[0]
            if first_in_list.dt == None:
                #this is a filler event, discard
                events.pop(0)
                continue

            if (earliest_event == None) or (first_in_list.dt <= earliest_event.dt):
                earliest_event = first_in_list
                earliest_source = cur_source

        if earliest_event != None:
            return earliest_source.pop(0)
