# Contributing to jzpack

Thanks for helping make jzpack reliable and interoperable.

## Before opening a pull request

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m build
```

Changes to the binary format must update `FORMAT.md`, include compatibility tests, and explain
whether older readers can decode the new output. Performance changes should include the benchmark
dataset and the hardware/dependency versions used.

Keep the public API small, preserve lossless round trips, and prefer clear typed exceptions over
implementation-specific errors.

## Pull requests

Describe the user problem, the behavior change, tests added, and any compatibility or performance
trade-offs. Small focused pull requests are easier to review.
