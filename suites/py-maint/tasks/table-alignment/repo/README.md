# mdtable

Render rows as Markdown tabels.

```python
from mdtable import render

print(render(["Item", "Price"], [["Tea", "3.50"], ["Cake", "12.00"]], align=["left", "right"]))
```

```
| Item | Price |
| :--- | ----: |
| Tea  |  3.50 |
| Cake | 12.00 |
```

On the command line, CSV on standard input:

```
python -m mdtable.cli --align lr < prices.csv
```

Columns are left-aligned unless `align` says otherwise. Centred text that
cannot be split evenly gets the extra space on its right.
