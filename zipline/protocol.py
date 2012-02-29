"""
The messaging protocol for Zipline.

Asserts are in place because any protocol error corresponds to a
programmer error so we want it to fail fast and in an obvious way
so it doesn't happen again. ZeroMQ follows the same philosophy.

Notes
=====

Msgpack
-------
Msgpack is the fastest seriaization protocol in Python at the
moment. Its 100% C is typically orders of magnitude faster than
json and pickle making it awesome for ZeroMQ.

You can only serialize Python structural primitives: strings,
numeric types, dicts, tuples and lists. Any any recursive
combinations of these.

Basically every basestring in Python corresponds to valid
msgpack message since the protocol is highly error tolerant.
Just keep in mind that if you ever unpack a raw msgpack string
make sure it looks like what you intend and/or catch ValueError
and TypeError exceptions.

It also has the nice benefit of never invoking ``eval`` ( unlike
json and pickle) which is a major security boon since it is
impossible to arbitrary code for evaluation through messages.

UltraJSON
---------
For anything going to the browser UltraJSON is the fastest
serializer, its mostly C as well.

The same domain of serialization as msgpack applies: Python
structural primitives. It also has the additional constraint
that anything outside of UTF8 can cause serious problems, so if
you have a strong desire to JSON encode ancient Sanskrit
( admit it, we all do ), just say no.

"""

import msgpack
import numbers
import datetime
import pytz
import zipline.util as qutil
#import ujson
#import ultrajson_numpy

from ctypes import Structure, c_ubyte

def Enum(*options):
    """
    Fast enums are very important when we want really tight zmq
    loops. These are probably going to evolve into pure C structs
    anyways so might as well get going on that.
    """
    class cstruct(Structure):
        _fields_ = [(o, c_ubyte) for o in options]
    return cstruct(*range(len(options)))

def FrameExceptionFactory(name):
    """
    Exception factory with a closure around the frame class name.
    """
    class InvalidFrame(Exception):
        def __init__(self, got):
            self.got = got
        def __str__(self):
            return "Invalid {framecls} Frame: {got}".format(
                framecls = name,
                got = self.got,
            )
            
    return InvalidFrame

class namedict(object):
    """
    So that you can use:

        foo.BAR
        -- or --
        foo['BAR']

    For more complex strcuts use collections.namedtuple:
    """

    def __init__(self, dct=None):
        if(dct):
            self.__dict__.update(dct)
    
    def __setitem__(self, key, value):
        """Required for use by pymongo as_class parameter to find."""
        if(key == '_id'):
            self.__dict__['id'] = value
        else:
            self.__dict__[key] = value
    
    def merge(self, other_nd):
        assert isinstance(other_nd, namedict)
        self.__dict__.update(other_nd.__dict__)
        
    def __repr__(self):
        return "namedict: " + str(self.__dict__)
    
    def __eq__(self, other):
        return self.__dict__ == other.__dict__
    
    def has_attr(self, name):
        return self.__dict__.has_key(name)

# ================
# Control Protocol
# ================

INVALID_CONTROL_FRAME = FrameExceptionFactory('CONTROL')

CONTROL_PROTOCOL = Enum(
    'INIT'      , # 0 - req
    'INFO'      , # 1 - req
    'STATUS'    , # 2 - req
    'SHUTDOWN'  , # 3 - req
    'KILL'      , # 4 - req

    'OK'        , # 5 - rep
    'DONE'      , # 6 - rep
    'EXCEPTION' , # 7 - rep
)

def CONTROL_FRAME(id, status):
    assert isinstance(basestring, id)
    assert isinstance(int, status)

    return msgpack.dumps(tuple([id, status]))

def CONTORL_UNFRAME(msg):
    assert isinstance(basestring, msg)

    try:
        id, status = msgpack.loads(msg)
        assert isinstance(basestring, id)
        assert isinstance(int, status)

        return id, status
    except TypeError:
        raise INVALID_CONTROL_FRAME(msg)
    except ValueError:
        raise INVALID_CONTROL_FRAME(msg)
    #except AssertionError:
        #raise INVALID_CONTROL_FRAME(msg)

# ==================
# Heartbeat Protocol
# ==================

# These encode the msgpack equivelant of 1 and 2. The heartbeat
# frame should only be 1 byte on the wire.

HEARTBEAT_PROTOCOL = namedict({
    'REQ' : b'\x01',
    'REP' : b'\x02',
})

# ==================
# Component State
# ==================

COMPONENT_STATE = Enum(
    'OK'        , # 0
    'DONE'      , # 1
    'EXCEPTION' , # 2
)

# ==================
# Datasource Protocol
# ==================

INVALID_DATASOURCE_FRAME = FrameExceptionFactory('DATASOURCE')

def DATASOURCE_FRAME(event):
    """
    wraps any datasource payload with id and type, so that unpacking may choose the write
    UNFRAME for the payload.
    ::ds_id:: an identifier that is unique to the datasource in the context of a component host (e.g. Simulator
    ::ds_type:: a string denoting the datasource type. Must be on of::
        TRADE
        (others to follow soon)
    ::payload:: a msgpack string carrying the payload for the frame
    """
    assert isinstance(event.source_id, basestring)
    assert isinstance(event.type, basestring)
    if(event.type == "TRADE"):
        return msgpack.dumps(tuple([event.type, TRADE_FRAME(event)]))
    else:
        raise INVALID_DATASOURCE_FRAME(str(event))
    
def DATASOURCE_UNFRAME(msg):
    """
    extracts payload, and calls correct UNFRAME method based on the datasource type passed along
    returns a dict containing at least::
        - source_id
        - type
    other properties are added based on the datasource type::
        - TRADE::
            - sid - int security identifier
            - price - float
            - volume - int 
            - dt - a datetime object
    """
    try:
        ds_type, payload = msgpack.loads(msg)
        assert isinstance(ds_type, basestring)
        if(ds_type == "TRADE"):
            return TRADE_UNFRAME(payload)
        else:
            raise INVALID_DATASOURCE_FRAME(msg)
            
    except TypeError:
        raise INVALID_DATASOURCE_FRAME(msg)
    except ValueError:
        raise INVALID_DATASOURCE_FRAME(msg)
        
# ==================
# Feed Protocol
# ==================
INVALID_FEED_FRAME = FrameExceptionFactory('FEED')

def FEED_FRAME(event):
    """
    :event: a nameddict with at least::
        - source_id 
        - type
    """
    assert isinstance(event, namedict)
    source_id = event.source_id
    ds_type = event.type
    PACK_DATE(event)
    payload = event.__dict__
    return msgpack.dumps(payload)
    
def FEED_UNFRAME(msg):
    try:
        payload = msgpack.loads(msg)
        #TODO: anything we can do to assert more about the content of the dict?
        assert isinstance(payload, dict)
        rval = namedict(payload)
        UNPACK_DATE(rval)
        return rval
    except TypeError:
        raise INVALID_FEED_FRAME(msg)
    except ValueError:
        raise INVALID_FEED_FRAME(msg)
        
# ==================
# Transform Protocol
# ==================
INVALID_TRANSFORM_FRAME = FrameExceptionFactory('TRANSFORM')

def TRANSFORM_FRAME(name, value):
    """
    :event: a nameddict with at least::
        - source_id 
        - type
    """
    assert isinstance(name, basestring)
    assert value != None
    
    if(name == 'SIM_DT'):
        value = PACK_ALGO_DT(value)
        
    return msgpack.dumps(tuple([name, value]))
    
def TRANSFORM_UNFRAME(msg):
    """
    :rtype: namedict with <transform_name>:<transform_value>
    """
    try:
        name, value = msgpack.loads(msg)
        #TODO: anything we can do to assert more about the content of the dict?
        assert isinstance(name, basestring)
        if(name == "PASSTHROUGH"):
            value = FEED_UNFRAME(value)
        elif(name == "SIM_DT"):
            value = UNPACK_ALGO_DT(value)
        return namedict({name : value})
    except TypeError:
        raise INVALID_TRANSFORM_FRAME(msg)
    except ValueError:
        raise INVALID_TRANSFORM_FRAME(msg)

def PACK_ALGO_DT(value):
    value = namedict({'dt' : value})
    PACK_DATE(value)
    return value.__dict__

def UNPACK_ALGO_DT(value):    
    value = namedict(value)
    UNPACK_DATE(value)
    return value.dt

# ==================
# Merge Protocol
# ==================
INVALID_MERGE_FRAME = FrameExceptionFactory('MERGE')

def MERGE_FRAME(event):
    """
    :event: a nameddict with at least::
        - source_id 
        - type
    """
    assert isinstance(event, namedict)
    assert isinstance(event.dt, datetime.datetime)
    PACK_DATE(event)
    if(event.has_attr('SIM_DT')):
        event.SIM_DT = PACK_ALGO_DT(event.SIM_DT)
    payload = event.__dict__
    return msgpack.dumps(payload)
    
def MERGE_UNFRAME(msg):
    try:
        payload = msgpack.loads(msg)
        #TODO: anything we can do to assert more about the content of the dict?
        assert isinstance(payload, dict)
        payload = namedict(payload)
        if(payload.has_attr('SIM_DT')):
            payload.SIM_DT = UNPACK_ALGO_DT(payload.SIM_DT)
        assert isinstance(payload.epoch, numbers.Integral)
        assert isinstance(payload.micros, numbers.Integral)
        UNPACK_DATE(payload)
        return payload
    except TypeError:
        raise INVALID_MERGE_FRAME(msg)
    except ValueError:
        raise INVALID_MERGE_FRAME(msg)

    
# ==================
# Finance Protocol
# ==================
INVALID_ORDER_FRAME = FrameExceptionFactory('ORDER')
INVALID_TRADE_FRAME = FrameExceptionFactory('TRADE')

# ==================
# Trades
# ==================

def TRADE_FRAME(event):
    """:event: should be a namedict with::
            - ds_id     -- the datasource id sending this trade out
            - sid       -- the security id
            - price     -- float of the price printed for the trade
            - volume    -- int for shares in the trade
            - dt        -- datetime for the trade
            
    """
    assert isinstance(event, namedict)
    assert isinstance(event.source_id, basestring)
    assert event.type == "TRADE"
    assert isinstance(event.sid, int)
    assert isinstance(event.price, float)
    assert isinstance(event.volume, int)
    PACK_DATE(event)
    return msgpack.dumps(tuple([event.sid, event.price, event.volume, event.epoch, event.micros, event.type, event.source_id]))
    
def TRADE_UNFRAME(msg):
    try:
        sid, price, volume, epoch, micros, source_type, source_id = msgpack.loads(msg)
        
        assert isinstance(sid, int)
        assert isinstance(price, float)
        assert isinstance(volume, int)
        assert isinstance(epoch, numbers.Integral)
        assert isinstance(micros, numbers.Integral)
        rval = namedict({'sid' : sid, 'price' : price, 'volume' : volume, 'epoch' : epoch, 'micros' : micros, 'type' : source_type, 'source_id' : source_id})
        UNPACK_DATE(rval)
        return rval
    except TypeError:
        raise INVALID_TRADE_FRAME(msg)
    except ValueError:
        raise INVALID_TRADE_FRAME(msg)

# =========
# Orders
# =========

def ORDER_FRAME(sid, amount):
    assert isinstance(sid, int)
    assert isinstance(amount, int) #no partial shares...   
    return msgpack.dumps(tuple([sid, amount]))
    

def ORDER_UNFRAME(msg):
    try:
        sid, amount = msgpack.loads(msg)
        assert isinstance(sid, int)
        assert isinstance(amount, int)

        return sid, amount
    except TypeError:
        raise INVALID_ORDER_FRAME(msg)
    except ValueError:
        raise INVALID_ORDER_FRAME(msg)
        
# =================
# Date Helpers
# =================

def PACK_DATE(event):    
    assert isinstance(event.dt, datetime.datetime)
    assert event.dt.tzinfo == pytz.utc #utc only please
    epoch = long(event.dt.strftime('%s'))
    event['epoch'] = epoch
    event['micros'] = event.dt.microsecond
    del(event.__dict__['dt'])
    return event

def UNPACK_DATE(payload):
    assert isinstance(payload.epoch, numbers.Integral)
    assert isinstance(payload.micros, numbers.Integral)
    dt = datetime.datetime.fromtimestamp(payload.epoch)
    dt = dt.replace(microsecond = payload.micros, tzinfo = pytz.utc)
    del(payload.__dict__['epoch'])
    del(payload.__dict__['micros'])
    payload['dt'] = dt
    return payload
    

FINANCE_PROTOCOL = Enum(
    'ORDER'         , # 0
    'TRANSACTION'   , # 1
    'TRADE'         , # 2
    )
