# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Predbat is a Home Assistant AppDaemon app that predicts and optimizes home battery charging/discharging based on electricity rates, solar forecasts, and historical load data. It supports inverters from GivEnergy, Solis, Huawei, SolarEdge, and Sofar, and integrates with energy providers like Octopus Energy, Kraken (EDF/E.ON), and Axle Energy VPP.

## Documentation for AI Bots

See .github/copilot-instructions.md for more details.

## Running Tests

Tests live in `apps/predbat/tests/` and are run from the `coverage/` directory:

```bash
# First-time setup (creates venv and installs deps):
cd coverage
source setup.csh

# Run all tests:
./run_all

# Skip slow tests (used by CI):
./run_all --quick

# Run a specific test by name:
./run_all --test basic_rates

# Run multiple specific tests:
./run_all --test basic_rates --test units

# Run tests matching a keyword:
./run_all -k octopus_

# List all available test names:
./run_all --list

# Coverage analysis:
./run_cov --quick
# Then open htmlcov/index.html
```

The `run_all` script is a thin wrapper; you can run `unit_test.py` directly from the `coverage/` directory (it needs to be the working directory so relative paths resolve).

## Code Quality

All checks are enforced via pre-commit and must pass before merging:

```bash
./run_pre_commit
```

Key constraints:

- **Line length**: 256 chars (Black), 250 chars (Flake8)
- **Docstrings**: 100% coverage required (`interrogate`) for all functions and classes
- **Spell checking**: British English (`en-gb`) via CSpell; add valid unknown words to `.cspell/custom-dictionary-workspace.txt` (file is auto-sorted alphabetically on commit, so re-stage after running pre-commit)
- **Variable naming**: `lower_case_with_underscores`
- pre-commit.ci will auto-commit fixable issues (trailing whitespace, etc.) back to your PR branch — run `git pull` after pushing to avoid divergence

## Architecture

### Orchestrator Pattern

`PredBat` in `predbat.py` is the main class and uses **multiple inheritance** to compose its behaviour:

```python
class PredBat(hass.Hass, Octopus, Energidataservice, Fetch, Plan, Execute, Output, UserInterface):
```

The main loop (`update_pred()`) runs every 5 minutes: fetch data → run optimization → execute plan → publish results.

### Core Modules

| Module | Role |
|--------|------|
| `plan.py` | Optimization engine — multi-threaded search across thousands of charge/discharge window scenarios |
| `predict.py` / `prediction.py` | Battery SOC prediction models, PV generation, load forecasting |
| `fetch.py` | Pulls PV forecasts, historical load, rate data, and inverter state |
| `execute.py` | Sends charge/discharge/reserve commands to inverters |
| `output.py` | Creates and updates Home Assistant sensors, switches, selects |
| `inverter.py` | Multi-inverter abstraction layer (GivEnergy, Solis, Huawei, SolarEdge, Sofar) |
| `config.py` | Defines `CONFIG_ITEMS` (all user settings) and `APPS_SCHEMA` (YAML validation) |
| `ha.py` | WebSocket + REST communication with Home Assistant |
| `userinterface.py` | Manages HA input entities (switches, selects, input_numbers) |
| `components.py` | Plugin registry and component lifecycle management |
| `component_base.py` | Abstract base class for all pluggable components |

### Component/Plugin System

`components.py` defines a registry of 18 pluggable components (DB, HA, Web, MCP, GECloud, Octopus, Fox, Solax, Solis, Axle, Ohme, Kraken, etc.). Each component:

- Inherits from `ComponentBase`
- Has `api_start()` / `api_stop()` lifecycle methods
- Can be independently enabled/disabled
- Has health monitoring with exponential backoff
- Routes HA events via entity prefix filtering

### Key Data Flow

1. `Fetch` retrieves rates (Octopus/Kraken API), solar forecasts (Solcast), historical load (HA history), and live inverter state
2. `Plan` runs a search algorithm to find the optimal set of charge/discharge windows over a 48-hour horizon
3. `Execute` sends the resulting commands to the inverter
4. `Output` publishes the plan and metrics as HA sensor states

### Testing Infrastructure

`unit_test.py` uses `TestHAInterface` (from `tests/test_infra.py`) to mock the Home Assistant connection. Tests call `create_predbat()` which builds a full `PredBat` instance against the mock. Individual test modules in `tests/` follow the naming convention `test_<feature>.py` with an exported `run_<feature>_tests()` or `test_<feature>()` function registered in `TEST_REGISTRY` in `unit_test.py`.

## Documentation

Documentation source lives in `docs/` and is built with MkDocs:

```bash
mkdocs serve   # Live preview at http://localhost:8000
```

When adding a new doc page, add it to `mkdocs.yml`. The published site at <https://springfall2008.github.io/batpred/> is built automatically from `main` via GitHub Actions.

## Feed-in Tariff (FIT) Support

Predbat supports UK Feed-in Tariff schemes where users earn a generation tariff on all solar production plus a deemed export payment on a percentage of generation (typically 50%).

### How It Works

When `metric_fit_generation_rate` is set above 0 (Expert Mode), FIT mode is activated:

- **Export rate zeroed in optimizer**: Since deemed export pays regardless of actual export, the optimizer treats actual export as having zero additional value. This makes the optimizer prefer self-consumption of solar over exporting it.
- **Battery headroom for solar**: The optimizer will not charge the battery to 100% from the grid when solar generation is forecast, leaving room for solar to charge the battery during the day.
- **FIT income tracked**: Generation and deemed export income are subtracted from the cost metric for accurate cost/savings display.

### Config Items

All under Expert Mode in `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `metric_fit_generation_rate` | 0 p/kWh | FIT generation tariff rate |
| `metric_fit_deemed_export_rate` | 0 p/kWh | Deemed export tariff rate |
| `metric_fit_deemed_export_percentage` | 50% | Deemed export percentage |

### HA Sensors (when FIT enabled)

| Sensor | Description |
|--------|-------------|
| `predbat.fit_income` | Predicted FIT income (base plan) |
| `predbat.fit_income_best` | Predicted FIT income (best/optimised plan) |

Both sensors include attributes: `generation_income`, `deemed_export_income`, `generation_rate`, `deemed_export_rate`, `deemed_export_percentage`.

### Key Files

| File | What changed |
|------|-------------|
| `config.py` | Three new `CONFIG_ITEMS` entries for FIT settings |
| `fetch.py` | Loads FIT config values, logs when FIT is enabled |
| `prediction.py` | Zeros export rate when FIT enabled; tracks FIT income per simulation step |
| `plan.py` | Extracts FIT income from prediction results; publishes `fit_income` / `fit_income_best` sensors |
| `tests/test_infra.py` | FIT defaults added to test config and `reset_inverter()` |
