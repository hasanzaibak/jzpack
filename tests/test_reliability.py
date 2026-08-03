import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jzpack import JZPackError, compress, decompress

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> bytes:
    return bytes.fromhex((FIXTURES / name).read_text())


def test_version_one_simple_fixture():
    assert decompress(read_fixture("v1_simple.hex")) == [
        {"id": 1, "meta": {"name": "a"}},
        {"id": 2, "meta": {"name": "b"}},
    ]


def test_version_one_empty_schema_fixture():
    assert decompress(read_fixture("v1_empty_records.hex")) == [{}, {}]


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
