# Development

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run locally

```bash
python3 main.py -p "Hello"
```

## Tests

```bash
python3 -m unittest discover -s tests
```

## Lint

```bash
python3 -m py_compile main.py openclaw_core.py tool_executor.py
```

## Packaging

```bash
pip install build
python3 -m build
```
