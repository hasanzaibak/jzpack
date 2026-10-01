"""Independent exact-value comparisons for the documented fidelity contract."""

import struct


def is_faithful(actual, expected) -> bool:
    """Compare supported values without Python's cross-type or float equality rules."""
    if type(actual) is not type(expected):
        return False

    value_type = type(expected)
    if value_type is float:
        return struct.pack(">d", actual) == struct.pack(">d", expected)
    if value_type is list:
        return len(actual) == len(expected) and all(is_faithful(a, e) for a, e in zip(actual, expected))
    if value_type is dict:
        if actual.keys() != expected.keys() or any(type(key) is not str for key in expected):
            return False
        return all(is_faithful(actual[key], value) for key, value in expected.items())
    if value_type in (type(None), bool, int, str, bytes):
        return actual == expected
    return False
