# Development

[← PyTap README](../README.md)

This repository includes a dev container with Home Assistant for local development and testing. Pushes to `main` and pull requests run hassfest, HACS validation, Ruff and the test suite in GitHub Actions; other branches can run them from the Actions tab (*Validate → Run workflow*).

## Project structure

```
pytap/
├── custom_components/pytap/     # The Home Assistant integration
│   ├── __init__.py              # Setup, unload, migration, device removal
│   ├── binary_sensor.py         # Flag and reading-fault binary sensors
│   ├── capture_services.py      # Packet capture actions
│   ├── config_flow.py           # Config and options flows
│   ├── connection.py            # TCP / serial / ESPHome serial-proxy sources
│   ├── const.py                 # Constants
│   ├── coordinator.py           # Listener thread, state, publishing, health
│   ├── diagnostics.py           # Diagnostics download
│   ├── energy.py                # Energy integration (Wh accumulation)
│   ├── entity.py                # Shared optimizer device info and flag entity base
│   ├── naming.py                # Device names and barcode entity IDs
│   ├── sensor.py                # Sensor platform
│   ├── services.yaml            # Action descriptions
│   ├── manifest.json, strings.json, translations/en.json, brand/
│   └── pytap/                   # Embedded protocol parser library (no HA imports)
│       └── core/                # Parser, events, state, capture, replay, flags
├── scripts/                     # Offline capture tools (replay_capture.py, capture_reports.py)
├── tests/                       # Integration tests
├── dashboards/                  # PV Panels, PV Production and Grafana dashboards
├── docs/                        # These docs, architecture and protocol notes
├── deploy/                      # deploy.sh: copy the component to a test HA over SSH
├── config/                      # Dev Home Assistant config directory
├── .devcontainer/               # Dev container with Home Assistant
├── requirements-test.txt        # Test dependencies
└── pytest.ini                   # Test configuration
```

## Running Home Assistant (dev)

```bash
python3 -m homeassistant --config config/ --debug
```

## Running tests

Install the test dependencies first (`pip install -r requirements-test.txt`; CI uses Python 3.14).

```bash
# Integration and parser tests (pytest.ini covers both)
python3 -m pytest -vv --tb=short
```

The main suite uses `pytest-homeassistant-custom-component`, whose current release pins Home Assistant 2026.10.4, older than PyTap's minimum (2026.11). So CI also runs **smoke tests on real Home Assistant 2026.11.0**, without that harness, in a separate environment:

```bash
pip install 'homeassistant==2026.11.0' 'serialx==1.15.1' 'aiousbwatcher==1.1.2' 'aiohasupervisor==0.8.1'
python -m unittest discover -s tests -p core_smoke.py -v
```

Real captures aren't committed (they contain optimizer barcodes): tests use synthetic data. Before a release, changes that affect live behaviour are also replayed against local captures in a throwaway Home Assistant instance.

## Linting

```bash
python3 -m ruff check custom_components/pytap/ tests/ scripts/
```

## Deploying to a Home Assistant instance

A deployment script is provided in `deploy/` to push the component to a remote HA OS installation via SSH.

**Setup:**

```bash
# Copy the example config and fill in your credentials
cp deploy/.deploy.env.example deploy/.deploy.env
# Edit deploy/.deploy.env with your host, username, and password
```

> **Note:** `deploy/.deploy.env` is gitignored — credentials are never committed.

**Deploy:**

```bash
# Using the config file
./deploy/deploy.sh

# Or pass credentials directly
./deploy/deploy.sh --host 192.168.1.184 --user 'user' --password 'yourpass'

# Deploy without restarting HA
./deploy/deploy.sh --no-restart
```

The script uploads the component via tar-over-SSH (compatible with HA OS SSH add-on), verifies the deployment, and optionally restarts Home Assistant Core.

Run `./deploy/deploy.sh --help` for all options.

## Design documents

- **[Architecture](architecture.md)** — How the code works now: modules, lifecycle, the listener and publishing, persistence, entities, design decisions, error handling and tests.
- **[Implementation history](implementation.md)** — How PyTap was built, phase by phase, from upstream's first version to the fork's latest work.
- **[Protocol status bits](protocol-status-bits.md)** — What the power report's flag bytes mean, and the evidence.
