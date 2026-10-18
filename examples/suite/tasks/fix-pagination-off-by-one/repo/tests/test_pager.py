from pager import page, page_count


def test_first_page_starts_at_first_item():
    assert page([1, 2, 3, 4, 5], 1, 2) == [1, 2]


def test_page_count_rounds_up():
    assert page_count(5, 2) == 3
