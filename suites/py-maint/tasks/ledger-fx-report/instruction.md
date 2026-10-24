The balance report converts some accounts to the wrong amount.

With a rates file that only has

```
EUR,USD,1.10
```

(one euro is worth 1.10 dollars), a dollar account that holds 110.00 USD shows
as 121.00 EUR in a balance report in euros. It should show 100.00 EUR. The
same report in dollars shows euro accounts correctly.

Find the cause and fix it. Don't change how reports are laid out or how
anything else is calculated.
