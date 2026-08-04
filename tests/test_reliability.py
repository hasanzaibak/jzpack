import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jzpack import JZPackError, UnsupportedVersionError, compress, decompress


@pytest.mark.parametrize("version", [1, 2])
def test_retired_standalone_versions_are_rejected(version: int):
    with pytest.raises(UnsupportedVersionError):
        decompress(b"JZPK" + bytes([version]) + b"\x00" * 100)


def test_output_is_deterministic_across_processes():
    script = (
        "from hashlib import sha256; "
        "from jzpack import compress; "
        "print(sha256(compress([{'id': i, 'status': 'ok'} for i in range(1000)])).hexdigest())"
    )
    repository = str(Path(__file__).parents[1])
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (repository, environment.get("PYTHONPATH")) if value
    )
    first = subprocess.check_output([sys.executable, "-c", script], cwd=repository, env=environment)
    second = subprocess.check_output([sys.executable, "-c", script], cwd=repository, env=environment)
    assert first == second


def test_truncated_payloads_raise_controlled_errors():
    payload = compress([{"id": index, "value": "x"} for index in range(20)])
    for end in range(len(payload)):
        with pytest.raises(JZPackError):
            decompress(payload[:end])


@settings(max_examples=200, deadline=None)
@given(st.binary(max_size=512))
def test_arbitrary_bytes_do_not_leak_parser_exceptions(data):
    try:
        decompress(data)
    except JZPackError:
        pass
