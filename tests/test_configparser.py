import configparser

from morgan.utils import ListExtendingOrderedDict


def assert_value(ini: str, value2: str):
    parser = configparser.ConfigParser(strict=False, dict_type=ListExtendingOrderedDict)
    parser.read_string(ini)
    value = parser.get("requirements", "key")
    assert value == value2


def test_req_1():
    ini = """\
[requirements]
key = value1
key = value2
"""
    assert_value(ini, "value1\nvalue2")


def test_req_2():
    ini = """\
[requirements]
key = value1

key = value2
"""
    assert_value(ini, "value1\n\nvalue2")


def test_req_3():
    ini = """\
[requirements]
key = value1
key = value2

[requirements]
key = value3
key = value4
"""
    assert_value(ini, "value1\nvalue2\n\nvalue3\nvalue4")


def test_req_4():
    ini = """\
[requirements]
key = value1
key = value2
[requirements]
key = value3
key = value4
"""
    assert_value(ini, "value1\nvalue2\nvalue3\nvalue4")


def test_req_5():
    ini = """\
[requirements]
key = value1
key = value2


[requirements]
key = value3
key = value4
"""
    assert_value(ini, "value1\nvalue2\n\n\nvalue3\nvalue4")


def assert_value2(ini: str, value2: str):
    parser = configparser.ConfigParser(
        strict=False,
        dict_type=ListExtendingOrderedDict,
        inline_comment_prefixes=("#",),
    )
    parser.read_string(ini)
    value = parser.get("requirements", "key")
    assert value == value2


def test_comment_1():
    ini = """\
[requirements]
key = value1    # comment 1
key = value2    # comment 2
"""
    assert_value2(ini, "value1\nvalue2")


def test_comment_2():
    ini = """\
[requirements]
key = value1#
key = value2    # comment 2
"""
    assert_value2(ini, "value1#\nvalue2")
