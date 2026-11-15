"""Tests for WARD storage implementations."""

import pandas as pd
import pytest

from ward.storage.memory import MemoryStorage


@pytest.fixture
def storage():
    """Create fresh in-memory storage for each test."""
    return MemoryStorage()


@pytest.fixture
def sample_model():
    """Create a sample water network model."""
    return {
        "model_id": "ctown",
        "name": "C-Town",
        "description": "Test water network",
    }


@pytest.fixture
def sample_scenario():
    """Create a sample scenario using the existing JSON format."""
    return {
        "schema_version": "1.0",
        "scenario_id": "scenario_test_01",
        "name": "Test Pump Failure",
        "description": "Test scenario",
        "attack_type": "pump_failure",
        "model_id": "ctown",
        "target_components": ["PU1"],
        "timing": {
            "start_time_sec": 3600,
            "duration_sec": 1800,
        },
        "parameters": {},
    }


@pytest.fixture
def sample_run():
    """Create a sample simulation run."""
    return {
        "run_id": "run_test_01",
        "scenario_id": "scenario_test_01",
        "model_id": "ctown",
        "status": "pending",
    }


def test_register_and_get_model(storage, sample_model):
    storage.register_model(sample_model)

    result = storage.get_model("ctown")

    assert result == sample_model


def test_create_and_get_scenario(storage, sample_model, sample_scenario):
    storage.register_model(sample_model)

    storage.create_scenario(sample_scenario)

    result = storage.get_scenario("scenario_test_01")

    assert result == sample_scenario


def test_duplicate_model_is_rejected(storage, sample_model):
    storage.register_model(sample_model)

    with pytest.raises(ValueError):
        storage.register_model(sample_model)


def test_duplicate_scenario_is_rejected(storage, sample_model, sample_scenario):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)

    with pytest.raises(ValueError):
        storage.create_scenario(sample_scenario)


def test_scenario_requires_existing_model(storage, sample_scenario):
    with pytest.raises(KeyError):
        storage.create_scenario(sample_scenario)


def test_update_scenario(storage, sample_model, sample_scenario):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)

    updated_scenario = sample_scenario.copy()
    updated_scenario["name"] = "Updated Pump Failure"

    storage.update_scenario("scenario_test_01", updated_scenario)

    result = storage.get_scenario("scenario_test_01")

    assert result["name"] == "Updated Pump Failure"


def test_delete_scenario(storage, sample_model, sample_scenario):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)

    storage.delete_scenario("scenario_test_01")

    assert storage.get_scenario("scenario_test_01") is None


def test_cannot_delete_scenario_with_historical_runs(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)
    storage.save_run(sample_run)

    with pytest.raises(ValueError, match="historical runs"):
        storage.delete_scenario("scenario_test_01")

    assert storage.get_scenario("scenario_test_01") is not None


def test_delete_missing_scenario(storage):
    with pytest.raises(KeyError, match="Scenario does not exist"):
        storage.delete_scenario("missing_scenario")


def test_save_and_get_run(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)

    storage.save_run(sample_run)

    result = storage.get_run("run_test_01")

    assert result["run_id"] == "run_test_01"
    assert result["scenario_id"] == "scenario_test_01"
    assert result["model_id"] == "ctown"
    assert result["status"] == "pending"


def test_run_status_transitions(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)
    storage.save_run(sample_run)

    storage.update_run_status("run_test_01", "running")
    assert storage.get_run("run_test_01")["status"] == "running"

    storage.update_run_status("run_test_01", "succeeded")
    assert storage.get_run("run_test_01")["status"] == "succeeded"


def test_invalid_run_status_transition(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)
    storage.save_run(sample_run)

    storage.update_run_status("run_test_01", "running")
    storage.update_run_status("run_test_01", "succeeded")

    with pytest.raises(ValueError):
        storage.update_run_status("run_test_01", "pending")


def test_run_status_history(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)
    storage.save_run(sample_run)

    storage.update_run_status("run_test_01", "running")
    storage.update_run_status("run_test_01", "succeeded")

    result = storage.get_run("run_test_01")
    history = result["status_history"]

    assert len(history) == 3
    assert history[0]["new_status"] == "pending"
    assert history[1]["new_status"] == "running"
    assert history[2]["new_status"] == "succeeded"


def test_list_runs_with_filters(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)

    # Create a second model and scenario.
    second_model = {
        "model_id": "net1",
        "name": "Net1",
        "description": "Second test network",
    }
    storage.register_model(second_model)

    second_scenario = sample_scenario.copy()
    second_scenario["scenario_id"] = "scenario_test_02"
    second_scenario["model_id"] = "net1"
    storage.create_scenario(second_scenario)

    # Save two runs.
    storage.save_run(sample_run)

    second_run = sample_run.copy()
    second_run["run_id"] = "run_test_02"
    second_run["scenario_id"] = "scenario_test_02"
    second_run["model_id"] = "net1"
    storage.save_run(second_run)

    # Test the filters.
    assert len(storage.list_runs(scenario_id="scenario_test_01")) == 1
    assert len(storage.list_runs(model_id="net1")) == 1
    assert len(storage.list_runs(status="pending")) == 2


def test_cannot_delete_scenario_with_existing_run(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)
    storage.save_run(sample_run)

    with pytest.raises(ValueError):
        storage.delete_scenario("scenario_test_01")

    # Verify the scenario still exists.
    assert storage.get_scenario("scenario_test_01") is not None


def test_missing_scenario_operations(storage):
    with pytest.raises(KeyError):
        storage.update_scenario(
            "missing_scenario",
            {"scenario_id": "missing_scenario"},
        )

    with pytest.raises(KeyError):
        storage.delete_scenario("missing_scenario")


def test_storage_returns_independent_copies(
    storage, sample_model, sample_scenario
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)

    retrieved = storage.get_scenario("scenario_test_01")

    # Change the retrieved copy.
    retrieved["name"] = "Accidentally Modified"
    retrieved["target_components"].append("PU2")

    # The stored version should remain unchanged.
    original = storage.get_scenario("scenario_test_01")

    assert original["name"] == "Test Pump Failure"
    assert original["target_components"] == ["PU1"]


def test_save_and_get_run_summary(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)
    storage.save_run(sample_run)

    summary = {
        "baseline": {
            "simulator": "WNTRSimulator",
            "elapsed_seconds": 2.5,
            "converged": True,
        },
        "attack": {
            "simulator": "WNTRSimulator",
            "elapsed_seconds": 3.1,
            "converged": True,
        },
    }

    storage.save_run_summary("run_test_01", summary)

    result = storage.get_run_summary("run_test_01")

    assert result == summary

    with pytest.raises(ValueError, match="Summary already exists"):
        storage.save_run_summary("run_test_01", summary)


def test_save_and_get_run_details(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)
    storage.save_run(sample_run)

    results = {
        "baseline": {
            "node": {
                "pressure": pd.DataFrame(
                    {"J1": [45.2, 44.9], "J2": [43.8, 43.5]},
                    index=[0, 3600],
                )
            },
            "link": {},
        },
        "attack": {
            "node": {
                "pressure": pd.DataFrame(
                    {"J1": [45.2, 30.1], "J2": [43.8, 29.5]},
                    index=[0, 3600],
                )
            },
            "link": {},
        },
    }

    storage.save_run_details("run_test_01", results)

    details = storage.get_run_details("run_test_01")

    assert len(details) == 8
    assert details[0]["case_name"] == "baseline"
    assert details[0]["component_type"] == "node"
    assert details[0]["metric_name"] == "pressure"

    with pytest.raises(ValueError, match="Details already exist"):
        storage.save_run_details("run_test_01", results)


def test_results_require_existing_run(storage):
    summary = {"baseline": {"converged": True}}
    details = {"baseline": {"node": {}, "link": {}}}

    with pytest.raises(KeyError):
        storage.save_run_summary("missing_run", summary)

    with pytest.raises(KeyError):
        storage.save_run_details("missing_run", details)


def test_duplicate_run_is_rejected(
    storage, sample_model, sample_scenario, sample_run
):
    storage.register_model(sample_model)
    storage.create_scenario(sample_scenario)
    storage.save_run(sample_run)

    with pytest.raises(ValueError, match="Run already exists"):
        storage.save_run(sample_run)