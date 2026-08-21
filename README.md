# crap4swift

`crap4swift` calculates strict function-level CRAP scores for Swift source. It uses a Tree-sitter Swift syntax tree and executable-line coverage from SwiftPM or LLVM JSON.

```bash
pipx install git+https://github.com/lukasa1993/crap4swift.git
crap4swift --fail-over 6
```

The default command is:

```bash
swift test --enable-code-coverage && swift test --show-codecov-path
```

Missing, stale, empty, or unmatched coverage is an error by default. Use `--allow-missing-coverage` only for exploratory analysis.

Exit status: `0` pass, `1` test/configuration/parse/coverage error, `2` quality limit failure.
