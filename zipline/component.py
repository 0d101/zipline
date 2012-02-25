"""
Commonly used messaging components.
"""
import os
import uuid
import socket
import humanhash

import zipline.util as qutil
from zipline.protocol import CONTROL_PROTOCOL

class Component(object):

    def __init__(self):
        """
        :addresses: a dict of name_string -> zmq port address strings. Must have the following entries::

            - sync_address: socket address used for synchronizing the start of all workers, heartbeating, and exit notification
                            will be used in REP/REQ sockets. Bind is always on the REP side.
            - data_address: socket address used for data sources to stream their records. 
                            will be used in PUSH/PULL sockets between data sources and a ParallelBuffer (aka the Feed). Bind
                            will always be on the PULL side (we always have N producers and 1 consumer)
            - feed_address: socket address used to publish consolidated feed from serialization of data sources
                            will be used in PUB/SUB sockets between Feed and Transforms. Bind is always on the PUB side.
            - merge_address: socket address used to publish transformed values.
                            will be used in PUSH/PULL from many transforms to one MergedParallelBuffer (aka the Merge). Bind
                            will always be on the PULL side (we always have N producers and 1 consumer)
            - result_address: socket address used to publish merged data source feed and transforms to clients
                            will be used in PUB/SUB from one Merge to one or many clients. Bind is always on the PUB side.

        Bind/Connect methods will return the correct socket type for each address. Any sockets on which recv is expected to be called
        will also return a Poller.

        """
        self.zmq               = None
        self.context           = None
        self.addresses         = None
        self.out_socket        = None
        self.gevent_needed     = False
        self.killed            = False
        self.heartbeat_timeout = 2000

        self.guid = uuid.uuid4()
        self.huid = humanhash.humanize(self.guid.hex)

    # ------------
    # Core Methods
    # ------------

    @property
    def get_id(self):
        raise NotImplementedError

    def open(self):
        raise NotImplementedError

    def destroy(self):
        """
        Tear down after normal operation.
        """
        raise NotImplementedError

    def kill(self):
        """
        Tear down ( fast ) as a mode of failure in the
        simulation.
        """
        raise NotImplementedError

    def do_work(self):
        raise NotImplementedError

    def _run(self):
        self.done       = False
        self.sockets    = []

        if self.gevent_needed:
            qutil.LOGGER.info("Loading gevent specific zmq for {id}".format(id=self.get_id))
            import gevent_zeromq
            self.zmq = gevent_zeromq.zmq
        else:
            import zmq
            self.zmq = zmq

        self.context = self.zmq.Context()
        self.setup_poller()

        self.open()
        self.setup_sync()
        self.setup_control()
        self.loop()

        #close all the sockets
        for sock in self.sockets:
            sock.close()

    def run(self, catch_exceptions=False):

        fail = None

        # Catching all exceptions makes this really hard to
        # debug, is it with care.
        if catch_exceptions:
            try:
                self._run()
            except Exception as e:
                qutil.LOGGER.exception("Unexpected error in run for {id}.".format(id=self.get_id))
                fail = e
            finally:
                if(self.context != None):
                    self.context.destroy()
                if fail:
                    raise fail
        else:
            self._run()
            if(self.context != None):
                self.context.destroy()

    def loop(self):
        while not self.done:
            self.confirm()
            self.do_work()

    # -----------
    #  Messaging
    # -----------

    def signal_done(self):
        #notify down stream components that we're done
        if(self.out_socket != None):
            self.out_socket.send(str(CONTROL_PROTOCOL.DONE))
        #notify host we're done

        # TODO: proper framing
        self.sync_socket.send(self.get_id + ":" + str(CONTROL_PROTOCOL.DONE))

        self.receive_sync_ack()
        #notify internal work look that we're done
        self.done = True

    def confirm(self):
        # send a synchronization request to the host

        # TODO: proper framing
        self.sync_socket.send(self.get_id + ":RUN")

        self.receive_sync_ack() # blocking

    def receive_sync_ack(self):
        """
        Wait for synchronization reply from the host.
        """

        socks = dict(self.sync_poller.poll(self.heartbeat_timeout))
        if self.sync_socket in socks and socks[self.sync_socket] == self.zmq.POLLIN:
            message = self.sync_socket.recv()
        else:
            raise Exception("Sync ack timed out on response for {id}".format(id=self.get_id))

    def bind_data(self):
        return self.bind_pull_socket(self.addresses['data_address'])

    def connect_data(self):
        return self.connect_push_socket(self.addresses['data_address'])

    def bind_feed(self):
        return self.bind_pub_socket(self.addresses['feed_address'])

    def connect_feed(self):
        return self.connect_sub_socket(self.addresses['feed_address'])

    def bind_merge(self):
        return self.bind_pull_socket(self.addresses['merge_address'])

    def connect_merge(self):
        return self.connect_push_socket(self.addresses['merge_address'])

    def bind_result(self):
        return self.bind_pub_socket(self.addresses['result_address'])

    def connect_result(self):
        return self.connect_sub_socket(self.addresses['result_address'])

    def bind_pull_socket(self, addr):
        pull_socket = self.context.socket(self.zmq.PULL)
        pull_socket.bind(addr)
        self.poll.register(pull_socket, self.zmq.POLLIN)

        self.sockets.append(pull_socket)

        return pull_socket

    def connect_push_socket(self, addr):
        push_socket = self.context.socket(self.zmq.PUSH)
        push_socket.connect(addr)
        #push_socket.setsockopt(self.zmq.LINGER,0)
        self.sockets.append(push_socket)
        self.out_socket = push_socket

        return push_socket

    def bind_pub_socket(self, addr):
        pub_socket = self.context.socket(self.zmq.PUB)
        pub_socket.bind(addr)
        #pub_socket.setsockopt(self.zmq.LINGER,0)
        self.out_socket = pub_socket

        return pub_socket

    def connect_sub_socket(self, addr):
        sub_socket = self.context.socket(self.zmq.SUB)
        sub_socket.connect(addr)
        sub_socket.setsockopt(self.zmq.SUBSCRIBE,'')
        self.sockets.append(sub_socket)

        self.poll.register(sub_socket, self.zmq.POLLIN)

        return sub_socket

    def setup_poller(self):
        """
        Setup the poller used for multiplexing the incoming data
        handling sockets.
        """

        self.poll = self.zmq.Poller()

    def setup_control(self):
        """
        Set up the control socket. Used to monitor the the
        overall status of the simulation and to forcefully tear
        down the simulation in case of a failure.
        """
        assert self.controller

        self.control_out = self.controller.message_sender()
        self.control_in = self.controller.message_listener()

        self.poll.register(self.control_in, self.zmq.POLLIN)
        self.sockets.extend([self.control_in, self.control_out])

    def setup_sync(self):
        qutil.LOGGER.debug("Connecting sync client for {id}".format(id=self.get_id))

        self.sync_socket = self.context.socket(self.zmq.REQ)
        self.sync_socket.connect(self.addresses['sync_address'])
        #self.sync_socket.setsockopt(self.zmq.LINGER,0)

        # Explictly, a different poller for obvious reasons.
        self.sync_poller = self.zmq.Poller()
        self.sync_poller.register(self.sync_socket, self.zmq.POLLIN)

        self.sockets.append(self.sync_socket)

    def debug(self):
        return (
            self.get_id          ,
            self.huid            ,
            socket.gethostname() ,
            os.getpid()          ,
            hex(id(self))        ,
        )

    def __repr__(self):
        return "<{name} {uuid} at {host} {pid} {pointer}>".format(
            name    = self.get_id          ,
            uuid    = self.huid            ,
            host    = socket.gethostname() ,
            pid     = os.getpid()          ,
            pointer = hex(id(self))        ,
        )
