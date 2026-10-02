"""Reproducibility contracts for writer diagnostics in an ordinary checkout."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture
def diagnostic_checkout(tmp_path, request):
    script = request.param
    directory = tmp_path / "ordinary-checkout" / "benchmarks"
    directory.mkdir(parents=True)
    for filename in (script, "corpus_wave3.py", "benchmark_corpus_wave3.py"):
        shutil.copyfile(REPOSITORY / "benchmarks" / filename, directory / filename)
    spec = importlib.util.spec_from_file_location("isolated_writer_diagnostic", directory / script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = {name: sys.modules.get(name) for name in ("wave3_corpus", "writer_ascii_corpus")}
    try:
        spec.loader.exec_module(module)
        yield module, directory
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


@pytest.mark.parametrize(
    "diagnostic_checkout", ["compare_writer_ascii.py", "profile_writer_cpu.py"], indirect=True
)
def test_writer_diagnostic_loads_corpus_from_its_own_checkout(diagnostic_checkout):
    module, directory = diagnostic_checkout
    loader = getattr(module, "_load_corpus", None) or module._corpus_module
    corpus = loader()
    assert Path(corpus.__file__).resolve() == directory / "corpus_wave3.py"
    assert corpus.build_records("schema-diverse", 2, corpus.DEFAULT_SEED)


@pytest.mark.parametrize(
    "diagnostic_checkout", ["compare_writer_ascii.py", "profile_writer_cpu.py"], indirect=True
)
@pytest.mark.parametrize("changed_source", ["corpus_wave3.py", "benchmark_corpus_wave3.py"])
def test_writer_diagnostic_rejects_changed_frozen_sources(diagnostic_checkout, changed_source):
    module, directory = diagnostic_checkout
    source = directory / changed_source
    source.write_bytes(source.read_bytes() + b"\n")
    loader = getattr(module, "_load_corpus", None) or module.run
    with pytest.raises(RuntimeError, match="wave-3 .*(source|harness)"):
        loader()


@pytest.mark.skipif(shutil.which("git") is None, reason="checkout provenance test requires Git")
@pytest.mark.parametrize("diagnostic_checkout", ["compare_writer_ascii.py"], indirect=True)
def test_frozen_source_hashes_survive_a_checkout_with_crlf_conversion(diagnostic_checkout):
    module, directory = diagnostic_checkout
    checkout = directory.parent
    shutil.copyfile(REPOSITORY / ".gitattributes", checkout / ".gitattributes")
    command = ["git", "-c", "core.autocrlf=true", "-C", str(checkout)]
    for arguments in (["init", "--quiet"], ["add", ".gitattributes", "benchmarks"]):
        subprocess.run(command + arguments, check=True, capture_output=True, timeout=15)
    sources = list(directory.glob("*.py"))
    for source in sources:
        source.unlink()
    subprocess.run(command + ["checkout-index", "--all", "--force"], check=True, capture_output=True, timeout=15)

    assert all(b"\r\n" not in source.read_bytes() for source in sources)
    corpus = module._load_corpus()
    assert Path(corpus.__file__).resolve() == directory / "corpus_wave3.py"
