"""
The messaging protocol for Zipline.

Asserts are in place because any protocol error corresponds to a
programmer error so we want it to fail fast and in an obvious way
so it doesn't happen again. ZeroMQ follows the same philosophy.

Notes
=====

Msgpack
-------
Msgpack is the fastest serialization protocol in Python at the
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

Data Structures
===============

Enum
----

Classic C style enumeration::

    opts = Enum('FOO', 'BAR')

    opts.FOO # 0
    opts.BAR # 1
    opts.FOO = opts.BAR # False

Oh, and if you do this::

    protocol.Enum([1,2,3])

Your interpreter will segfault, think of this like an extreme assert.

Namedict
--------

Namedicts are dict like objects that have fields accessible by attribute lookup
as well as being indexable and iterable::

    HEARTBEAT_PROTOCOL = namedict({
        'REQ' : b'\x01',
        'REP' : b'\x02',
    })

    HEARTBEAT_PROTOCOL.REQ # syntactic sugar
    HEARTBEAT_PROTOCOL.REP # oh suga suga

    HEARTBEAT_PROTOCOL['REQ'] # classic dictionary index

Namedtuple
----------

From the standard library, namedtuples are great for specifying
containers for spec'ing data container objects::

    from collections import namedtuple

    Person = namedtuple('Person', 'name age gender')
    bob = Person(name='Bob', age=30, gender='male')

    bob.name   # 'Bob'
    bob.age    # 30
    bob.gender # male

    # The slots on the tuple are also finite and read-only. This
    # is a good thing, keeps us honest!

    bob.hobby = 'underwater archery'
    # Will raise:
    # AttributeError: 'Person' object has no attribute 'hobby'

    bob.name = 'joe'
    # Will raise:
    # AttributeError: can't set attribute

    # Namedtuples are normally read-only, but you can change the
    # internals using a private operation.
    bob._replace(gender='female')

    # You can also dump out to dictionary form:
    OrderedDict([('name', 'Bob'), ('age', 30), ('gender', 'male')])

    # Or JSON.
    json.dumps(bob._asdict())
    '{"gender":"male","age":30,"name":"Bob"}'

"""

import msgpack
import numbers
import datetime
import pytz
import copy
from collections import namedtuple

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
    So that you can use::

        foo.BAR
        -- or --
        foo['BAR']

    For more complex structs use collections.namedtuple:
    """

    def __init__(self, dct=None):
        if(dct):
            self.__dict__.update(dct)

    def __setitem__(self, key, value):
        """
        Required for use by pymongo as_class parameter to find.
        """
        if(key == '_id'):
            self.__dict__['id'] = value
        else:
            self.__dict__[key] = value

    def __getitem__(self, key):
        return self.__dict__[key]

    def keys(self):
        return self.__dict__.keys()

    def as_dict(self):
        # shallow copy is O(n)
        return copy.copy(self.__dict__)

    def delete(self, key):
        del(self.__dict__[key])

    def merge(self, other_nd):
        assert isinstance(other_nd, namedict)
        self.__dict__.update(other_nd.__dict__)

    def __repr__(self):
        return "namedict: " + str(self.__dict__)

    def __eq__(self, other):
        # !!!!!!!!!!!!!!!!!!!!
        # !!!! DANGEROUS !!!!!
        # !!!!!!!!!!!!!!!!!!!!
        return other != None and self.__dict__ == other.__dict__

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
    assert isinstance(id, basestring,)
    assert isinstance(status, int)

    return msgpack.dumps(tuple([id, status]))

def CONTORL_UNFRAME(msg):
    assert isinstance(msg, basestring)

    try:
        id, status = msgpack.loads(msg)
        assert isinstance(id, basestring)
        assert isinstance(status, int)

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

COMPONENT_TYPE = Enum(
    'SOURCE'  , # 0
    'CONDUIT' , # 1
    'SINK'    , # 2
)

COMPONENT_STATE = Enum(
    'OK'        , # 0
    'DONE'      , # 1
    'EXCEPTION' , # 2
)

# NOFAILURE  - Component is either not running or has not failed
# ALGOEXCEPT - Exception thrown in the given algorithm
# HOSTEXCEPT - Exception thrown on our end.
# INTERRUPT  - Manually interuptted by user

COMPONENT_FAILURE = Enum(
    'NOFAILURE'  ,
    'ALGOEXCEPT' ,
    'HOSTEXCEPT' ,
    'INTERRUPT'  ,
)

BACKTEST_STATE = Enum(
    'IDLE'       ,
    'QUEUED'     ,
    'INPROGRESS' ,
    'CANCELLED'  , # cancelled ( before natural completion )
    'EXCEPTION'  , # failure ( due to unnatural causes )
    'DONE'       , # done ( naturally completed )
)

# ==================
# Datasource Protocol
# ==================

INVALID_DATASOURCE_FRAME = FrameExceptionFactory('DATASOURCE')

def DATASOURCE_FRAME(event):
    """
    Wraps any datasource payload with id and type, so that unpacking may choose
    the write UNFRAME for the payload.

    ::ds_id:: an identifier that is unique to the datasource in the context of
    a component host (e.g. Simulator
    ::ds_type:: a string denoting the datasource type. Must be on of::
        TRADE
        (others to follow soon)
    ::payload:: a msgpack string carrying the payload for the frame
    """

    assert isinstance(event.source_id, basestring)
    assert isinstance(event.type, int), 'Unexpected type %s' % (event.type)
    
    #datasources will send sometimes send empty msgs to feel gaps
    if len(event.keys()) == 2:
        return msgpack.dumps(tuple([
            event.type, 
            event.source_id, 
            DATASOURCE_TYPE.EMPTY
        ]))

    if(event.type == DATASOURCE_TYPE.TRADE):
        return msgpack.dumps(tuple([
            event.type, 
            event.source_id, 
            TRADE_FRAME(event)
        ]))
    elif(event.type == DATASOURCE_TYPE.ORDER):
        return msgpack.dumps(tuple([
            event.type, 
            event.source_id, 
            ORDER_SOURCE_FRAME(event)
        ]))
    else:
        raise INVALID_DATASOURCE_FRAME(str(event))

def DATASOURCE_UNFRAME(msg):
    """
    Extracts payload, and calls correct UNFRAME method based on the datasource
    type passed along.

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
        ds_type, source_id, payload = msgpack.loads(msg)
        assert isinstance(ds_type, int)
        rval = namedict({'source_id':source_id})
        if payload == DATASOURCE_TYPE.EMPTY:
            child_value = namedict({'dt':None})
        elif(ds_type == DATASOURCE_TYPE.TRADE):
            child_value = TRADE_UNFRAME(payload)
        elif(ds_type == DATASOURCE_TYPE.ORDER):
            child_value = ORDER_SOURCE_UNFRAME(payload)
        else:
            raise INVALID_DATASOURCE_FRAME(msg)
            
        rval.merge(child_value)
        return rval
        
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
    payload = event.as_dict()
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
    assert isinstance(name, basestring)
    if value == None:
        return msgpack.dumps(tuple([name, TRANSFORM_TYPE.EMPTY]))
    if(name == TRANSFORM_TYPE.TRANSACTION):
        value = TRANSACTION_FRAME(value)
    return msgpack.dumps(tuple([name, value]))

def TRANSFORM_UNFRAME(msg):
    """
    :rtype: namedict with <transform_name>:<transform_value>
    """
    try:

        name, value = msgpack.loads(msg)
        if(value == TRANSFORM_TYPE.EMPTY):
            return namedict({name : None})
        #TODO: anything we can do to assert more about the content of the dict?
        assert isinstance(name, basestring)
        if(name == TRANSFORM_TYPE.PASSTHROUGH):
            value = FEED_UNFRAME(value)
        elif(name == TRANSFORM_TYPE.TRANSACTION):
            value = TRANSACTION_UNFRAME(value)

        return namedict({name : value})
    except TypeError:
        raise INVALID_TRANSFORM_FRAME(msg)
    except ValueError:
        raise INVALID_TRANSFORM_FRAME(msg)

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
    PACK_DATE(event)
    if(event.has_attr(TRANSFORM_TYPE.TRANSACTION)):
        if(event.TRANSACTION == None):
            event.TRANSACTION = TRANSFORM_TYPE.EMPTY
        else:
            event.TRANSACTION = TRANSACTION_FRAME(event.TRANSACTION)
    payload = event.as_dict()
    return msgpack.dumps(payload)

def MERGE_UNFRAME(msg):
    try:
        payload = msgpack.loads(msg)
        #TODO: anything we can do to assert more about the content of the dict?
        assert isinstance(payload, dict)
        payload = namedict(payload)
        if(payload.has_attr(TRANSFORM_TYPE.TRANSACTION)):
            if(payload.TRANSACTION == TRANSFORM_TYPE.EMPTY):
                payload.TRANSACTION = None
            else:
                payload.TRANSACTION = TRANSACTION_UNFRAME(payload.TRANSACTION)
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
# Trades - Should only be called from inside DATASOURCE_ (UN)FRAME.
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
    assert event.type == DATASOURCE_TYPE.TRADE
    assert isinstance(event.sid, int)
    assert isinstance(event.price, numbers.Real)
    assert isinstance(event.volume, numbers.Integral)
    PACK_DATE(event)
    return msgpack.dumps(tuple([
        event.sid,
        event.price,
        event.volume,
        event.dt,
        event.type,
    ]))

def TRADE_UNFRAME(msg):
    try:
        packed = msgpack.loads(msg)
        sid, price, volume, dt, source_type = packed

        assert isinstance(sid, int)
        assert isinstance(price, numbers.Real)
        assert isinstance(volume, numbers.Integral)
        rval = namedict({
            'sid'       : sid,
            'price'     : price,
            'volume'    : volume,
            'dt'        : dt,
            'type'      : source_type
        })
        UNPACK_DATE(rval)
        return rval
    except TypeError:
        raise INVALID_TRADE_FRAME(msg)
    except ValueError:
        raise INVALID_TRADE_FRAME(msg)

# =========
# Orders - from client to order source
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

#
# ==================
# TRANSACTIONS - Should only be called from inside TRANSFORM_(UN)FRAME.
# ==================

def TRANSACTION_FRAME(event):
    assert isinstance(event, namedict)
    assert isinstance(event.sid, int)
    assert isinstance(event.price, numbers.Real)
    assert isinstance(event.commission, numbers.Real)
    assert isinstance(event.amount, int)
    PACK_DATE(event)
    return msgpack.dumps(tuple([
        event.sid,
        event.price,
        event.amount,
        event.commission,
        event.dt
    ]))

def TRANSACTION_UNFRAME(msg):
    try:
        sid, price, amount, commission, dt = msgpack.loads(msg)

        assert isinstance(sid, int)
        assert isinstance(price, numbers.Real)
        assert isinstance(commission, numbers.Real)
        assert isinstance(amount, int)
        rval = namedict({
            'sid'        : sid,
            'price'      : price,
            'amount'     : amount,
            'commission' : commission,
            'dt'      : dt
        })

        UNPACK_DATE(rval)
        return rval
    except TypeError:
        raise INVALID_TRADE_FRAME(msg)
    except ValueError:
        raise INVALID_TRADE_FRAME(msg)


# =========
# Orders - from order source to feed
#        - should only be called from inside DATASOURCE_(UN)FRAME
# =========

def ORDER_SOURCE_FRAME(event):
    assert isinstance(event.sid, int)
    assert isinstance(event.amount, int) #no partial shares...
    assert isinstance(event.source_id, basestring)
    assert event.type == DATASOURCE_TYPE.ORDER
    PACK_DATE(event)
    return msgpack.dumps(tuple([
        event.sid,
        event.amount,
        event.dt,
        event.source_id,
        event.type
    ]))


def ORDER_SOURCE_UNFRAME(msg):
    try:
        sid, amount, dt, source_id, source_type = msgpack.loads(msg)
        event = namedict({
            "sid"       : sid,
            "amount"    : amount,
            "dt"        : dt,
            "source_id" : source_id,
            "type"      : source_type
        })
        assert isinstance(sid, int)
        assert isinstance(amount, int)
        assert isinstance(source_id, basestring)
        assert isinstance(source_type, int)
        UNPACK_DATE(event)
        return event
    except TypeError:
        raise INVALID_ORDER_FRAME(msg)
    except ValueError:
        raise INVALID_ORDER_FRAME(msg)

# =================
# Date Helpers
# =================

def PACK_DATE(event):
    """
    Packs the datetime property of event into msgpack'able longs.
    This function should be called purely for its side effects. 
    The event's 'dt' property is replaced by a tuple of integers::
        - year, month, day, hour, minute, second, microsecond
    
    PACK_DATE and UNPACK_DATE are inverse operations. 
    
    :param event: event must a namedict with a property named 'dt' that is a datetime.
    :rtype: None
    """
    assert isinstance(event.dt, datetime.datetime)
    assert event.dt.tzinfo == pytz.utc #utc only please
    year, month, day, hour, minute, second =  event.dt.timetuple()[0:6]
    micros = event.dt.microsecond
    event['dt'] = tuple([year, month, day, hour, minute, second, micros])

def UNPACK_DATE(event):
    """
    Unpacks the datetime property of event from msgpack'able longs.
    This function should be called purely for its side effects. 
    The event's 'dt' property is converted to a datetime by reading and then 
    combining a tuple of integers.
    
    UNPACK_DATE and PACK_DATE are inverse operations. 
    
    :param tuple event: event must a namedict with::
            - a property named 'dt_tuple' that is a tuple of integers 
            representing the date and time in UTC. dt_tumple must have year, 
            month, day, hour, minute, second, and microsecond
    :rtype: None
    """
    assert isinstance(event.dt, tuple)
    assert len(event.dt) == 7
    for item in event.dt:
        assert isinstance(item, numbers.Integral)
    year, month, day, hour, minute, second, micros = event.dt
    dt = datetime.datetime(year, month, day, hour, minute, second)
    dt = dt.replace(microsecond = micros, tzinfo = pytz.utc)
    event.dt = dt


DATASOURCE_TYPE = Enum(
    'ORDER',
    'TRADE',
    'EMPTY',
)

ORDER_PROTOCOL = Enum(
    'DONE',
    'BREAK',
)


#Transform type needs to be a namedict to facilitate merging.
TRANSFORM_TYPE = namedict({
    'TRANSACTION' : 'TRANSACTION', #needed?
    'PASSTHROUGH' : 'PASSTHROUGH',
    'EMPTY'       : ''
})


FINANCE_COMPONENT = namedict({
    'TRADING_CLIENT'   : 'TRADING_CLIENT',
    'PORTFOLIO_CLIENT' : 'PORTFOLIO_CLIENT',
    'ORDER_SOURCE'     : 'ORDER_SOURCE',
    'TRANSACTION_SIM'  : 'TRANSACTION_SIM'
})
