# CI validation

The GitHub Actions workflow checks package behavior on the supported Python versions and the
filesystem contracts on three operating systems. Runtime dependency pins are explicit in the
minimum-version job so the normal editable install cannot silently restore newer packages after the
minimums are selected.

| Job | Environment | Checks |
|---|---|---|
| `test` | Ubuntu, Python 3.10–3.14, latest versions satisfying project constraints | Full pytest suite and source/test compilation. `pip install -e ".[dev]"` resolves the latest compatible runtime and development dependencies available when the job runs. |
| `minimum-dependencies` | Ubuntu, Python 3.10, `msgpack==1.0.0`, `zstandard==0.21.0` | Full pytest suite and source/test compilation. The exact runtime pins are installed with `--no-deps` after the editable development install, then checked through installed package metadata. |
| `platform-contracts` | macOS and Windows; Python 3.13 | Full pytest suite. The Ubuntu `test` matrix already runs the full suite on Python 3.10–3.14; these additional jobs check platform-specific behavior without duplicating a Linux run. |
| `quality` | Ubuntu, Python 3.12, latest versions satisfying project constraints | Ruff lint, wheel/source distribution build, and cache-sentinel source-archive exclusion checks. |

All jobs use read-only repository permissions. The Python support range comes from
`requires-python = ">=3.10"` and the declared 3.10–3.14 classifiers in `pyproject.toml`.

## Local checks

Run the standard development checks from a clean environment:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q jzpack tests
python -m ruff check .
python -m build --outdir /tmp/jzpack-dist
```

To reproduce the minimum-runtime-dependency lane, use a Python 3.10 virtual environment and run:

```bash
python -m pip install -e ".[dev]"
python -m pip install --no-deps "msgpack==1.0.0" "zstandard==0.21.0"
python -c "from importlib.metadata import version; assert version('msgpack') == '1.0.0'; assert version('zstandard') == '0.21.0'"
python -m pytest -q
```

`--no-deps` is important here: it installs the exact runtime pins without asking pip to resolve the
package's lower-bounded requirements again. Use a fresh environment so unrelated installed packages
cannot affect the result.

## Local validation snapshot

At the initial CI implementation checkpoint, the full suite passed with 128 tests in both local environments:

- `/tmp/jzpack-ci-20261001-py312/bin/python` ran Python 3.12.13 with `msgpack==1.2.3` and
  `zstandard==0.25.0`; pytest, Ruff, `compileall`, and a wheel/source build to
  `/tmp/jzpack-ci-20261001-dist` passed.
- `/tmp/jzpack-ci-20261001-minimum-py311/bin/python` ran Python 3.11.15 with
  `msgpack==1.0.0` and `zstandard==0.21.0`; `msgpack.Packer` came from the pure-Python
  `msgpack.fallback` backend, and pytest plus `compileall` passed.
- Ruby's standard YAML parser accepted `.github/workflows/ci.yml`, and `git diff --check` passed.

Python 3.10 was not available locally. That local minimum-version run therefore did not establish
the Python 3.10 result, nor behavior with msgpack's native extension.

## Hosted validation checkpoint

The [completed CI run](https://github.com/hasanzaibak/jzpack/actions/runs/36896532437) for
`3e87f770be076cac166caf7fd35de22c81c3782b` passed all eight jobs: the Linux Python 3.10–3.13
matrix, exact-minimum runtime dependencies on Python 3.10, macOS and Windows on Python 3.13,
and quality/build checks. This checkpoint includes the combined 383-test suite. Future release
commits require their own successful CI run; this result does not clear a different source snapshot.

Local checks do not execute GitHub-hosted runners for all supported Python versions or operating
systems. In particular, a local macOS/Linux run cannot establish Windows filesystem behavior; the
`platform-contracts` matrix supplies that hosted coverage, while the Ubuntu `test` matrix provides
the Linux coverage. Before a workflow run completes, those hosted matrix results remain unverified.

## Release 0.5.1 checkpoint

All eight [exact-release CI jobs](https://github.com/hasanzaibak/jzpack/actions/runs/36940128021)
passed for `bb59b4cc3c4e64c8e1621d49afb2be78fdb1c07f`, including the 404-test suite on the
supported hosted Python/platform lanes and exact minimum dependencies on Python 3.10.
This clears that release snapshot only; subsequent runtime changes require their own checks.

## Release 0.5.2 checkpoint

All eight [exact-release CI jobs](https://github.com/hasanzaibak/jzpack/actions/runs/36955599304)
passed for `a3c9f557ae86e22d80ab2829ec1d8fc72d95a806`. Every one of the seven
test lanes passed all 468 tests, including Linux Python 3.10–3.13, exact minimum
dependencies on Python 3.10, macOS, and Windows. The quality job passed lint and
distribution builds. Deprecated action-runtime annotations remain maintenance
work; this successful run does not verify a future action upgrade.

## Release 0.5.3 checkpoint

All eight [exact-release jobs](https://github.com/hasanzaibak/jzpack/actions/runs/37095316960)
passed for `eca4fea9977eb8785b95df9db13c2e1af6d297fb`, with 469 tests in each of
seven test lanes. The refreshed checkout/setup actions and cache-sentinel archive
guard passed. The [publication record](evidence/release-0.5.3.json) retains artifact
hashes and the successful upload/download action and protected publishing checks.

## Python 3.14 matrix checkpoint

The [completed CI run](https://github.com/hasanzaibak/jzpack/actions/runs/37098337150)
passed all nine jobs for `c6a35ce6d08197dda48d298f86712d61b8516595`. Its eight test
lanes each passed all 469 tests: Linux Python 3.10–3.14, exact minimum runtime
dependencies on Python 3.10, and macOS/Windows on Python 3.13. The quality lane passed
lint, distribution build, and cache-sentinel checks. This run records the stable Python
3.14 matrix expansion; later source changes still require their own exact-commit CI run.
