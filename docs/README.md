# WARD development

WARD models the physical effects of cyber attacks on water networks using WNTR.
The first demonstration will compare a pump shutdown with normal operation.
The application is local-only, single-user, and will use SQLite. Accounts and
simultaneous multi-user access are outside the current scope.

## Setup

Install uv and Git, then run:

```powershell
git clone https://github.com/coxjacob/WNTR-WARD.git
cd WNTR-WARD
uv sync --locked --python 3.10
uv run --locked python -m ward
uv run --locked python -m pytest tests
```

Python 3.10 is the minimum supported version. WNTR is version 1.5.0 in this
repository. Its core dependencies are NumPy, SciPy, NetworkX, pandas, and
Matplotlib; uv.lock records exact versions for repeatable installation.
Commit dependency changes together with the updated lockfile.

Installing WNTR builds C++ extensions. On Windows, install Visual Studio Build
Tools with the Desktop development with C++ workload. On Linux, install your
distribution's C++ compiler and Python development headers. On macOS, install
the Xcode command line tools.

The setup command imports WARD and WNTR, reports the WNTR version, and loads
the bundled Net1 network. It does not run a simulation or start a server.
Application tests live in tests; inherited WNTR tests live in wntr/tests.

## Running simulations

- **Pump outage.** A [scenario JSON file](../ward/scenarios/pump_poweroff_basic.json)
  defines which pump shuts off and when. Pass the network's `.inp` path to
  `run_pump_attack()` to run the outage and export pressures and flows to CSV.
  The default filename is `<scenario_id>_results.csv` in the current directory.
- **Baseline comparison.** `run_paired_simulations()` runs the same network with
  and without the outage using matching settings. It returns both sets of
  results in memory for comparison.

Both scenario simulation entry points save the scenario definition, run status,
configuration, summary, and measurements to SQLite. The default database is
`data/ward.sqlite` relative to the working directory. Set `WARD_DATABASE_PATH`
to choose another file. Run metadata includes the network file path and SHA-256
digest, and each run preserves its scenario snapshot. Failed simulations are
recorded with failure details. Pass `storage=MemoryStorage()` to either function
when testing without disk persistence. Storage connections opened by the
simulation functions are closed before they return.

For background execution, create one `RunManager` from `ward.simulation`.
Its `submit(scenario_path, network_path)` method returns a run ID immediately;
`get_status(run_id)` returns `queued`, `running`, `succeeded`, or `failed`
(or `None` for an unknown ID). It uses one worker and separate SQLite
connections for submission, execution, and polling. Call `close()` during
shutdown to wait for accepted runs to finish. Queued runs use their saved
scenario snapshots.
The queue is in memory.

## Custom dashboard runs

The dashboard offers predefined scenarios and custom configurations. Custom runs
let users choose CTOWN, Net1, Net2, Net3, Net6, ky4, or ky10, a single pump,
and shutdown start/duration in hours. Pump shutdown is currently the only executable custom attack. Attack
windows must end within seven days. Net2 has no pumps and supports preview only.

FastAPI exposes available models at `GET /models` and supported attacks at
`GET /attack-types`. Submit `POST /runs` with either `scenario_id` for a saved
scenario or `scenario` containing `name`, `model_id`, `attack_type`,
`target_components`, and `timing` (`start_time_sec`, `duration_sec`). Custom
configurations are validated before queueing and are never added to the scenario
library. Each run retains its own configuration snapshot, returned with results.
The database upgrades existing runs automatically on startup.

## Structure and responsibilities

| Location | Purpose |
| --- | --- |
| ward/ | Application code |
| ward/simulation.py | WNTR access and the setup check |
| tests/ | Application tests |
| config/ | Application settings |
| migrations/ | SQLite database update scripts |
| docs/ | Application development documentation |
| demo/ | Demo scenarios and instructions |
| wntr/ | Inherited simulation library |

Settings will be added as features need them; invalid settings must stop startup
with a clear error. Database creation and migrations belong to TASK-08. Demo
scenarios should reuse bundled networks rather than copying them; Net1 is only
the setup-check network, not the final demonstration network.

The system architecture document remains outside this repository. These notes
record the implementation boundaries needed for the initial setup.

Future API handlers will validate requests and call scenario, simulation,
analysis, and storage modules under ward. Scenario code will describe attacks;
simulation code will apply them to fresh WNTR models and run them. Analysis
will consume result data, and storage will save scenarios and results to SQLite.
Only simulation modules will import WNTR. Other modules will exchange ordinary
data rather than live WNTR models. No WNTR source changes are needed for setup.

The API will load settings and open storage during startup, then close storage
and release active run resources during shutdown. Simulation failures must be
reported as failed runs with useful details rather than successful partial
results. Invalid requests must produce clear validation errors. The setup
command exits after loading the network and lets errors produce a failing exit
status. API and database behavior will be implemented in their own tasks.

Use test-first development: add a test for the intended behavior, run it and
confirm the expected failure, implement the change, then rerun relevant tests.

## Project checks

Run these commands from the repository directory after `uv sync --locked`.
GitHub runs the same checks on PRs targeting `dev_f26_wntrwardv1`.

| Check | Command |
| --- | --- |
| Formatting | `uv run --locked ruff format --check ward tests` |
| Common code mistakes | `uv run --locked ruff check ward tests` |
| Unit tests | `uv run --locked python -m pytest tests -m "not simulation"` |
| Simulation test | `uv run --locked python -m pytest tests -m simulation` |

Fix formatting with `uv run --locked ruff format ward tests`. Application checks
are separate from inherited WNTR tests. The simulation test uses bundled Net1.

## Demo API

Start the API from the repository directory:

```powershell
uv run --locked uvicorn ward.api:app --host 127.0.0.1
```

Open [interactive API documentation](http://127.0.0.1:8000/docs) for request
and response schemas and examples. Startup preloads the bundled CTOWN pump
scenario if it is not already saved; shutdown waits for accepted runs to finish.
`WARD_DATABASE_PATH` selects the database as for standalone simulations.

| Endpoint | Purpose |
| --- | --- |
| `GET /scenarios` | List saved scenario definitions |
| `GET /scenarios/{scenario_id}` | Retrieve a scenario |
| `GET /models/{model_id}/topology` | Retrieve nodes, links, model coordinates, and visualization metadata |
| `POST /runs` | Submit a saved `scenario_id` or custom `scenario`; return HTTP 202 with run, scenario, and model IDs |
| `GET /runs/{run_id}` | Poll queued, running, succeeded, or failed status |
| `GET /runs/{run_id}/results` | Retrieve saved pressures and affected-node analysis |

Requests select a saved scenario or supply a custom configuration, using a
configured model ID rather than a local file path. All seven packaged models
are configured by default; `create_app()` accepts other model mappings for
integration and tests. The runner currently supports pump outages.

Results include `time_sec`, `pressures.baseline[node_id]`,
`pressures.attack[node_id]`, `units`, `timing`, `wntr_version`, and `analysis`.
Pressure arrays share the time axis and use psi; missing measurements are null.
Older saved runs may have an empty analysis object. Topology coordinates use
the model's coordinate system, not necessarily latitude and longitude.

Errors use `{"error": {"code": "INPUT_ERROR", "message": "..."}}`.
Invalid requests return 422, missing resources 404, unavailable results 409,
and server failures 500. Failed jobs are retrieved with HTTP 200 and
`status: "failed"`, plus a safe `error` object. Codes distinguish
`INPUT_ERROR`, `MODEL_LOADING_ERROR`, `ATTACK_SETUP_ERROR`, `SIMULATION_ERROR`,
`CONVERGENCE_ERROR`, `ANALYSIS_ERROR`, `STORAGE_ERROR`, and `UNEXPECTED_ERROR`.
Debugging details remain in logs and storage, not HTTP responses.

## Running the dashboard

The Streamlit dashboard uses FastAPI for model previews, scenario submission,
run status, and baseline-versus-attack pressure results.

After `uv sync --locked`, start the API from the repository directory:

```powershell
uv run --locked uvicorn ward.api:app --host 127.0.0.1
```

In a second terminal, start the dashboard:

```powershell
uv run --locked streamlit run ward/dashboard.py --server.address 127.0.0.1
```

Open http://127.0.0.1:8501. Stop each service with Ctrl+C in its terminal.
