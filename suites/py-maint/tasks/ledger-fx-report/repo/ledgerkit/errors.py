"""Everything ledgerkit raises derives from LedgerError."""


class LedgerError(Exception):
    """Base class of ledgerkit's errors."""


class ParseError(LedgerError):
    """A journal or rates file that cannot be read; line is 1-based."""

    def __init__(self, line, message):
        super().__init__(f"line {line}: {message}")
        self.line = line


class UnknownAccount(LedgerError):
    """A posting to an account that was never declared."""


class UnbalancedTransaction(LedgerError):
    """A transaction whose postings do not sum to zero in every currency."""


class CurrencyMismatch(LedgerError):
    """Arithmetic on amounts in different currencies, or a posting in another currency than its account's."""


class MissingRate(LedgerError):
    """No exchange rate, direct, inverse or through a third currency, between two currencies."""

    def __init__(self, source, target):
        super().__init__(f"no exchange rate from {source} to {target}")
        self.source = source
        self.target = target
