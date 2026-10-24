# ledgerkit

Plain-text double-entry bookkeeping in several currencies.

```
account Assets:Bank:EUR   EUR
account Assets:Bank:USD   USD
account Income:Salary     EUR

2024-01-31 Salary
    Assets:Bank:EUR     3000.00 EUR
    Income:Salary      -3000.00 EUR
```

```
python -m ledgerkit balance journal.txt --rates rates.csv --currency EUR
python -m ledgerkit income journal.txt --rates rates.csv --currency EUR --from 2024-01-01
python -m ledgerkit register journal.txt Assets:Bank:EUR
```

## Exchange rates

A rates file states each rate once, as `BASE,QUOTE,RATE`: `EUR,USD,1.10` says
one euro is worth 1.10 dollars. Reports convert in either direction, and
through a third currency when no rate between two currencies is stated.

## Layout

- `ledgerkit/model`: accounts, postings, transactions and the journal
- `ledgerkit/parse`: the journal format and bank CSV exports
- `ledgerkit/fx`: exchange rates and conversion
- `ledgerkit/reports`: balance, income statement and register, and their tables
- `ledgerkit/rules`: sorting imported transactions into accounts
- `ledgerkit/storage`: saving journals as JSON
