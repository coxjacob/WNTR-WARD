import numpy as np
import pytest

from ward.simulation import load_test_network, run_simulation


@pytest.mark.simulation
def test_short_simulation_returns_pressures_and_flows(tmp_path):
    network = load_test_network()
    network.options.time.duration = 3600
    network.options.time.report_timestep = 3600

    results = run_simulation(network, tmp_path / "net1")

    pressure = results.node["pressure"]
    flow = results.link["flowrate"]
    assert results.error_code is None
    assert pressure.index.tolist() == [0, 3600]
    assert flow.index.tolist() == [0, 3600]
    assert pressure.shape == (2, 11)
    assert flow.shape == (2, 13)
    assert np.isfinite(pressure.to_numpy()).all()
    assert np.isfinite(flow.to_numpy()).all()
