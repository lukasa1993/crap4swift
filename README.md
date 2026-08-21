# crap4swift

`crap4swift` calculates the Change Risk Anti-Pattern metric for Swift functions and methods.

```text
CRAP = CC² × (1 - coverage)³ + CC
```

The analyzer is implemented in Python. It does not modify the target project.

## Install

```bash
pipx install git+https://github.com/lukasa1993/crap4swift.git
```

## Run

```bash
crap4swift --fail-over 6
```

The default test command is:

```bash
swift test --enable-code-coverage
```

LLVM coverage export JSON is supported. The test command must also export the JSON report when the project does not already do this.

Use a project-specific command when required:

```bash
crap4swift --test-command "<command that runs tests and exports coverage>"
```

Analyze an existing report:

```bash
crap4swift --no-test --coverage target/coverage/coverage.json
```

Use `--require-coverage` to fail when a function has no coverage data. Use `--json` for machine-readable output. Positional path fragments limit the analyzed files.

## Exit status

- `0`: analysis completed and the quality gate passed.
- `1`: the command or analysis failed.
- `2`: coverage is required but missing, or a score is above `--fail-over`.

## Development

```bash
python -m pip install -e . pytest
pytest -q
```
