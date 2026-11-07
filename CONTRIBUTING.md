# Contributing

Thanks for helping improve QwenCoreMCP. Keep the three MCP servers independently runnable and avoid unnecessary runtime dependencies.

## Run the tests

```bash
python -m pip install -r requirements.txt
python -m pip install pytest jsonschema
python -m pytest -q tests/
```

Please add regression tests for bug fixes in the structured `tests/` package. Include your Python version, operating system, reproduction steps, and expected/actual behavior in bug reports.

Do not share real credentials, private memory files, or unsanitized LM Studio conversation exports. For security reports, follow `SECURITY.md` rather than posting exploit details publicly.
