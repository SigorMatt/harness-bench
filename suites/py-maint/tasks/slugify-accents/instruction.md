`slugify()` in textkit drops accented letters instead of turning them into
their base letter: `slugify("Crème Brûlée")` returns `"crme-brle"` instead of
`"creme-brulee"`.

The failing test is `tests/test_slug.py::test_accents_are_transliterated`.
Fix the bug without changing the function's signature or its other behaviour.
