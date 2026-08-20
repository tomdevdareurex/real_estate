# Contributing

## Principles

- Write all code, documentation, logs, messages, and comments in English.
- Keep personal contact collection and image collection disabled by default.


## Development

```powershell
C:\Program Files\Python312\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pytest
```

Add a failing test before changing behavior, then run formatting, linting, strict typing,
security scanning, coverage, and package build checks.

Ruff runs only in Linux CI because its unsigned Windows native executable is blocked by corporate
AppLocker. Local checks use `python -m black`, `python -m isort`, `python -m mypy`, and
`python -m bandit`.
