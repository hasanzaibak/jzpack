# Selective fidelity mutation checks

This opt-in check asks whether critical codec regressions are detected independently by a focused
encoder test and a public v3-container round trip. It applies three deliberately faulty changes to
disposable copies of the repository. The checked-out runtime source is read but never mutated.
Each temporary copy is removed after its pytest run.

Run from the repository root with an environment containing the project test dependencies:

```bash
PYTHONPATH=. python tools/verify_fidelity_mutations.py \
  --json-out docs/evidence/fidelity-mutations-current.json
```

The command first runs all six selected tests unchanged. It then applies each mutation separately
and requires the corresponding unit test and public-container test to fail by their exact pytest
node IDs. An import/setup error or an unrelated failure does not count as a killed mutant. The
mutation anchors require an exact number of source matches and fail closed if the codec changes.

## Results on the release implementation

At source snapshot `267730398a1bd602efe4bb3324877655c1cdc204` plus the fidelity-mutation test/tool
changes, all six baseline cases passed in both environments. The focused tests exercise direct
`ColumnEncoder`/`DeltaEncoder` behavior; the separate public tests exercise `compress`/`decompress`
containers. Every mutant was killed by both kinds of test. No mutant survived.

| Deliberate defect | Focused unit test | Public-container test |
|---|---|---|
| Use Python float equality instead of binary64-bit equality | `test_rle_preserves_exact_type_and_float_bits[signed-zero-distinction]` | `test_default_codec_preserves_reproduced_values[signed-zero-rle]` |
| Bypass the scalar exact-type guard in both RLE comparisons | `test_rle_preserves_exact_type_and_float_bits[bool-int-distinction]` | `test_default_codec_preserves_reproduced_values[bool-and-int-rle]` |
| Permit floating values and deltas in the DELTA writer | `test_direct_delta_encoder_still_rejects_malformed_inputs[unsafe-float-cancellation]` | `test_default_codec_preserves_reproduced_values[float-cancellation]` |

The float mutation merges positive and negative zero; the scalar mutation merges `True` and `1`; the
DELTA mutation reproduces cancellation loss in `[1e16, 1.0] * 60`. Each defect is rejected directly
and changes or breaks the corresponding public v3 result.

| Environment | Dependency backend | Baseline | Mutant outcomes | Retained report |
|---|---|---:|---|---|
| Python 3.12.13, msgpack 1.2.3, zstandard 0.25.0 | `msgpack._cmsgpack` | 6 passed | 3 killed by both tests | [current environment report](evidence/fidelity-mutations-python312.json) |
| Python 3.11.15, msgpack 1.0.0, zstandard 0.21.0 | `msgpack.fallback` | 6 passed | 3 killed by both tests | [minimum-dependency report](evidence/fidelity-mutations-python311-minimum.json) |

The reports retain the source snapshot digest, baseline codec hash, mutated codec hashes, runtime,
and exact failing test node IDs. Baseline `jzpack/encoders.py` SHA-256 was
`9793fcd8d0bf0e7b848b89ebdd48fb30329a9f89225bcdefb35d3525f040db69`; the complete selected-source
snapshot digest was `961828e9a8eb3257f68b62b9cd93a9239b58a8744fb59af980e31dd65a857113`.

This is a deliberately small mutation set, not a general mutation score or proof that every
possible fidelity defect is covered. Python 3.10 was not available locally for this run.

## Fourth-wave rerun

After the reviewed RLE and ASCII optimizations, checkpoint
`5a3ee0ea048c9eb794870ced2e9c79d61910b700` reran the same three faults in both
environments. All six baseline cases passed; each mutant again failed its
separate unit and public-container cases. The retained
[current report](evidence/wave-4-mutations-current.json) and
[minimum report](evidence/wave-4-mutations-minimum.json) record encoder SHA-256
`7a24623c54f0f21de910bbbbb267ed051efd29f2507354063f0675d41e048c87`
and selected-source snapshot SHA-256
`ab679096e41212eefd8d0f176ddbb67282446df6470771530e37dbb2233c8baa`.
The later Git/provenance tests and release metadata do not change these selected
codec/test sources. This remains a targeted assertion check, not package-wide
mutation coverage.
