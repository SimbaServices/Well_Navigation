# Route simulator

Local simulation server for navigation apps under test. No API keys.

## Run

From the repository root:

```bash
python -m pip install -r requirements.txt
python -m route_sim
```

The server listens on http://127.0.0.1:8765.

`PORT` or `--port` changes the port. `HOST` or `--host` changes the bind address.
