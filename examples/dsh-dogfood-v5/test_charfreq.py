"""Tests for charfreq module."""
import charfreq


def test_empty_string():
    assert charfreq.char_freq("") == {}


def test_single_char():
    assert charfreq.char_freq("a") == {"a": 1}


def test_repeated_char():
    assert charfreq.char_freq("aaa") == {"a": 3}


def test_multiple_chars():
    result = charfreq.char_freq("abac")
    assert result["a"] == 2
    assert result["b"] == 1
    assert result["c"] == 1


def test_top_chars():
    text = "aabbbccc"
    top = charfreq.top_chars(text, 2)
    assert top[0] == ("b", 3)
    assert top[1] == ("c", 3)


def test_top_chars_enough():
    text = "abc"
    top = charfreq.top_chars(text, 5)
    assert len(top) == 3


if __name__ == "__main__":
    test_empty_string()
    test_single_char()
    test_repeated_char()
    test_multiple_chars()
    test_top_chars()
    test_top_chars_enough()
    print("All tests passed!")