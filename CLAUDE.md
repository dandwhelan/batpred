# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Predbat is a Home Assistant addon (app) that predicts and optimizes home battery charging/discharging based on electricity rates, solar forecasts, and historical load data. It supports inverters from GivEnergy, Solis, Huawei, SolarEdge, and Sofar, and integrates with energy providers like Octopus Energy, Kraken (EDF/E.ON), and Axle Energy VPP.

It also supports Predbat.com which is a cloud based product that does not use Home Assistant and can run in a Docker environment.

## Running Tests

Tests take time to run, _always_ save the test output to a file and then grep the file afterwards.
Never just pipe the output to grep as if you search for the wrong thing you will have to re-run it again.

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

# Stop at the first failure (the runner otherwise reports every failing test):
./run_all --fail-fast

# Give each test a fresh PredBat instance, to find tests coupled to suite order:
./run_all --quick --isolate

# Coverage analysis:
./run_cov --quick
# Then open htmlcov/index.html
```

The `run_all` script is a thin wrapper; you can run `unit_test.py` directly from the `coverage/` directory (it needs to be the working directory so relative paths resolve).

**Live-instance collision**: `tests/test_web_if.py` posts to a hardcoded `http://127.0.0.1:5052`. On a machine that also runs a live Predbat instance bound to that port (e.g. via Docker), the test's POSTs — including `/restart` and mode changes — land on the live instance instead of the test's own web server. Changing `coverage/apps.yaml`'s `web_port` does not help, since the test's client target is hardcoded separately from the server it starts. Check for anything already bound to port 5052 (`docker ps`, `lsof -i :5052`) before running the full or `--quick` suite anywhere a live instance might be running.

**Live-config collision**: the same hazard, one layer deeper. `CONFIG_ROOTS = ["/config", "/conf", "/homeassistant", "./"]` (`const.py`) is searched in order, so inside a container that bind-mounts a live deployment at `/config`, `config_root` resolves to _that deployment_ and any test building a real object reads and writes the owner's files. `tests/test_optimise_all_windows.py` constructs a real `Compare`, whose `load_yaml()`/`save_yaml()` use `config_root + "/comparisons.yaml"`: run there it loads the owner's saved tariff comparisons, adds its own `base`/`double` fixtures and writes all of them back. That is also why it reports `Compare expected 2 results but got 13` in that setting — the count is the owner's comparisons, not a regression.

**So run the suite in a throwaway container, never the live one.** The published image ships an empty `/config` of its own, so `--rm` discards anything written:

```bash
docker run --rm --network none --entrypoint sh -v "$PWD:/work" -w /work/coverage <predbat-image> -c 'PREDBAT_KERNEL_REQUIRED=1 python3 ../apps/predbat/unit_test.py --quick'
```

`--entrypoint sh` is required — the image's default entrypoint ignores the command and loops on "Please Update apps.yaml". `--network none` is fine for the suite (it only suppresses the GitHub manifest check), but the annual heat-pump smoke scripts need real network for Open-Meteo. Anything that calls `PredBat()` directly rather than through `unit_test.py` — `run_annual_tests.py`, for instance — also needs `PREDBAT_APPS_FILE` pointed at an apps.yaml, e.g. `/work/coverage/apps.yaml`.

**Known flaky test**: `tests/test_manual_select.py` picks a dropdown option by weekday label (`"%a %H:%M"`) and can fail near midnight UTC, when the label's weekday falls behind the harness's "today" — `get_override_time_from_string` then resolves it into the past and `manual_select` returns `off`. A failure here in that window is not necessarily a regression; rerunning after the boundary passes should confirm.

### Replaying a debug dump

`predbat_debug_*.yaml` is a full state dump, written to `debug/` when `switch.predbat_debug_enable` is on and normally what users attach to bug reports. Replay one against the current tree with:

```bash
cd coverage
./run_all --debug_file <path-to-predbat_debug.yaml>
```

It lists every config item that differs from its default, recalculates the plan, and writes `plan_orig.html` / `plan_final.json` into `coverage/`. Add `--redo` to recompute rates, load model and Octopus slots instead of reusing the ones in the dump. Committed examples live in `coverage/cases/*.yaml` and run as golden regressions under `./run_all --test debug_cases`.

### Debugging notes

`.claude/skills/issue-triage/references/debug-journal.md` records what past investigations found: per-integration API quirks, symptom-to-module pointers, and traps such as stale kernel binaries and test-order pollution. Read it before debugging an integration or a "the plan is wrong" report, and add to it when you learn something a future session would want.

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

### Storage

The Storage component provides an abstraction of saving/loading from a cache and must be used instead of direct file access.

### Testing Infrastructure

`unit_test.py` uses `TestHAInterface` (from `tests/test_infra.py`) to mock the Home Assistant connection. Tests call `create_predbat()` which builds a full `PredBat` instance against the mock. Individual test modules in `tests/` follow the naming convention `test_<feature>.py` with an exported `run_<feature>_tests()` or `test_<feature>()` function registered in `TEST_REGISTRY` in `unit_test.py`.

**IMPORTANT** Unit tests must be added for all new code.

## Documentation

Documentation source lives in `docs/` and is built with MkDocs:

```bash
mkdocs serve   # Live preview at http://localhost:8000
```

When adding a new doc page, add it to `mkdocs.yml`. The published site at <https://springfall2008.github.io/batpred/> is built automatically from `main` via GitHub Actions.

## Feed-in Tariff (FIT) Support

Predbat supports UK Feed-in Tariff schemes where users earn a generation tariff on all solar production plus a deemed export payment on a percentage of generation (typically 50%).

### How It Works

FIT is off by default. When the `metric_fit_enable` master switch is turned on **and** `metric_fit_generation_rate` is set above 0 (both Expert Mode), FIT mode is activated:

- **Export rate zeroed in optimizer**: Since deemed export pays regardless of actual export, the optimizer treats actual export as having zero additional value. This makes the optimizer prefer self-consumption of solar over exporting it.
- **Battery headroom for solar**: The optimizer will not charge the battery to 100% from the grid when solar generation is forecast, leaving room for solar to charge the battery during the day.
- **FIT income tracked**: Generation and deemed export income are subtracted from the cost metric for accurate cost/savings display.

Turning `metric_fit_enable` off zeroes the FIT rates at config-load time (`Fetch.fit_apply_enable()`), so every downstream consumer — the Python prediction engine, the C++ kernel, and the published sensors — sees FIT as inactive without the user having to clear their configured rates. Because this gate lives at the single point where rates are loaded, no kernel change or ABI bump is required for the toggle.

### Config Items

All under Expert Mode in `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `metric_fit_enable` | False | Master switch (off by default); must be turned on to activate FIT, otherwise all FIT behaviour is disabled and configured rates are ignored |
| `metric_fit_generation_rate` | 0 p/kWh | FIT generation tariff rate |
| `metric_fit_deemed_export_rate` | 0 p/kWh | Deemed export tariff rate |
| `metric_fit_deemed_export_percentage` | 50% | Deemed export percentage |

### HA Sensors (when FIT enabled)

| Sensor | Description |
|--------|-------------|
| `predbat.fit_income` | Predicted FIT income (base plan) |
| `predbat.fit_income_best` | Predicted FIT income (best/optimised plan) |
| `predbat.fit_income_yesterday` | Predicted FIT income for yesterday's baseline |

All sensors include attributes: `generation_income`, `deemed_export_income`, `generation_rate`, `deemed_export_rate`, `deemed_export_percentage`.

### Key Files

| File | What changed |
|------|-------------|
| `config.py` | `metric_fit_enable` master switch plus three FIT rate `CONFIG_ITEMS` entries |
| `fetch.py` | Loads FIT config values, applies the `metric_fit_enable` gate (`fit_apply_enable()`), logs when FIT is enabled or disabled by the switch |
| `prediction.py` | Zeros export rate when FIT enabled; tracks FIT income per simulation step |
| `plan.py` | Extracts FIT income from prediction results; publishes `fit_income` / `fit_income_best` sensors |
| `output.py` | Extracts FIT income from yesterday predictions; publishes `fit_income_yesterday` sensor |
| `tests/test_infra.py` | FIT defaults (including `metric_fit_enable`) added to test config and `reset_inverter()` |
| `tests/test_fit.py` | Covers the FIT calculator plus the `metric_fit_enable` master-switch toggle |
| `prediction_kernel.py` | `kernel_supported()` returns False when a FIT rate is set, so a FIT run uses the Python engine rather than the FIT-blind C++ kernel |
| `tests/test_kernel_parity.py` | FIT deterministic edge cases and FIT rate randomisation in the parity sweep |

### C++ Kernel Note (fork)

**FIT is Python-only.** The kernel carried FIT fields in `PkContext` at fork ABI/parity 103-105 until the 2026-08-24 upstream merge dropped them; the fork is now back on upstream's plain numbering (currently **5/10**) and the C++ kernel knows nothing about FIT. `kernel_supported()` therefore returns False whenever `metric_fit_generation_rate` or `metric_fit_deemed_export_rate` is above zero, so a FIT user falls back to the Python engine and gets the plan they configured for, just more slowly. Covered by `tests/test_fit.py`. If FIT is ever re-added to the kernel, that gate is what to remove.

**The fork's kernel source is not upstream's.** Both sit at ABI/parity 5/10, but this fork carries its own C++ work on top: an exact-integer `round_py` fast path (~7ns vs upstream's ~96ns snprintf/strtod), a `shared_ptr` context map so `pk_context_free` on another thread cannot free a running context, a `calc_percent_limit` clamp at zero, the `pk_round_py_test` hook, and a `kernel_n_steps` guard. Because the revision numbers match upstream's, **the loader cannot tell fork binaries from upstream ones** — so on any upstream merge that touches `prediction_kernel.cpp`, taking either side's `.so` files wholesale is wrong. Merge the source, then rebuild all six `prediction_kernel_lib_*.so` from the merged tree via `build_kernel_cross.sh` (needs zig; `ZIG=/path/to/zig bash apps/predbat/build_kernel_cross.sh`) and confirm with `./run_all --test kernel_parity`.

Any behavioural change to `prediction.py`'s hot loop must still be mirrored in `prediction_kernel.cpp` with `KERNEL_PARITY_REVISION` and `PK_PARITY_REVISION` both bumped, followed by a rebuild of all six binaries.

## Fork-Specific Notes

This repository is a personal fork of `springfall2008/batpred` (currently based on upstream v8.48.4). Fork changes on top of upstream:

- **FIT support** — see the Feed-in Tariff section above
- **Custom web dashboard** — the port-5052 web UI has a `/dash_entities` page and a redesigned power flow diagram (`web.py`, `web_helper.py`)
- **DB history fix** — `db_manager`/HA history returns correct results for entities with no state change inside the query window
- **Fork release pipeline** — see below

### Release Process (fork)

Releases are versioned `v712.xx` (kept deliberately above upstream's `v8.x` scheme so the built-in updater treats fork releases as newest). To cut a release:

1. Bump `THIS_VERSION` in `apps/predbat/predbat.py` (e.g. `v712.05`)
2. Merge to `main` — `.github/workflows/release.yml` derives the tag from `THIS_VERSION` on push to `main` and creates the GitHub release automatically (skips if the tag already exists)

Installations tracking this fork self-update from these releases via Predbat's built-in updater (`github.py` points at `dandwhelan/batpred`).

### Merging from upstream

When merging `upstream/main`, preserve the FIT feature (Python + C++ kernel), the custom dashboard, and the fork release workflow. If upstream bumps its kernel ABI revision, re-apply FIT kernel support and keep the fork's revision strictly above upstream's, then rebuild all six kernel binaries.
