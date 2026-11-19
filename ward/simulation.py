import hashlib
import logging
import os
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

import pandas as pd

import wntr
from wntr.network.controls import BaseControlAction

from . import analysis
from .scenario import WaterScenario
from .storage.sqlite import SQLiteStorage


def load_test_network():
    network_path = files("wntr.library").joinpath("networks", "Net1.inp")
    return wntr.network.WaterNetworkModel(str(network_path))


def load_ctown(network_path=None):
    if network_path is None:
        network_path = (
            Path(__file__).resolve().parent.parent
            / "examples"
            / "networks"
            / "CTOWN.inp"
        )

    network_path = Path(network_path)

    if not network_path.is_file():
        raise FileNotFoundError(f"CTOWN network file not found: {network_path}")

    return wntr.network.WaterNetworkModel(str(network_path))


def load_preview_network():  # load preset network for prototype dashboard, return a dictionary with the junctions and the network HTML
    network_path = files("wntr.library").joinpath(
        "networks", "Net3.inp"
    )  # loads preset network from WNTR library
    network = wntr.network.WaterNetworkModel(
        str(network_path)
    )  # load the network into a model object
    with (
        TemporaryDirectory() as directory
    ):  # create a temporary directory to store the network HTML file
        plot_path = (
            Path(directory) / "network.html"
        )  # create a path for the network HTML file in the temporary directory
        wntr.graphics.plot_interactive_network(  # asks WNTR to plot the network
            network,
            node_attribute="elevation",  # color nodes by their elevation
            node_attribute_name="Elevation (m)",  # names attribute in the plot's hover details
            filename=str(plot_path),  # tells WNTR where to save the HTML file
            auto_open=False,  # prevents WNTR from opening the HTML file in a browser after saving it
        )
        network_html = plot_path.read_text(
            encoding="utf-8"
        )  # read the HTML file into a string so it can be embedded in the dashboard
    return {  # return a dictionary with junctions and network HTML for the dashboard to use
        "junctions": network.junction_name_list,
        "network_html": network_html,
    }


def run_simulation(network, output_prefix):
    simulator = wntr.sim.EpanetSimulator(network)
    return simulator.run_sim(file_prefix=str(output_prefix), convergence_error=True)


# wrap a normal pump status action so it pauses during the outage
class _PumpStatusAction(BaseControlAction):
    # store original action, simulation model, and outage window
    def __init__(self, action, network, start_time, end_time):
        super().__init__()
        self.action = action
        self.network = network
        self.start_time = start_time
        self.end_time = end_time

    # keep the original action's model dependencies
    def requires(self):
        return self.action.requires()

    # report the same pump and status attribute as the original action
    def target(self):
        return self.action.target()

    # keep the original action's description for logging
    def __str__(self):
        return str(self.action)

    # run the original action outside the outage window and notify the simulator of the update
    def run_control_action(self):
        if not self.start_time < self.network.sim_time <= self.end_time:
            self.action.run_control_action()
            self.notify()


# load requested scenario or default to the bundled pump outage scenario if none is provided
def _load_pump_scenario(scenario_path):
    if isinstance(scenario_path, WaterScenario):
        return scenario_path
    if scenario_path is None:
        scenario_path = (
            Path(__file__).resolve().parent / "scenarios" / "pump_poweroff_basic.json"
        )
    return WaterScenario.load_from_json(Path(scenario_path))


# check the scenario uses a supported pump outage type and targets one component
def _validate_pump_scenario(scenario):
    if scenario.attack_type not in {"pump_off", "pump_failure"}:
        raise ValueError(
            f"run_pump_attack only supports pump_off/pump_failure scenarios; "
            f"got {scenario.attack_type!r}"
        )
    if len(scenario.target_components) != 1:
        raise ValueError(
            "A pump attack scenario must specify exactly one target component"
        )


# schedule outage, prevent normal controls from reopening the pump mid-outage
def _apply_pump_outage(network, scenario):
    pump_name = scenario.target_components[0]
    if pump_name not in network.pump_name_list:
        raise ValueError(f"Target {pump_name!r} is not a pump in the selected network")

    start_time = scenario.timing.start_time_sec
    end_time = start_time + scenario.timing.duration_sec
    pump = network.get_link(pump_name)

    # wrap only actions that change the selected pump's status
    def guard_pump_status(actions):
        return [
            _PumpStatusAction(action, network, start_time, end_time)
            if action.target() == (pump, "status")
            else action
            for action in actions
        ]

    # apply the guard to matching actions in both branches of each existing control
    for _, control in network.controls():
        if any(action.target() == (pump, "status") for action in control.actions()):
            control.update_then_actions(guard_pump_status(control._then_actions))
            control.update_else_actions(guard_pump_status(control._else_actions))

    pump.add_outage(
        network,
        start_time,
        end_time,
        add_after_outage_rule=True,
    )


@contextmanager
def _stored_run(
    scenario, network_path, storage, run_id=None, *, initial_status="running"
):
    owned = storage is None
    if owned:
        storage = SQLiteStorage(
            os.environ.get("WARD_DATABASE_PATH", "data/ward.sqlite")
        )
    saved_run = False
    try:
        definition = scenario.model_dump(mode="json")
        if run_id is None:
            run_id = uuid4().hex
        existing = storage.get_run(run_id)
        if existing is not None:
            if (
                initial_status != "running"
                or existing["status"] != "pending"
                or existing["scenario_snapshot"] != definition
            ):
                raise ValueError(f"Run already exists: {run_id}")
            storage.update_run_status(run_id, "running")
        else:
            if storage.get_model(scenario.model_id) is None:
                storage.register_model(
                    {"model_id": scenario.model_id, "name": scenario.model_id}
                )
            if storage.get_scenario(scenario.scenario_id) is None:
                storage.create_scenario(definition)
            else:
                storage.update_scenario(scenario.scenario_id, definition)
            storage.save_run(
                {
                    "run_id": run_id,
                    "scenario_id": scenario.scenario_id,
                    "model_id": scenario.model_id,
                    "status": initial_status,
                    "metadata": {
                        "network_path": str(Path(network_path).resolve()),
                        "network_sha256": hashlib.sha256(
                            Path(network_path).read_bytes()
                        ).hexdigest(),
                        "wntr_version": wntr.__version__,
                    },
                }
            )
        saved_run = True
        yield storage, run_id
    except Exception as error:
        if saved_run:
            storage.update_run_status(run_id, "failed", str(error))
        raise
    finally:
        if owned:
            storage.close()


def _save_case_results(storage, run_id, results, *, analysis_result=None):
    summary = {
        name: {key: value for key, value in case.items() if key not in {"node", "link"}}
        for name, case in results.items()
    }
    if analysis_result is not None:
        summary["analysis"] = analysis_result
    storage.save_run_summary(run_id, summary)
    storage.save_run_details(run_id, results)
    failures = [
        str(case["failure"]) for case in results.values() if not case["converged"]
    ]
    storage.update_run_status(
        run_id,
        "failed" if failures else "succeeded",
        "; ".join(failures) if failures else None,
    )


def run_pump_attack(
    scenario_path=None,
    network_path=None,
    output_path=None,
    *,
    storage=None,
):
    """Run one pump outage described by a WNTR-WARD scenario JSON file.

    Provide ``network_path`` to select the EPANET INP file for the scenario's
    ``model_id``. The model ID is registered in storage, and the run records
    the selected network path and content digest.

    Results are written as one CSV containing node pressures and link
    flowrates at each reported simulation time. The WNTR results object is
    returned for further analysis. The scenario, run, and measurements are also
    saved to SQLite at WARD_DATABASE_PATH (default data/ward.sqlite).
    Pass storage to inject an implementation of the shared storage interface.
    """
    scenario = _load_pump_scenario(scenario_path)
    if network_path is None:
        raise ValueError("network_path is required to select the scenario model")

    _validate_pump_scenario(scenario)
    network = wntr.network.WaterNetworkModel(str(network_path))
    _apply_pump_outage(network, scenario)
    end_time = scenario.timing.start_time_sec + scenario.timing.duration_sec
    network.options.time.duration = max(network.options.time.duration, end_time)

    with _stored_run(scenario, network_path, storage) as (saved, run_id):
        simulator = wntr.sim.WNTRSimulator(network)
        results = simulator.run_sim(convergence_error=True)

        if output_path is None:
            output_path = Path.cwd() / f"{scenario.scenario_id}_results.csv"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        pressure = results.node["pressure"].add_prefix("node_pressure_")
        flowrate = results.link["flowrate"].add_prefix("link_flowrate_")
        output = pd.concat([pressure, flowrate], axis=1)
        output.index.name = "time_sec"
        output.to_csv(output_path)

        print(f"Simulation results written to: {output_path.resolve()}")
        _save_case_results(
            saved,
            run_id,
            {
                "attack": {
                    "simulator": "WNTRSimulator",
                    "wntr_version": wntr.__version__,
                    "settings": network.options.to_dict(),
                    "converged": True,
                    "failure": None,
                    "node": results.node,
                    "link": results.link,
                }
            },
        )
        return results


# run one simulation, collect results, and return a dictionary with the results and metadata
def _run_paired_case(network, scenario=None):
    settings = network.options.to_dict()
    started = perf_counter()
    results = None
    failure = None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            if scenario is not None:
                _apply_pump_outage(network, scenario)
            results = wntr.sim.WNTRSimulator(network).run_sim(convergence_error=True)
        except (RuntimeError, ValueError, ArithmeticError) as error:
            failure = {"type": type(error).__name__, "message": str(error)}

    return {
        "simulator": "WNTRSimulator",
        "wntr_version": wntr.__version__,
        "settings": settings,
        "elapsed_seconds": perf_counter() - started,
        "converged": results is not None,
        "warnings": [str(warning.message) for warning in caught],
        "failure": failure,
        "node": {}
        if results is None
        else {metric: frame.copy() for metric, frame in results.node.items()},
        "link": {}
        if results is None
        else {metric: frame.copy() for metric, frame in results.link.items()},
    }


# run the baseline and pump outage with matching settings so that the results can be compared
def run_paired_simulations(
    scenario_path, network_path, *, attack_first=False, storage=None, run_id=None
):
    """Run, analyze, and save a baseline-versus-attack comparison.

    Affected-node analysis is saved in the run summary when both cases
    converge. Success is recorded only after analysis and persistence finish.

    Parameters
    ----------
    scenario_path : path-like or WaterScenario
        Versioned scenario JSON file or validated scenario snapshot.
    network_path : path-like
        EPANET network file matching the scenario model.
    attack_first : bool, optional
        Execute the attack before the baseline.
    storage : Storage, optional
        Inject storage for tests. By default use SQLite at
        WARD_DATABASE_PATH, or data/ward.sqlite.
    run_id : str, optional
        ID for the new run. Must not already exist in storage. When omitted,
        generate a new ID.

    Returns
    -------
    dict
        Baseline and attack measurements with convergence metadata.
    """
    scenario = _load_pump_scenario(scenario_path)
    _validate_pump_scenario(scenario)
    baseline = wntr.network.WaterNetworkModel(str(network_path))
    attack = wntr.network.WaterNetworkModel(str(network_path))
    end_time = scenario.timing.start_time_sec + scenario.timing.duration_sec
    duration = max(baseline.options.time.duration, end_time)
    baseline.options.time.duration = duration
    attack.options.time.duration = duration

    cases = [("baseline", baseline, None), ("attack", attack, scenario)]
    if attack_first:
        cases.reverse()
    with _stored_run(scenario, network_path, storage, run_id) as (saved, saved_run_id):
        results = {
            name: _run_paired_case(network, outage) for name, network, outage in cases
        }
        analysis_result = None
        if all(case["converged"] for case in results.values()):
            analysis_result = analysis.identify_affected_nodes(
                results, scenario=scenario
            )
        _save_case_results(
            saved, saved_run_id, results, analysis_result=analysis_result
        )
        return results


class RunManager:
    """Queue paired runs on one worker. Call close() during application shutdown."""

    def __init__(self, database_path=None):
        self.database_path = Path(
            database_path or os.environ.get("WARD_DATABASE_PATH", "data/ward.sqlite")
        ).resolve()
        self._executor = ThreadPoolExecutor(max_workers=1)

    def submit(self, scenario_path, network_path):
        """Save a pending run and return its ID without waiting for simulation."""
        scenario = _load_pump_scenario(scenario_path)
        _validate_pump_scenario(scenario)
        network_path = Path(network_path).resolve()
        storage = SQLiteStorage(self.database_path)
        try:
            with _stored_run(
                scenario, network_path, storage, initial_status="pending"
            ) as (_, run_id):
                self._executor.submit(self._execute, run_id)
            return run_id
        finally:
            storage.close()

    def _execute(self, run_id):
        storage = None
        try:
            storage = SQLiteStorage(self.database_path)
            run = storage.get_run(run_id)
            network_path = Path(run["metadata"]["network_path"])
            run_paired_simulations(
                WaterScenario.model_validate(run["scenario_snapshot"]),
                network_path,
                storage=storage,
                run_id=run_id,
            )
        except Exception as error:
            logging.getLogger(__name__).exception("Run %s failed", run_id)
            if storage is not None:
                run = storage.get_run(run_id)
                if run["status"] in {"pending", "running"}:
                    storage.update_run_status(run_id, "failed", str(error))
        finally:
            if storage is not None:
                storage.close()

    def get_status(self, run_id):
        """Return queued, running, succeeded, failed, or None for an unknown ID."""
        storage = SQLiteStorage(self.database_path)
        try:
            run = storage.get_run(run_id)
            if run is None:
                return None
            return "queued" if run["status"] == "pending" else run["status"]
        finally:
            storage.close()

    def close(self):
        """Wait for accepted runs to finish and release the worker."""
        self._executor.shutdown(wait=True)


def check_setup():
    network = load_test_network()
    print(f"WNTR version: {wntr.__version__}")
    print(
        f"Loaded Net1: {network.num_nodes} nodes, {network.num_links} links, {network.num_junctions} junctions, {network.num_pipes} pipes, {network.num_pumps} pumps, {network.num_valves} valves, {network.num_tanks} tanks, {network.num_reservoirs} reservoirs"
    )
