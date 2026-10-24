"""Exchange rates between currencies."""

from decimal import Decimal

from ..currency import check_code
from ..errors import MissingRate


class RateTable:
    """Exchange rates, each stated once: set("EUR", "USD", 1.10) means one EUR is worth 1.10 USD.

    rate() also finds the inverse of a stated rate, and a rate through a third
    currency when neither direction is stated.
    """

    def __init__(self):
        self._rates = {}

    def set(self, base, quote, rate):
        check_code(base)
        check_code(quote)
        rate = Decimal(str(rate))
        if base == quote:
            raise ValueError("a rate needs two different currencies")
        if not rate > 0:
            raise ValueError(f"a rate must be positive, not {rate}")
        self._rates[(base, quote)] = rate

    def currencies(self):
        return {code for pair in self._rates for code in pair}

    def pairs(self):
        """The stated rates as (base, quote, rate), sorted."""
        return sorted((base, quote, rate) for (base, quote), rate in self._rates.items())

    def _lookup(self, source, target):
        """The rate from source to target if it is stated in either direction, else None."""
        if (source, target) in self._rates:
            return self._rates[(source, target)]
        if (target, source) in self._rates:
            return self._rates[(target, source)]
        return None

    def rate(self, source, target):
        """How many units of target one unit of source is worth. Raises MissingRate."""
        if source == target:
            return Decimal(1)
        found = self._lookup(source, target)
        if found is not None:
            return found
        for via in sorted(self.currencies() - {source, target}):
            first, second = self._lookup(source, via), self._lookup(via, target)
            if first is not None and second is not None:
                return first * second
        raise MissingRate(source, target)
