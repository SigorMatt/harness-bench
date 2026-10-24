from envfile import dumps, parse


def test_simple_values_are_written_as_they_are():
    assert dumps({"HOST": "db.local", "PORT": "5432"}) == "HOST=db.local\nPORT=5432\n"


def test_other_values_are_quoted_and_read_back():
    values = {"NAME": "Ada Lovelace", "QUOTE": 'say "hi"', "LINES": "a\nb"}
    assert parse(dumps(values)) == values
