`shopcart/core.py` mixes the cart classes with money arithmetic. Move the four
money helpers, `round_money`, `apply_discount`, `add_tax` and `parse_money`
(and the `CENT` constant they use), into a new module, `shopcart/money.py`, and
make the rest of the package use them from there.

Code elsewhere imports these helpers from `shopcart.core`, so that must keep
working and give the very same functions. Don't change any behaviour.
