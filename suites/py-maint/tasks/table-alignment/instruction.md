In mdtable, right-aligned columns are padded on the wrong side: the text ends
up at the left of its cell. For example,

```python
render(["Item", "Price"], [["Tea", "3.50"], ["Cake", "12.00"]], align=["left", "right"])
```

shows `3.50` at the left of its cell instead of the right, unlike the README's
example.

Fix this bug in `mdtable/render.py`. Change nothing else: no other file
(tests included), and nothing else in that file, even where something looks
worth tidying. Leave no new files in the repository.
