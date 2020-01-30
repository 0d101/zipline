"""
HDF5-backed FX Rates

The classes defined in this file produce and consume foreign exchange rates
stored in HDF5 files.

An FX Rate file contains one or more "rates", each of which contains a
collection of arrays exchange rates describing how to convert from "base"
currencies to "quote" currencies.

The specific set of rates contained in a particular file is arbitrary and
user-defined. We infer no particular semantics from their names, other than
that they are distinct rates. Pipeline API users can select the rate that
should be used for a currency conversion via ``BoundColumn.fx`` method.

Examples of possible rate names might be things like "bid", "mid", and "ask",
or "london_close", "tokyo_close", "nyse_close".

HDF5 FX Rate File Schema
------------------------

An FX rate file has two kinds of nodes: **data nodes** and **index nodes**.

Data nodes are 2-dimensional arrays of numerical exchange rate values. Data
nodes are stored at paths of the form /data/{rate}/{quote_currency}/rates. The
path to a data node encodes two pieces of information: the "rate" (e.g. 'mid',
'bid', 'ask') of the node, and the quote currency of the node. All data nodes
in a file have the same shape.

Index nodes are 1-dimensional arrays containing row- and column-labels for the
file's data nodes. There are two index nodes in a file, at /index/dts and
/index/currencies.

**Example:**

A file containing fields ["rate1", "rate2"] and currencies ["USD", "CAD",
"EUR"] has the following schema:

.. code-block:: none

   /data
     /field1
       /USD
         /rates
       /CAD
         /rates
       /EUR
         /rates
     /field2
       /USD
         /rates
       /CAD
         /rates
       /EUR
         /rates
   /index
     /dts
     /currencies

/index/dts
^^^^^^^^^^

This node contains a 1-dimensional array of int64s. When interpreted as an
np.datetime64[ns], each entry in the array defines the date label for the
corresponding row of a data node.

/index/currencies
^^^^^^^^^^^^^^^^^

This node contains a 1-dimensional array of length-3 strings. Each entry in the
array defines the the label for the corresponding column of a data node.

/data/{rate}/{quote_currency}/rates
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Nodes at these locations are data nodes. They store 2-dimensional arrays of
exchange rates.

Represented as a DataFrame with appropriate indices, the data at
/data/rate1/USD/rates might look like this::

                    USD       EUR       CAD
   2014-01-01  1.000000  0.808477  0.442329
   2014-01-02  1.000000  2.235770  0.817352
   2014-01-03  1.000000  0.378603  1.181822
   2014-01-04  1.000000  0.090983  0.198182
   2014-01-05  1.000000  1.135781  0.536609

Each column of the array contains exchange rates mapping from currency of the
column's label to the quote encoded in the node's path (USD in this
example). The label for column i in a data node is the ith element of
/index/currencies.

Each row of the array contains exchange rates for a given date. The label for
row i in a data node is the ith element of /index/dts.
"""
from interface import implements
import h5py
from logbook import Logger
import numpy as np
import pandas as pd

from zipline.utils.memoize import lazyval
from zipline.utils.numpy_utils import bytes_array_to_native_str_object_array

from .base import FXRateReader, DEFAULT_FX_RATE
from .utils import check_dts, is_sorted_ascending

HDF5_FX_VERSION = 0

INDEX = 'index'
DATA = 'data'
CURRENCIES = 'currencies'
DTS = 'dts'
RATES = 'rates'

log = Logger(__name__)


class HDF5FXRateReader(implements(FXRateReader)):
    """An FXRateReader backed by HDF5.

    Parameters
    ----------
    group : h5py.Group
        Top-level group written by an :class:`HDF5FXRateWriter`.
    default_rate : str
        Rate to use when ``get_rates`` is called requesting the default rate.
    """

    def __init__(self, group, default_rate):
        self._group = group
        self._default_rate = default_rate

        if self.version != HDF5_FX_VERSION:
            raise ValueError(
                "FX Reader version ({}) != File Version ({})".format(
                    HDF5_FX_VERSION, self.version,
                )
            )

    @classmethod
    def from_path(cls, path, default_rate):
        """
        Construct from a file path.

        Parameters
        ----------
        path : str
            Path to an HDF5 fx rates file.
        default_rate : str
            Rate to use when ``get_rates`` is called requesting the default
            rate.
        """
        return cls(h5py.File(path), default_rate=default_rate)

    @lazyval
    def version(self):
        try:
            return self._group.attrs['version']
        except KeyError:
            # TODO: Remove this.
            return 0

    @lazyval
    def dts(self):
        """Row labels for rate groups.
        """
        raw_dts = self._group[INDEX][DTS][:].astype('M8[ns]')
        if not is_sorted_ascending(raw_dts):
            raise ValueError("dts are not sorted for {}!".format(self._group))

        return pd.DatetimeIndex(raw_dts, tz='UTC')

    @lazyval
    def currencies(self):
        """Column labels for rate groups.
        """
        # Currencies are stored as fixed-length bytes in the file, but we want
        # `str` objects in memory.
        bytes_array = self._group[INDEX][CURRENCIES][:]
        objects = bytes_array_to_native_str_object_array(bytes_array)
        return pd.Index(objects)

    def get_rates(self, rate, quote, bases, dts):
        """Get rates to convert ``bases`` into ``quote``.

        See :class:`zipline.data.fx.base.FXRateReader` for details.
        """
        if rate == DEFAULT_FX_RATE:
            rate = self._default_rate

        check_dts(self.dts, dts)

        row_ixs = self.dts.searchsorted(dts, side='right') - 1
        col_ixs = self.currencies.get_indexer(bases)

        try:
            dataset = self._group[DATA][rate][quote][RATES]
        except KeyError:
            raise ValueError(
                "FX rates not available for rate={}, quote_currency={}."
                .format(rate, quote)
            )

        # OPTIMIZATION: Row indices correspond to dates, which must be in
        # sorted order. Rather than reading the entire dataset from h5, we can
        # read just the interval from min_row to max_row inclusive
        #
        # However, we also need to handle two important edge cases:
        #
        #   1. row_ixs contains -1 for dts before the start of self.dts.
        #   2. col_ixs contains -1 for any currencies we don't know about.
        #
        # If either of the above cases obtains, we want to return NaN for the
        # corresponding output locations.

        # We handle (1) by reading raw data into a buffer with one extra
        # row. When we then apply the row index to permute the raw data into
        # the correct order, any rows with values of -1 will pull from the
        # extra row, which will always contain NaN>
        #
        # We handle (2) by overwriting columns with indices of -1 with NaN as a
        # postprocessing step.
        slice_begin = max(row_ixs[0], 0)
        slice_end = max(row_ixs[-1], 0) + 1  # +1 to be inclusive of end date.

        # Allocate a buffer full of NaNs with one extra row/column. See
        # OPTIMIZATION notes above.
        buf = np.full(
            (slice_end - slice_begin + 1, len(self.currencies)),
            np.nan,
        )

        # Read data into all but the last row/column of the buffer.
        dataset.read_direct(
            buf[:-1],
            np.s_[slice_begin:slice_end],
        )

        # Permute the rows into place, pulling from the empty NaN locations for
        # row/column indices of -1.
        out = buf[:, col_ixs][row_ixs - slice_begin]

        # Fill missing columns with NaN. See OPTIMIZATION notes above.
        out[:, col_ixs == -1] = np.nan

        return out


class HDF5FXRateWriter(object):
    """Writer class for HDF5 files consumed by HDF5FXRateReader.
    """
    def __init__(self, group):
        self._group = group

    def write(self, dts, currencies, data):
        """Write data to the file.

        Parameters
        ----------
        dts : pd.DatetimeIndex
            Index of row labels for rates to be written.
        currencies : np.array[object]
            Array of column labels for rates to be written.
        data : iterator[(str, str, np.array[float64])]
            Iterator of (rate, quote_currency, array) tuples. Each array
            should be of shape ``(len(dts), len(currencies))``, and should
            contain a table of rates where each column is a timeseries of rates
            mapping its column label's currency to ``quote_currency``.
        """
        self._write_metadata()
        self._write_index_group(dts, currencies)
        self._write_data_group(dts, currencies, data)

    def _write_metadata(self):
        self._group.attrs['version'] = HDF5_FX_VERSION
        self._group.attrs['last_updated_utc'] = str(pd.Timestamp.utcnow())

    def _write_index_group(self, dts, currencies):
        """Write content of /index.
        """
        if not is_sorted_ascending(dts):
            raise ValueError("dts is not sorted")

        for c in currencies:
            if not isinstance(c, str) or len(c) != 3:
                raise ValueError("Invalid currency: {!r}".format(c))

        index_group = self._group.create_group(INDEX)

        self._log_writing(INDEX, DTS)
        index_group.create_dataset(DTS, data=dts.astype('int64'))

        self._log_writing(INDEX, CURRENCIES)
        index_group.create_dataset(CURRENCIES, data=currencies.astype('S3'))

    def _write_data_group(self, dts, currencies, data):
        """Write content of /data.
        """
        data_group = self._group.create_group(DATA)

        expected_shape = (len(dts), len(currencies))
        for rate, quote, array in data:
            if array.shape != expected_shape:
                raise ValueError(
                    "Unexpected shape for rate={}, quote={}."
                    "\nExpected shape: {}. Got {}."
                    .format(rate, quote, expected_shape, array.shape)
                )

            self._log_writing(DATA, rate, quote)
            target = data_group.require_group('/'.join((rate, quote)))
            target.create_dataset(RATES, data=array)

    def _log_writing(self, *path):
        log.debug("Writing {}", '/'.join(path))
