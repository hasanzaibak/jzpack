import random
import string
import tempfile
from pathlib import Path

import pytest

from jzpack import JZPackCompressor, StreamingCompressor, compress, decompress


class TestBasicCompression:
    def test_empty_list(self):
        data = []
        assert decompress(compress(data)) == data

    def test_single_record(self):
        data = [{"key": "value"}]
        assert decompress(compress(data)) == data

    def test_multiple_records(self):
        data = [{"id": i, "name": f"item_{i}"} for i in range(100)]
        assert decompress(compress(data)) == data

    def test_nested_objects(self):
        data = [
            {
                "user": {"profile": {"name": "John", "settings": {"theme": "dark"}}},
                "timestamp": "2024-01-01",
            }
            for _ in range(100)
        ]
        assert decompress(compress(data)) == data

    def test_deeply_nested(self):
        data = [{"level1": {"level2": {"level3": {"level4": {"value": i}}}}} for i in range(100)]
        assert decompress(compress(data)) == data


class TestDataTypes:
    def test_mixed_types(self):
        data = [
            {
                "string": "hello",
                "integer": 42,
                "float": 3.14,
                "boolean": True,
                "null": None,
                "list": [1, 2, 3],
            }
            for _ in range(100)
        ]
        assert decompress(compress(data)) == data

    def test_unicode_content(self):
        data = [
            {
                "japanese": "日本語テスト",
                "emoji": "🎉🚀💻",
                "chinese": "中文测试",
                "arabic": "اختبار عربي",
            }
            for _ in range(100)
        ]
        assert decompress(compress(data)) == data

    def test_special_characters(self):
        data = [
            {
                "quotes": 'He said "hello"',
                "backslash": "path\\to\\file",
                "newline": "line1\nline2",
                "tab": "col1\tcol2",
            }
            for _ in range(100)
        ]
        assert decompress(compress(data)) == data

    def test_large_integers(self):
        data = [{"big": 2**60 + i, "negative": -(2**60 + i)} for i in range(100)]
        assert decompress(compress(data)) == data

    def test_floats_precision(self):
        data = [{"pi": 3.141592653589793, "e": 2.718281828459045} for _ in range(100)]
        assert decompress(compress(data)) == data


class TestCompressionLevels:
    def test_level_1(self):
        data = [{"test": "value"} for _ in range(1000)]
        compressed = compress(data, level=1)
        assert decompress(compressed) == data

    def test_level_19(self):
        data = [{"test": "value"} for _ in range(1000)]
        compressed = compress(data, level=19)
        assert decompress(compressed) == data

    def test_higher_level_smaller_size(self):
        data = [{"test": "value" * 100} for _ in range(1000)]
        size_1 = len(compress(data, level=1))
        size_19 = len(compress(data, level=19))
        assert size_19 <= size_1


class TestFastMode:
    def test_fast_mode_basic(self):
        data = [{"id": i, "name": f"item_{i}"} for i in range(1000)]
        compressed = compress(data, fast=True)
        assert decompress(compressed) == data

    def test_fast_mode_nested(self):
        data = [{"user": {"profile": {"name": f"user_{i}"}}} for i in range(1000)]
        compressed = compress(data, fast=True)
        assert decompress(compressed) == data

    def test_fast_vs_normal_size(self):
        data = [{"status": "OK", "id": i} for i in range(1000)]
        normal_size = len(compress(data, fast=False))
        fast_size = len(compress(data, fast=True))
        assert fast_size >= normal_size


class TestOrderPreservation:
    def test_homogeneous_order(self):
        data = [{"id": i, "value": f"item_{i}"} for i in range(1000)]
        decompressed = decompress(compress(data))
        assert decompressed == data

    def test_heterogeneous_order_simple(self):
        data = [
            {"type": "A", "value_a": 1},
            {"type": "B", "value_b": "x"},
            {"type": "A", "value_a": 2},
            {"type": "B", "value_b": "y"},
        ]
        decompressed = decompress(compress(data))
        assert decompressed == data

    def test_heterogeneous_order_complex(self):
        data = [
            {"type": "A", "a": 1},
            {"type": "B", "b": 2},
            {"type": "C", "c": 3},
            {"type": "A", "a": 4},
            {"type": "B", "b": 5},
            {"type": "C", "c": 6},
            {"type": "A", "a": 7},
        ]
        decompressed = decompress(compress(data))
        assert decompressed == data

    def test_alternating_schemas(self):
        data = []
        for i in range(500):
            if i % 2 == 0:
                data.append({"even": True, "index": i})
            else:
                data.append({"odd": True, "index": i, "extra": "field"})
        decompressed = decompress(compress(data))
        assert decompressed == data

    def test_random_schema_order(self):
        random.seed(42)
        schemas = [
            lambda i: {"type": "user", "id": i},
            lambda i: {"type": "event", "name": f"evt_{i}", "ts": i},
            lambda i: {"type": "log", "level": "INFO", "msg": f"msg_{i}"},
            lambda i: {"type": "metric", "value": i, "unit": "ms", "host": "srv1"},
        ]
        data = [random.choice(schemas)(i) for i in range(1000)]
        decompressed = decompress(compress(data))
        assert decompressed == data

    def test_single_outlier_schema(self):
        data = [{"common": "field", "id": i} for i in range(999)]
        data.insert(500, {"outlier": True, "special": "value"})
        decompressed = decompress(compress(data))
        assert decompressed == data

    def test_many_schemas(self):
        data = []
        for i in range(100):
            record = {"base": i}
            for j in range(i % 10):
                record[f"field_{j}"] = j
            data.append(record)
        decompressed = decompress(compress(data))
        assert decompressed == data


class TestHeterogeneousSchemas:
    def test_mixed_schemas_content(self):
        data = [
            {"type": "A", "value_a": 1},
            {"type": "B", "value_b": "test"},
            {"type": "A", "value_a": 2},
            {"type": "C", "value_c": True, "extra": "field"},
        ]
        decompressed = decompress(compress(data))
        assert decompressed == data

    def test_sparse_fields(self):
        data = []
        for i in range(1000):
            record = {"id": i}
            if i % 2 == 0:
                record["even_field"] = i
            if i % 3 == 0:
                record["triple_field"] = i * 3
            if i % 5 == 0:
                record["five_field"] = "five"
            data.append(record)
        decompressed = decompress(compress(data))
        assert decompressed == data

    def test_optional_nested(self):
        data = []
        for i in range(100):
            record = {"id": i}
            if i % 2 == 0:
                record["meta"] = {"created": "2024-01-01"}
            if i % 3 == 0:
                record["meta"] = {"created": "2024-01-01", "updated": "2024-01-02"}
            data.append(record)
        decompressed = decompress(compress(data))
        assert decompressed == data


class TestCompressorClass:
    def test_compressor_instance(self):
        compressor = JZPackCompressor(compression_level=3)
        data = [{"id": i} for i in range(100)]
        compressed = compressor.compress(data)
        assert compressor.decompress(compressed) == data

    def test_compressor_reuse(self):
        compressor = JZPackCompressor()
        data1 = [{"batch": 1, "id": i} for i in range(100)]
        data2 = [{"batch": 2, "id": i} for i in range(100)]

        compressed1 = compressor.compress(data1)
        compressed2 = compressor.compress(data2)

        assert compressor.decompress(compressed1) == data1
        assert compressor.decompress(compressed2) == data2

    def test_file_operations(self):
        compressor = JZPackCompressor(compression_level=3)
        data = [{"file_test": i} for i in range(1000)]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jzpk"
            compressor.compress_to_file(data, str(path))
            assert path.exists()
            decompressed = compressor.decompress_from_file(str(path))
            assert decompressed == data


class TestStreamingCompressor:
    def test_streaming_basic(self):
        data = [{"id": i, "value": f"item_{i}"} for i in range(1000)]

        streaming = StreamingCompressor(compression_level=3)
        for record in data:
            streaming.add_record(record)

        compressed = streaming.finalize()
        decompressed = decompress(compressed)
        assert decompressed == data

    def test_streaming_batch(self):
        data = [{"id": i, "value": f"item_{i}"} for i in range(1000)]

        streaming = StreamingCompressor()
        streaming.add_batch(data[:500])
        streaming.add_batch(data[500:])

        compressed = streaming.finalize()
        decompressed = decompress(compressed)
        assert decompressed == data

    def test_streaming_mixed_schemas(self):
        data = [{"type": "A", "a": i} if i % 2 == 0 else {"type": "B", "b": i} for i in range(100)]

        streaming = StreamingCompressor()
        for record in data:
            streaming.add_record(record)

        compressed = streaming.finalize()
        decompressed = decompress(compressed)
        assert decompressed == data

    def test_streaming_clear(self):
        streaming = StreamingCompressor()
        streaming.add_batch([{"id": i} for i in range(100)])
        streaming.clear()
        streaming.add_batch([{"new": i} for i in range(50)])

        compressed = streaming.finalize()
        decompressed = decompress(compressed)
        assert decompressed == [{"new": i} for i in range(50)]


class TestIntegrity:
    def test_full_integrity_realistic(self):
        random.seed(42)
        statuses = ["active", "inactive", "pending", "error", "archived"]
        tags_pool = [f"tag_{i}" for i in range(50)]

        data = []
        for i in range(10000):
            data.append(
                {
                    "id": i,
                    "user_id": random.randint(1000, 9999),
                    "username": f"user_{random.randint(1, 1000)}",
                    "bio": "".join(random.choices(string.ascii_letters, k=50)),
                    "metrics": {
                        "login_count": random.randint(0, 100),
                        "is_pro": random.choice([True, False]),
                    },
                    "status": random.choice(statuses),
                    "tags": random.sample(tags_pool, 3),
                }
            )

        decompressed = decompress(compress(data))
        assert len(decompressed) == len(data)
        for i in range(len(data)):
            assert decompressed[i] == data[i], f"Mismatch at index {i}"

    def test_full_integrity_high_cardinality(self):
        random.seed(42)
        data = []
        for i in range(10000):
            data.append(
                {
                    "id": i,
                    "uuid": "".join(random.choices(string.hexdigits, k=32)),
                    "email": f"{''.join(random.choices(string.ascii_lowercase, k=10))}@example.com",
                    "score": random.random() * 1000,
                }
            )

        decompressed = decompress(compress(data))
        assert len(decompressed) == len(data)
        for i in range(len(data)):
            assert decompressed[i] == data[i], f"Mismatch at index {i}"

    def test_integrity_fast_mode(self):
        random.seed(42)
        data = [{"id": i, "value": random.random()} for i in range(10000)]

        decompressed = decompress(compress(data, fast=True))
        assert len(decompressed) == len(data)
        for i in range(len(data)):
            assert decompressed[i] == data[i], f"Mismatch at index {i}"


class TestLargeDataset:
    def test_50k_records_homogeneous(self):
        data = [{"id": i, "service": "api-gateway", "status": "OK", "latency": i % 100} for i in range(50000)]
        decompressed = decompress(compress(data))
        assert len(decompressed) == len(data)
        for i in range(len(data)):
            assert decompressed[i] == data[i], f"Mismatch at index {i}"

    def test_50k_records_heterogeneous(self):
        data = []
        for i in range(50000):
            if i % 3 == 0:
                data.append({"type": "event", "id": i})
            elif i % 3 == 1:
                data.append({"type": "log", "id": i, "level": "INFO"})
            else:
                data.append({"type": "metric", "id": i, "value": i % 1000})
        decompressed = decompress(compress(data))
        assert len(decompressed) == len(data)
        for i in range(len(data)):
            assert decompressed[i] == data[i], f"Mismatch at index {i}"


class TestEdgeCases:
    def test_empty_strings(self):
        data = [{"empty": "", "also_empty": ""} for _ in range(100)]
        assert decompress(compress(data)) == data

    def test_empty_nested(self):
        data = [{"nested": {}} for _ in range(100)]
        assert decompress(compress(data)) == data

    def test_single_field(self):
        data = [{"x": i} for i in range(1000)]
        assert decompress(compress(data)) == data

    def test_many_fields(self):
        data = [{f"field_{j}": j for j in range(50)} for _ in range(100)]
        assert decompress(compress(data)) == data

    def test_long_keys(self):
        long_key = "a" * 200
        data = [{long_key: i} for i in range(100)]
        assert decompress(compress(data)) == data

    def test_long_values(self):
        data = [{"content": "x" * 10000} for _ in range(10)]
        assert decompress(compress(data)) == data

    def test_none_values(self):
        data = [{"a": None, "b": None, "c": i} for i in range(100)]
        assert decompress(compress(data)) == data

    def test_boolean_columns(self):
        data = [{"flag": i % 2 == 0, "id": i} for i in range(1000)]
        assert decompress(compress(data)) == data

    def test_mixed_numeric_types(self):
        data = [{"int": i, "float": float(i) + 0.5} for i in range(1000)]
        assert decompress(compress(data)) == data


class TestHeaderValidation:
    def test_magic_header(self):
        data = [{"test": "value"}]
        compressed = compress(data)
        assert compressed[:4] == b"JZPK"

    def test_version(self):
        data = [{"test": "value"}]
        compressed = compress(data)
        assert compressed[4] == 1

    def test_invalid_magic_raises(self):
        with pytest.raises(ValueError, match="missing magic header"):
            decompress(b"BAAD\x01" + b"\x00" * 100)

    def test_invalid_version_raises(self):
        with pytest.raises(ValueError, match="Unsupported version"):
            decompress(b"JZPK\x99" + b"\x00" * 100)


class TestEncodingStrategies:
    def test_rle_triggered(self):
        data = [{"status": "OK", "id": i} for i in range(1000)]
        compressed = compress(data)
        decompressed = decompress(compressed)
        assert decompressed == data

    def test_delta_triggered(self):
        data = [{"seq": i, "name": "test"} for i in range(1000)]
        compressed = compress(data)
        decompressed = decompress(compressed)
        assert decompressed == data

    def test_dictionary_triggered(self):
        services = ["auth", "api", "db", "cache", "queue"]
        data = [{"service": services[i % 5], "id": i} for i in range(1000)]
        compressed = compress(data)
        decompressed = decompress(compressed)
        assert decompressed == data

    def test_raw_fallback(self):
        random.seed(42)
        data = [{"random": random.random(), "id": i} for i in range(1000)]
        compressed = compress(data)
        decompressed = decompress(compressed)
        assert decompressed == data
