`page()` in `pager.py` skips the first items: asking for page 1 returns the
second page. Pages are meant to be 1-based.

Fix the bug without changing any function signatures. The failing test is
`tests/test_pager.py::test_first_page_starts_at_first_item`.
