import copy
import pandas
from ctypes import Structure, c_ubyte
from collections import MutableMapping
from itertools import izip

def Enum(*options):
    """
    Fast enums are very important when we want really tight zmq
    loops. These are probably going to evolve into pure C structs
    anyways so might as well get going on that.
    """
    class cstruct(Structure):
        _fields_ = [(o, c_ubyte) for o in options]
        __iter__ = lambda s: iter(range(len(options)))
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

class ndict(MutableMapping):
    """
    Xtreme Namedicts 2.0

    Ndicts are dict like objects that have fields accessible by attribute
    lookup as well as being indexable and iterable. Done right
    this time.
    """

    cls = None
    __slots__ = ['cls', '__internal']

    def __init__(self, dct=None):
        self.__internal = dict()

        if not ndict.cls:
            ndict.cls = frozenset(dir(self))

        if dct:
            self.__internal.update(dct)

    # Abstact Overloads
    # -----------------

    def __deepcopy__(self, memo):
        return ndict(copy.deepcopy(self.__internal))

    def __setattr__(self, key, value):
        if key == 'cls' or key == '__internal' or '_ndict' in key:
            super(ndict, self).__setattr__(key, value)
        else:
            self.__internal[key] = value
        return value

    def __setitem__(self, key, value):
        """
        Required for use by pymongo as_class parameter to find.
        """
        if key == '_id':
            self.__internal['id'] = value
        else:
            self.__internal[key] = value

    def __getattr__(self, key):
        if key in self.cls:
            super(ndict, self).__getattr__(key)
        else:
            return self.__internal[key]

    def __getitem__(self, key):
        return self.__internal[key]

    def __delitem__(self, key):
        del self.__internal[key]

    def __iter__(self):
        return self.__internal.iterkeys()

    def __len__(self):
        return len(self.__internal)

    # Compatability with namedicts
    # ----------------------------

    # for compat, not the Python way to do things though...
    # Deprecated, use builtin ``del`` operator.
    delete = __delitem__

    def has_attr(self, key):
        """
        Deprecated, use builtin ``in`` operator.
        """
        return self.__contains__(key)

    def has_key(self, key):
        return self.__contains__(key)

    # Custom Methods
    # --------------

    def copy(self):
        return ndict(copy.copy(self.__internal))

    def as_dataframe(self):
        """
        Return the representation as a Pandas dataframe.
        """
        d = pandas.DataFrame(self.__internal)
        return d

    def as_series(self):
        """
        Return the representation as a Pandas time series.
        """
        s = pandas.Series(self.__internal)
        s.name = self.sid
        return s

    def as_dict(self):
        """
        Return the representation as a vanilla Python dict.
        """
        # shallow copy is O(n)
        return copy.copy(self.__internal)

    def merge(self, other_nd):
        """
        Merge in place with another ndict.
        """
        assert isinstance(other_nd, ndict)
        self.__internal.update(other_nd.__internal)

    def __repr__(self):
        return "ndict(%s)" % str(self.__internal)

    # Faster dictionary comparison?
    #def __eq__(self, other):
        #assert isinstance(other, ndict)

        #keyeq = set(self.keys()) == set(other.keys())

        #if not keyeq:
            #return False

        #for i, j in izip(self.itervalues(), other.itervalues()):
            #if i != j:
                #return False

        #return True

# This is not neccesarily the most intuitive construction, but
# we're aiming for raw performance rather than readability. So
# we do things that we would not normally do in business logic.
def namelookup(dct):
    ks = dct.keys()
    vs = dct.values()
    dct = {}
    class _lookup:
        __slots__ = ks
        def __init__(self):
            for k, v in zip(ks, vs):
                setattr(self,k,v)
            self.__setattr__ = self.locked
        def locked(self,k,v):
            raise Exception('Name lookups are fixed at init.')
        def __repr__(self):
            return '<namelookup %s>' % self.__slots__
    del dct
    return _lookup()
