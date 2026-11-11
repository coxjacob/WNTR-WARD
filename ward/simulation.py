from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

import wntr
from wntr.network.controls import BaseControlAction

from .scenario import WaterScenario


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


class _PumpStatusAction(BaseControlAction): # wrap a normal pump status action so it pauses during the outage
    def __init__(self, action, network, start_time, end_time): # store original action, simulation model, and outage window
        super().__init__()
        self.action = action
        self.network = network
        self.start_time = start_time
        self.end_time = end_time

    def requires(self): # keep the original action's model dependencies
        return self.action.requires()

    def target(self): # report the same pump and status attribute as the original action
        return self.action.target()

    def __str__(self): # keep the original action's description for logging
        return str(self.action)

    def run_control_action(self): # run the original action outside the outage window and notify the simulator of the update
        if not self.start_time < self.network.sim_time <= self.end_time:
            self.action.run_control_action()
            self.notify()


def run_pump_attack(
    scenario_path=None,
    network_path=None,
    output_path=None,
):
    """Run one pump outage described by a WNTR-WARD scenario JSON file.

    Provide ``network_path`` to select the EPANET INP file for the scenario's
    ``model_id``. The model ID is descriptive metadata; this function does not
    assume a model filename or maintain a model registry.

    Results are written as one CSV containing node pressures and link
    flowrates at each reported simulation time. The WNTR results object is
    returned for further analysis.
    """
    if scenario_path is None:
        scenario_path = (
            Path(__file__).resolve().parent / "scenarios" / "pump_poweroff_basic.json"
        )
    scenario_path = Path(scenario_path)
    scenario = WaterScenario.load_from_json(scenario_path)

    if network_path is None:
        raise ValueError("network_path is required to select the scenario model")

    if scenario.attack_type not in {"pump_off", "pump_failure"}:
        raise ValueError(
            f"run_pump_attack only supports pump_off/pump_failure scenarios; "
            f"got {scenario.attack_type!r}"
        )
    if len(scenario.target_components) != 1:
        raise ValueError(
            "A pump attack scenario must specify exactly one target component"
        )

    network = wntr.network.WaterNetworkModel(str(network_path))

    pump_name = scenario.target_components[0]
    if pump_name not in network.pump_name_list:
        raise ValueError(f"Target {pump_name!r} is not a pump in the selected network")

    start_time = scenario.timing.start_time_sec
    end_time = start_time + scenario.timing.duration_sec
    pump = network.get_link(pump_name)

    def guard_pump_status(actions): # wrap only actions that change the selected pump's status
        return [
            _PumpStatusAction(action, network, start_time, end_time)
            if action.target() == (pump, "status")
            else action
            for action in actions
        ]

    for _, control in network.controls(): # apply the guard to matching actions in both branchs of each existing control
        if any(action.target() == (pump, "status") for action in control.actions()):
            control.update_then_actions(guard_pump_status(control._then_actions))
            control.update_else_actions(guard_pump_status(control._else_actions))

    pump.add_outage(
        network,
        start_time,
        end_time,
        add_after_outage_rule=True,
    )

    # Keep the model's configured duration unless it ends before the outage.
    network.options.time.duration = max(network.options.time.duration, end_time)

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
    return results


def check_setup():
    network = load_test_network()
    print(f"WNTR version: {wntr.__version__}")
    print(
        f"Loaded Net1: {network.num_nodes} nodes, {network.num_links} links, {network.num_junctions} junctions, {network.num_pipes} pipes, {network.num_pumps} pumps, {network.num_valves} valves, {network.num_tanks} tanks, {network.num_reservoirs} reservoirs"
    )
