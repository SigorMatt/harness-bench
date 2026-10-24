"""Converting amounts between currencies."""

from ..money import Money


def convert(money, currency, rates):
    """money in currency, at the rates of a RateTable, rounded to currency's minor unit."""
    if money.currency == currency:
        return money.rounded()
    return Money(money.amount * rates.rate(money.currency, currency), currency).rounded()


def convert_all(amounts, currency, rates):
    """The sum of amounts (Money in any currencies) in currency; each is converted and rounded first."""
    total = Money.zero(currency)
    for money in amounts:
        total = total + convert(money, currency, rates)
    return total
