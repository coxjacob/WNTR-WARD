import ast
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from ward.dashboard import build_network_figure, build_pressure_figure

DASHBOARD = Path(__file__).resolve().parents[1] / "ward" / "dashboard.py"


def _sample_scenario():
    return {
        "schema_version": "1.0",
        "scenario_id": "scenario_pump_01",
        "name": "CTOWN Main Pump Outage",
        "description": "Shut down pump PU1 between hours 1 and 3.",
        "attack_type": "pump_off",
        "model_id": "ctown",
        "target_components": ["PU1"],
        "timing": {"start_time_sec": 3600, "duration_sec": 7200},
        "parameters": {},
    }


def _sample_topology():
    return {
        "model_id": "ctown",
        "nodes": [
            {"node_id": "J1", "type": "Junction", "coordinates": [10.0, 20.0]},
            {"node_id": "J2", "type": "Junction", "coordinates": [30.0, 20.0]},
            {"node_id": "T1", "type": "Tank", "coordinates": [50.0, 40.0]},
        ],
        "links": [
            {
                "link_id": "PU1",
                "type": "Pump",
                "start_node": "J1",
                "end_node": "J2",
                "vertices": [],
            },
            {
                "link_id": "P1",
                "type": "Pipe",
                "start_node": "J2",
                "end_node": "T1",
                "vertices": [],
            },
        ],
        "visualization": {"coordinate_system": "model"},
    }


def _sample_results(run_id="run-123", affected=True):
    affected_nodes = (
        [
            {
                "node_id": "J2",
                "rank": 1,
                "baseline_min_pressure_psi": 48.0,
                "attack_min_pressure_psi": 12.5,
                "max_pressure_drop_psi": 35.5,
                "duration_hours": 2.0,
                "impact_category": "attack_induced_low_pressure",
                "reasons": [
                    "Pressure dropped below 20.0 psi (to 12.50 psi) from compliant baseline (48.00 psi)."
                ],
            }
        ]
        if affected
        else []
    )
    return {
        "run_id": run_id,
        "scenario_id": "scenario_pump_01",
        "model_id": "ctown",
        "status": "succeeded",
        "created_at": "2026-04-01 12:00:00",
        "completed_at": "2026-04-01 12:00:03",
        "units": {"time": "s", "pressure": "psi"},
        "time_sec": [0, 3600, 7200, 10800],
        "pressures": {
            "baseline": {
                "J1": [50.0, 50.0, 50.0, 50.0],
                "J2": [48.0, 48.0, 48.0, 48.0],
                "T1": [10.0, 10.5, 11.0, 10.5],
            },
            "attack": {
                "J1": [50.0, 49.0, 49.0, 50.0],
                "J2": [48.0, 12.5, 14.0, 47.5],
                "T1": [10.0, 10.2, 10.4, 10.5],
            },
        },
        "analysis": {
            "scenario_id": "scenario_pump_01",
            "summary": {
                "total_evaluated_nodes": 3,
                "affected_node_count": len(affected_nodes),
                "pre_existing_only_node_count": 0,
                "window_start_hours": 1.0,
                "window_end_hours": 3.0,
                "window_duration_hours": 2.0,
            },
            "thresholds": {"min_pressure_psi": 20.0},
            "affected_nodes": affected_nodes,
            "pre_existing_only_nodes": [],
        },
        "timing": {"start_time_sec": 3600, "duration_sec": 7200},
        "wntr_version": "1.5.0",
    }


def _mock_client_factory(routes_get, routes_post=None):
    routes_post = routes_post or {}

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path):
            handler = routes_get[path]
            status, payload = handler() if callable(handler) else handler
            return httpx.Response(
                status, json=payload, request=httpx.Request("GET", f"http://test{path}")
            )

        def post(self, path, json=None):
            handler = routes_post[path]
            status, payload = handler(json) if callable(handler) else handler
            return httpx.Response(
                status,
                json=payload,
                request=httpx.Request("POST", f"http://test{path}"),
            )

    return MockClient


def test_dashboard_does_not_import_wntr_or_storage():
    tree = ast.parse(DASHBOARD.read_text(encoding="utf-8"))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(mod == "wntr" or mod.startswith("wntr.") for mod in imported_modules)
    assert not any(
        mod == "sqlite3" or mod.startswith("ward.storage") for mod in imported_modules
    )


def test_dashboard_handles_unavailable_api_and_recovers(monkeypatch):
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path):
            raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "Client", FailingClient)
    page = AppTest.from_file(str(DASHBOARD)).run()
    assert not page.exception
    assert "Cannot connect to WARD FastAPI service" in page.error[0].value

    monkeypatch.setattr(
        httpx,
        "Client",
        _mock_client_factory(
            {
                "/scenarios": (200, [_sample_scenario()]),
                "/models/ctown/topology": (200, _sample_topology()),
            }
        ),
    )
    page.button[0].click().run()
    assert not page.exception
    assert not page.error
    assert "scenario_pump_01" in page.selectbox[0].value


def test_dashboard_submits_polls_and_displays_results_and_metadata(monkeypatch):
    polls = {"count": 0}

    def status_handler():
        polls["count"] += 1
        if polls["count"] == 1:
            return (
                200,
                {
                    "run_id": "run-123",
                    "scenario_id": "scenario_pump_01",
                    "model_id": "ctown",
                    "status": "running",
                },
            )
        return (
            200,
            {
                "run_id": "run-123",
                "scenario_id": "scenario_pump_01",
                "model_id": "ctown",
                "status": "succeeded",
                "completed_at": "2026-04-01 12:00:03",
            },
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        _mock_client_factory(
            routes_get={
                "/scenarios": (200, [_sample_scenario()]),
                "/models/ctown/topology": (200, _sample_topology()),
                "/runs/run-123": status_handler,
                "/runs/run-123/results": (200, _sample_results("run-123")),
            },
            routes_post={
                "/runs": (
                    202,
                    {
                        "run_id": "run-123",
                        "scenario_id": "scenario_pump_01",
                        "model_id": "ctown",
                        "status": "queued",
                    },
                )
            },
        ),
    )

    page = AppTest.from_file(str(DASHBOARD)).run()
    assert not page.exception

    # Click  da Run Scenario button
    run_button = next(b for b in page.button if b.label == "Run Scenario")
    run_button.click().run()

    assert not page.exception
    assert any("succeeded" in s.value for s in page.success)

    metric_map = {m.label: m.value for m in page.metric}
    assert metric_map["Scenario ID"] == "scenario_pump_01"
    assert metric_map["Attack Type"] == "pump_off"
    assert metric_map["Targeted Components"] == "PU1"
    assert metric_map["Simulation Timestamp"] == "2026-04-01 12:00:03"

    assert page.selectbox[1].value == "J2"
    df = page.dataframe[0].value
    assert df["Baseline (psi)"].tolist() == [48.0, 48.0, 48.0, 48.0]
    assert df["Attack (psi)"].tolist() == [48.0, 12.5, 14.0, 47.5]

    page.selectbox[1].select("J1").run()
    assert not page.exception
    assert any("unaffected" in i.value.lower() for i in page.info)
    assert page.dataframe[0].value["Attack (psi)"].tolist() == [50.0, 49.0, 49.0, 50.0]


def test_dashboard_displays_failed_run_error_and_incomplete_state(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "Client",
        _mock_client_factory(
            routes_get={
                "/scenarios": (200, [_sample_scenario()]),
                "/models/ctown/topology": (200, _sample_topology()),
                "/runs/run-fail": (
                    200,
                    {
                        "run_id": "run-fail",
                        "scenario_id": "scenario_pump_01",
                        "model_id": "ctown",
                        "status": "failed",
                        "error": {
                            "code": "CONVERGENCE_ERROR",
                            "message": "The simulation did not converge.",
                        },
                    },
                ),
            },
            routes_post={
                "/runs": (
                    202,
                    {
                        "run_id": "run-fail",
                        "scenario_id": "scenario_pump_01",
                        "model_id": "ctown",
                        "status": "queued",
                    },
                )
            },
        ),
    )

    page = AppTest.from_file(str(DASHBOARD)).run()
    run_button = next(b for b in page.button if b.label == "Run Scenario")
    run_button.click().run()

    assert not page.exception
    assert not page.success
    assert any("CONVERGENCE_ERROR" in e.value for e in page.error)
    assert any("failed" in w.value.lower() for w in page.warning)


def test_network_and_pressure_figures_match_analysis_and_handle_missing_coords():
    topology = _sample_topology()
    scenario = _sample_scenario()
    results = _sample_results()

    base_fig = build_network_figure(
        topology,
        view_mode="baseline",
        scenario=scenario,
        results=results,
        selected_node="J2",
    )
    atk_fig = build_network_figure(
        topology,
        view_mode="attack",
        scenario=scenario,
        results=results,
        selected_node="J2",
    )
    assert base_fig is not None
    assert atk_fig is not None
    affected_trace = next(t for t in atk_fig.data if t.name == "Affected nodes")
    assert list(affected_trace.customdata) == ["J2"]
    assert not any(t.name == "Affected nodes" for t in base_fig.data)
    attacked_pump_trace = next(t for t in atk_fig.data if t.name == "Attacked pump")
    assert list(attacked_pump_trace.customdata) == ["PU1"]
    p_fig = build_pressure_figure(results, "J2")
    assert list(p_fig.data[0].y) == results["pressures"]["baseline"]["J2"]
    assert list(p_fig.data[1].y) == results["pressures"]["attack"]["J2"]
    assert list(p_fig.data[0].x) == [0.0, 1.0, 2.0, 3.0]

    no_coord_topology = {
        "model_id": "empty",
        "nodes": [{"node_id": "J1", "type": "Junction", "coordinates": None}],
        "links": [],
        "visualization": {"coordinate_system": "model"},
    }
    assert build_network_figure(no_coord_topology, view_mode="baseline") is None


def test_dashboard_live_fastapi_integration(tmp_path, monkeypatch):
    """End-to-end check against real FastAPI TestClient endpoints."""
    pytest = __import__("pytest")
    pytest.importorskip("wntr")
    from ward.api import create_app

    app = create_app(database_path=tmp_path / "dash.sqlite")
    with TestClient(app) as client:
        scenarios = client.get("/scenarios").json()
        assert scenarios[0]["scenario_id"] == "scenario_pump_01"
        topology = client.get("/models/ctown/topology").json()
        fig = build_network_figure(
            topology,
            view_mode="baseline",
            scenario=scenarios[0],
            selected_node="J412",
        )
        assert fig is not None
        assert len(fig.data) >= 4
