from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.verify_fidelity_mutations import (
    CODEC_FILE,
    MUTATIONS,
    REPOSITORY,
    _replace_exact,
    failed_nodeids,
    make_mutated_copy,
)


def test_mutation_anchors_apply_to_distinct_fidelity_guards():
    source = (REPOSITORY / CODEC_FILE).read_text(encoding="utf-8")
    mutated_versions = [mutation.apply(source) for mutation in MUTATIONS]

    assert len({mutation.name for mutation in MUTATIONS}) == len(MUTATIONS)
    assert all(mutated != source for mutated in mutated_versions)
    assert len(set(mutated_versions)) == len(MUTATIONS)


def test_mutation_anchor_fails_closed_when_source_drifted():
    with pytest.raises(ValueError, match="expected 2"):
        _replace_exact("one target", "target", "mutated", expected=2)


def test_mutated_codec_is_written_only_to_a_disposable_copy(tmp_path: Path):
    source_codec = REPOSITORY / CODEC_FILE
    original_bytes = source_codec.read_bytes()
    destination = tmp_path / "temporary-mutant"
    mutated_hash = make_mutated_copy(REPOSITORY, destination, MUTATIONS[0])

    copied_codec = destination / CODEC_FILE
    assert hashlib.sha256(copied_codec.read_bytes()).hexdigest() == mutated_hash
    assert copied_codec.read_bytes() != original_bytes
    assert source_codec.read_bytes() == original_bytes
    assert not (destination / ".git").exists()


def test_failure_parser_reports_only_explicit_pytest_failure_nodeids():
    output = """\
FAILED tests/test_one.py::test_target[case] - AssertionError
ERROR tests/test_import.py - ModuleNotFoundError
1 failed, 1 error in 0.01s
"""

    assert failed_nodeids(output) == ["tests/test_one.py::test_target[case]"]
