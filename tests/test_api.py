from importlib.resources import files
from threading import Event

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def demo_api(tmp_path):
    from ward.api import create_app
    from ward.storage.sqlite import SQLiteStorage

    database_path = tmp_path / "api.sqlite"
    saved = SQLiteStorage(database_path)
    saved.register_model({"model_id": "net1", "name": "Test network"})
    saved.create_scenario(
        {
            "scenario_id": "test-scenario",
            "name": "Test scenario",
            "model_id": "net1",
            "attack_type": "pump_off",
            "target_components": ["9"],
            "timing": {"start_time_sec": 3600, "duration_sec": 3600},
            "parameters": {},
        }
    )
    saved.close()
    network_path = tmp_path / "network.inp"
    network_path.write_bytes(
        files("wntr.library").joinpath("networks", "Net1.inp").read_bytes()
    )
    application = create_app(database_path, {"net1": network_path})
    return application, database_path


def test_api_lists_saved_scenarios_and_network_topology(tmp_path):
    from ward.api import create_app
    from ward.storage.sqlite import SQLiteStorage

    database_path = tmp_path / "api.sqlite"
    application = create_app(database_path=database_path)
    with TestClient(application) as client:
        scenarios = client.get("/scenarios")
        assert scenarios.status_code == 200
        assert scenarios.json()[0]["scenario_id"] == "scenario_pump_01"
        scenario = client.get("/scenarios/scenario_pump_01")
        assert scenario.status_code == 200
        assert scenario.json()["model_id"] == "ctown"
        topology = client.get("/models/ctown/topology")
        assert topology.status_code == 200
        network = topology.json()
        assert len(network["nodes"]) == 396
        assert len(network["links"]) == 444
        assert all(node["coordinates"] is not None for node in network["nodes"])
        pump = next(link for link in network["links"] if link["link_id"] == "PU1")
        assert pump["type"] == "Pump"
        assert pump["start_node"] in {node["node_id"] for node in network["nodes"]}
        assert network["visualization"]["coordinate_system"] == "model"
        assert client.get("/scenarios/missing").status_code == 404
        assert client.get("/models/missing/topology").status_code == 404

    saved = SQLiteStorage(database_path)
    try:
        assert len(saved.list_scenarios()) == 1
    finally:
        saved.close()


@pytest.mark.simulation
def test_submit_poll_and_retrieve_saved_results(demo_api, monkeypatch):
    from ward import analysis

    application, _ = demo_api
    started = Event()
    release = Event()
    identify = analysis.identify_affected_nodes

    def paused_analysis(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return identify(*args, **kwargs)

    monkeypatch.setattr(analysis, "identify_affected_nodes", paused_analysis)
    with TestClient(application) as client:
        try:
            response = client.post("/runs", json={"scenario_id": "test-scenario"})
            assert response.status_code == 202
            submitted = response.json()
            run_id = submitted["run_id"]
            assert submitted["scenario_id"] == "test-scenario"
            assert submitted["model_id"] == "net1"
            assert started.wait(10)
            assert client.get(f"/runs/{run_id}").json()["status"] == "running"
            pending = client.get(f"/runs/{run_id}/results")
            assert pending.status_code == 409
            assert pending.json()["error"]["code"] == "RESULTS_NOT_READY"
        finally:
            release.set()

    with TestClient(application) as client:
        status = client.get(f"/runs/{run_id}").json()
        assert status["status"] == "succeeded"
        response = client.get(f"/runs/{run_id}/results")
        assert response.status_code == 200
        result = response.json()
        assert result["run_id"] == run_id
        assert result["units"] == {"time": "s", "pressure": "psi"}
        assert result["time_sec"][0] == 0
        assert result["analysis"]["summary"]["affected_node_count"] > 0
        assert result["wntr_version"]
        assert set(result["pressures"]) == {"baseline", "attack"}
        assert len(result["pressures"]["baseline"]["11"]) == len(result["time_sec"])
        baseline = result["pressures"]["baseline"]["11"]
        attack = result["pressures"]["attack"]["11"]
        assert baseline != attack
        assert "network_path" not in response.text
        assert client.get("/runs/missing").status_code == 404
        assert client.get("/runs/missing/results").status_code == 404


def test_failed_model_run_returns_safe_error_code(demo_api):
    application, database_path = demo_api
    (database_path.parent / "network.inp").write_text("invalid network file")
    with TestClient(application) as client:
        submitted = client.post("/runs", json={"scenario_id": "test-scenario"})
        assert submitted.status_code == 202
        run_id = submitted.json()["run_id"]

    with TestClient(application) as client:
        status = client.get(f"/runs/{run_id}")
        assert status.json()["status"] == "failed"
        assert status.json()["error"]["code"] == "MODEL_LOADING_ERROR"
        assert str(database_path.parent) not in status.text
        results = client.get(f"/runs/{run_id}/results")
        assert results.status_code == 409
        assert results.json()["error"]["code"] == "RUN_FAILED"


@pytest.mark.simulation
@pytest.mark.parametrize(
    "stage,code",
    [
        ("attack", "ATTACK_SETUP_ERROR"),
        ("simulation", "SIMULATION_ERROR"),
        ("convergence", "CONVERGENCE_ERROR"),
        ("analysis", "ANALYSIS_ERROR"),
        ("storage", "STORAGE_ERROR"),
        ("unexpected", "UNEXPECTED_ERROR"),
    ],
)
def test_background_errors_are_classified_and_sanitized(
    demo_api, monkeypatch, stage, code
):
    import sqlite3

    from ward import analysis, simulation
    from ward.storage.sqlite import SQLiteStorage

    application, database_path = demo_api
    sensitive = str(database_path.parent / "private-network.inp")

    def fail(*args, **kwargs):
        if stage == "convergence":
            raise RuntimeError(f"Simulation did not converge at time 1: {sensitive}")
        if stage == "storage":
            raise sqlite3.OperationalError(sensitive)
        raise ValueError(sensitive)

    if stage == "attack":
        saved = SQLiteStorage(database_path)
        definition = saved.get_scenario("test-scenario")
        definition["target_components"] = ["missing-pump"]
        saved.update_scenario("test-scenario", definition)
        saved.close()
    elif stage in {"simulation", "convergence"}:
        monkeypatch.setattr(simulation.wntr.sim.WNTRSimulator, "run_sim", fail)
    elif stage == "analysis":
        monkeypatch.setattr(analysis, "identify_affected_nodes", fail)
    elif stage == "storage":
        monkeypatch.setattr(SQLiteStorage, "save_run_details", fail)
    else:
        monkeypatch.setattr(simulation, "run_paired_simulations", fail)

    with TestClient(application) as client:
        response = client.post("/runs", json={"scenario_id": "test-scenario"})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
    with TestClient(application) as client:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["error"]["code"] == code
        assert sensitive not in response.text
        assert "Traceback" not in response.text
        assert "error_message" not in response.json()


def test_request_errors_and_openapi_match_the_contract(demo_api, monkeypatch):
    import sqlite3

    from ward.storage.sqlite import SQLiteStorage

    application, database_path = demo_api
    with TestClient(application) as client:
        for body in (
            {},
            {"scenario_id": ""},
            {"scenario_id": " "},
            {"scenario_id": "test-scenario", "network_path": "private.inp"},
        ):
            response = client.post("/runs", json=body)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "INPUT_ERROR"
        assert client.post("/runs", json={"scenario_id": "missing"}).status_code == 404
        storage = SQLiteStorage(database_path)
        definition = storage.get_scenario("test-scenario")
        definition["attack_type"] = "valve_closure"
        storage.update_scenario("test-scenario", definition)
        storage.close()
        unsupported = client.post("/runs", json={"scenario_id": "test-scenario"})
        assert unsupported.status_code == 422
        assert unsupported.json()["error"]["code"] == "INPUT_ERROR"

        schema = client.get("/openapi.json").json()
        for path in (
            "/scenarios",
            "/scenarios/{scenario_id}",
            "/models/{model_id}/topology",
            "/runs",
            "/runs/{run_id}",
            "/runs/{run_id}/results",
        ):
            assert path in schema["paths"]
        request_schema = schema["components"]["schemas"]["RunRequest"]
        assert request_schema["required"] == ["scenario_id"]
        assert request_schema["properties"]["scenario_id"]["examples"] == [
            "scenario_pump_01"
        ]
        assert "202" in schema["paths"]["/runs"]["post"]["responses"]
        assert "pressures" in schema["components"]["schemas"]["RunResults"]["required"]
        assert client.get("/docs").status_code == 200

        def fail_read(*args, **kwargs):
            raise sqlite3.OperationalError(str(database_path))

        monkeypatch.setattr(SQLiteStorage, "list_scenarios", fail_read)
        error = client.get("/scenarios")
        assert error.status_code == 500
        assert error.json()["error"]["code"] == "STORAGE_ERROR"
        assert str(database_path) not in error.text

        def busy_read(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(SQLiteStorage, "list_scenarios", busy_read)
        busy = client.get("/scenarios")
        assert busy.status_code == 503
        assert busy.json()["error"]["code"] == "STORAGE_BUSY"
        assert busy.headers["Retry-After"] == "1"
