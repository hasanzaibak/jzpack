# Package releases

The repository publishes through `.github/workflows/python-publish.yml`: publishing a GitHub
Release builds wheel and source distributions, then uses PyPI trusted publishing in the `pypi`
environment. Never bypass an environment approval or substitute an unreviewed local upload.

## Release gates

1. Prepare a release branch from reviewed main; update `jzpack.__version__`, the changelog,
   public behavior documentation, and benchmark claims supported by retained raw reports.
2. Obtain an independent review of the exact diff. Run the full suite with current and minimum
   runtime dependencies, lint, compilation, distribution builds, and a built-wheel import and
   exact round-trip smoke check outside the source checkout.
3. Commit, merge, and push with authorization. Require every hosted CI job to pass on the exact
   release commit, including supported platforms and the Python 3.10 minimum-dependency lane.
4. Create and push the specific version tag at that commit, then publish a GitHub Release with
   reviewed notes. Monitor the resulting build and PyPI publishing jobs; report any required
   environment approval without bypassing it.
5. Verify the official PyPI version, distribution hashes, and installation into a fresh environment.
   Smoke-test the installed package outside the checkout, with no source `PYTHONPATH` override.
6. Record the release URL, workflow evidence, and installed-package verification in the delivery
   ledger. Keep remaining performance and coverage gaps explicit.

## Source distribution contents

The root `.gitignore` and Hatch sdist exclusions omit `.hypothesis` and `.ruff_cache` from source
archives. Before release, build with cache sentinel files present and confirm that the archive
excludes them while retaining the package, `py.typed`, `LICENSE`, `README.md`, tests, and required
fixtures.

Version 0.5.0 retains the v3 wire format and historical float DELTA reads. Its safer encoder cannot
restore values already lost by an older encoder. The new bounded `write_records` API complements
the existing APIs; `StreamingCompressor` still accumulates its archive in memory. Resource limits
and atomic path output are documented in [WRITER.md](WRITER.md).

Performance comparisons are workload-specific. Publication does not establish universal speed,
memory, compression, compatibility, or adoption claims.
