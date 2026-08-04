import io
import os
from pathlib import Path

import pytest

import jzpack.compressor as compressor_module
from jzpack import JZPackCompressor, ResourceLimitError, compress


@pytest.fixture
def compressor() -> JZPackCompressor:
    return JZPackCompressor()


def test_path_based_round_trip_accepts_strings_and_returns_byte_count(
    compressor: JZPackCompressor, tmp_path: Path
) -> None:
    records = [{"id": index, "status": "ok"} for index in range(5)]
    path = tmp_path / "records.jzpk"

    written = compressor.compress_to_file(records, str(path))

    assert isinstance(written, int)
    assert written == path.stat().st_size
    assert compressor.decompress_from_file(str(path)) == records


def test_pathlib_path_round_trip(compressor: JZPackCompressor, tmp_path: Path) -> None:
    records = [{"id": 1, "meta": {"source": "test"}}]
    path = tmp_path / "records.jzpk"

    compressor.compress_to_file(records, path)

    assert compressor.decompress_from_file(path) == records


def test_existing_destination_is_replaced_atomically(compressor: JZPackCompressor, tmp_path: Path) -> None:
    path = tmp_path / "records.jzpk"
    path.write_bytes(compress([{"version": "old"}]))
    replacement = [{"version": "new", "count": 2}]

    compressor.compress_to_file(replacement, path)

    assert compressor.decompress_from_file(path) == replacement
    assert path.read_bytes() == compress(replacement)


def test_atomic_replacement_failure_preserves_destination_and_cleans_temp_file(
    compressor: JZPackCompressor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "records.jzpk"
    previous = compress([{"version": "old"}])
    path.write_bytes(previous)
    replacement = [{"version": "new"}]
    replace_calls: list[tuple[str, str]] = []

    def fail_replace(source: str, destination: str) -> None:
        replace_calls.append((source, destination))
        raise OSError("injected replace failure")

    monkeypatch.setattr(compressor_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        compressor.compress_to_file(replacement, path)

    assert path.read_bytes() == previous
    assert replace_calls
    assert Path(replace_calls[0][0]).parent == path.parent
    assert Path(replace_calls[0][1]) == path
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_write_failure_during_temp_write_preserves_destination_and_cleans_temp_file(
    compressor: JZPackCompressor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "records.jzpk"
    previous = compress([{"version": "old"}])
    path.write_bytes(previous)

    class FailingWriter:
        def __init__(self, file_descriptor: int) -> None:
            self._file_descriptor = file_descriptor

        def __enter__(self) -> "FailingWriter":
            return self

        def __exit__(self, *_: object) -> None:
            os.close(self._file_descriptor)

        def write(self, _: bytes) -> int:
            raise OSError("injected write failure")

    def fail_fdopen(file_descriptor: int, mode: str) -> FailingWriter:
        assert mode == "wb"
        return FailingWriter(file_descriptor)

    monkeypatch.setattr(compressor_module.os, "fdopen", fail_fdopen)

    with pytest.raises(OSError, match="injected write failure"):
        compressor.compress_to_file([{"version": "new"}], path)

    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_compression_failure_does_not_touch_destination_or_create_temp_file(
    compressor: JZPackCompressor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "records.jzpk"
    previous = b"previous bytes"
    path.write_bytes(previous)

    def fail_compression(_: object) -> bytes:
        raise RuntimeError("injected compression failure")

    monkeypatch.setattr(compressor, "compress", fail_compression)

    with pytest.raises(RuntimeError, match="injected compression failure"):
        compressor.compress_to_file([{"version": "new"}], path)

    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_file_like_compression_uses_current_position_and_leaves_stream_open(
    compressor: JZPackCompressor,
) -> None:
    records = [{"id": 1}, {"id": 2}]
    expected = compressor.compress(records)
    stream = io.BytesIO(b"prefix-and-trailing-data")
    position = len(b"prefix-")
    stream.seek(position)

    written = compressor.compress_to_file(records, stream)

    assert written == len(expected)
    assert stream.getvalue()[position : position + written] == expected
    assert stream.tell() == position + written
    assert not stream.closed


def test_file_like_decompression_uses_current_position_and_leaves_stream_open(
    compressor: JZPackCompressor,
) -> None:
    records = [{"id": 1}, {"id": 2}]
    payload = compressor.compress(records)
    prefix = b"ignored-prefix"
    stream = io.BytesIO(prefix + payload)
    stream.seek(len(prefix))

    assert compressor.decompress_from_file(stream) == records
    assert stream.tell() == len(prefix) + len(payload)
    assert not stream.closed


def test_text_streams_are_rejected_as_non_binary(compressor: JZPackCompressor) -> None:
    with pytest.raises(TypeError, match="binary"):
        compressor.compress_to_file([{"id": 1}], io.StringIO())
    with pytest.raises(TypeError, match="binary"):
        compressor.decompress_from_file(io.StringIO("not a JZPK payload"))


@pytest.mark.parametrize("records", [[], [{}, {}]])
def test_empty_records_work_through_path_and_file_like_routes(
    compressor: JZPackCompressor, tmp_path: Path, records: list[dict[str, object]]
) -> None:
    path = tmp_path / "empty.jzpk"
    stream = io.BytesIO()

    compressor.compress_to_file(records, path)
    compressor.compress_to_file(records, stream)

    assert compressor.decompress_from_file(path) == records
    stream.seek(0)
    assert compressor.decompress_from_file(stream) == records
    assert not stream.closed


@pytest.mark.parametrize("route", ["path", "stream"])
def test_file_helpers_preserve_decompression_limits(
    compressor: JZPackCompressor, tmp_path: Path, route: str
) -> None:
    records = [{"id": index} for index in range(10)]
    path = tmp_path / "limited.jzpk"
    compressor.compress_to_file(records, path)

    if route == "path":
        source = path
    else:
        source = io.BytesIO(path.read_bytes())

    with pytest.raises(ResourceLimitError, match="max_records"):
        compressor.decompress_from_file(source, max_records=5)

    if route == "path":
        source = path
    else:
        source = io.BytesIO(path.read_bytes())
    with pytest.raises(ResourceLimitError, match="max_output_size"):
        compressor.decompress_from_file(source, max_output_size=1)
