"""
Commonly used messaging components.
"""

import logging

import zipline.protocol as zp
from zipline.component import Component
from zipline.protocol import COMPONENT_TYPE

LOGGER = logging.getLogger('ZiplineLogger')

class DataSource(Component):
    """
    Baseclass for data sources. Subclass and implement send_all - usually this
    means looping through all records in a store, converting to a dict, and
    calling send(map).

    Every datasource has a dict property to hold filters::
        - key -- name of the filter, e.g. SID
        - value -- a primitive representing the filter. e.g. a list of ints.

    Modify the datasource's filters via the set_filter(name, value)
    """
    def __init__(self, source_id):
        Component.__init__(self)

        self.id = source_id
        self.init()
        self.filter = {}

    def init(self):
        self.cur_event = None

    def set_filter(self, name, value):
        self.filter[name] = value

    @property
    def get_id(self):
        return self.id

    @property
    def get_type(self):
        return COMPONENT_TYPE.SOURCE

    def open(self):
        self.data_socket = self.connect_data()

    def send(self, event):
        """
        Emit data.
        """
        assert isinstance(event, zp.namedict)

        event['source_id'] = self.get_id
        event['type'] = self.get_type

        try:
            ds_frame = self.frame(event)
        except zp.INVALID_DATASOURCE_FRAME as exc:
            return self.signal_exception(exc)

        self.data_socket.send(ds_frame)

    def frame(self, event):
        return zp.DATASOURCE_FRAME(event)
