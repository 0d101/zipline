from collections import defaultdict, deque
from zipline.transforms.base import BaseTransform

class Returns(object):
    """
    Class that maintains a dictionary from sids to the sid's
    closing price N trading days ago.
    """
    def __init__(self, days):
        self.days = days
        self.mapping = defaultdict(self._create)
        
    def update(self, event):
        """
        Update and return the calculated returns for this event's sid.
        """
        assert event.has_key('dt')
        assert event.has_key('price')
        
        tracker = self.mapping[event.sid]
        tracker.update(event)
        
        return tracker.get_returns()

    def _create(self):
        return ReturnsFromPriorClose(self.days)

class ReturnsFromPriorClose(object):
    """
    Records the last N closing events for a given security as well as the
    last event for the security.  When we get an event for a new day, we 
    treat the last event seen  as the close for the previous day.
    """

    def __init__(self, days):
        self.closes = deque()
        self.last_event = None
        self.returns = None
        self.days = days

    def get_returns(self):
        return self.returns

    def update(self, event):
        
        if self.last_event:

            # Day has changed since the last event we saw.  Treat
            # the last event as the closing price for its day and 
            # clear out the oldest close if it has expired.
            if self.last_event.dt.date() != event.dt.date():

                self.closes.append(self.last_event)

                # We keep an event for the end of each trading day, so
                # if the number of stored events is greater than the
                # number of days we want to track, the oldest close
                # is expired and should be discarded.
                while len(self.closes) > self.days:
                    # Pop the oldest event.
                    self.closes.popleft()

        # We only generate a return value once we've seen enough days
        # to give a sensible value.  Would be nice if we could query
        # db for closes prior to our initial event, but that would
        # require giving this transform database creds, which we want
        # to avoid.

        if len(self.closes) == self.days:
            last_close = self.closes[0].price 
            change = event.price - last_close
            self.returns = change / last_close


        # the current event is now the last_event
        self.last_event = event
        
