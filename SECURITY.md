# Security policy

## Reporting a vulnerability

Please do not report security vulnerabilities in public issues. Contact the project maintainers
privately through the security contact configured for the repository, including a minimal
reproduction and the affected version.

JZPK data may be untrusted. Consumers should use `max_output_size` and `max_records` when decoding
payloads received from external systems, and should treat decompressed records as untrusted input.

## Scope

Security reports are especially useful for decompression bombs, malformed-payload crashes,
excessive memory use, parser differentials, and cross-language format inconsistencies.
