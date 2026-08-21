# crap4swift

Use `crap4swift` for Swift CRAP verification.

1. Run `crap4swift --help` before first use.
2. Run the complete Swift unit test suite with code coverage.
3. Run the gate with `--fail-over 6`.
4. Treat exit `1` as a test, parser, or coverage failure. Do not report it as a quality pass.
5. Treat exit `2` as a CRAP limit failure.
