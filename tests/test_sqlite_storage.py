import pandas as pd
import pytest

from ward.storage.sqlite import SQLiteStorage


@pytest.fixture
def storage(tmp_path):
    """Create a fresh temporary SQLite database for each test."""
    db_path = tmp_path / "test_storage.sqlite"
    db = SQLiteStorage(db_path)

    model = {
        "model_id": "ctown",
        "name": "C-Town",
        "description": "Test water network",
    }
    db.register_model(model)

    scenario = {
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
    db.create_scenario(scenario)

    run = {
        "run_id": "run_test_01",
        "scenario_id": "scenario_test_01",
        "model_id": "ctown",
        "status": "pending",
    }
    db.save_run(run)

    yield db
    db.close()


def test_save_and_get_run_summary(storage):
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


def test_save_and_get_run_details(storage):
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
    assert any(
        detail["case_name"] == "attack"
        and detail["component_id"] == "J1"
        and detail["time_seconds"] == 3600.0
        and detail["value"] == 30.1
        for detail in details
    )

    with pytest.raises(ValueError, match="Details already exist"):
        storage.save_run_details("run_test_01", results)


def test_results_require_existing_run(storage):
    summary = {"baseline": {"converged": True}}
    details = {"baseline": {"node": {}, "link": {}}}

    with pytest.raises(KeyError):
        storage.save_run_summary("missing_run", summary)

    with pytest.raises(KeyError):
        storage.save_run_details("missing_run", details)


def test_data_persists_after_reopening_database(tmp_path):
    db_path = tmp_path / "persistence_test.sqlite"

    # Create the database and save data.
    db = SQLiteStorage(db_path)

    db.register_model(
        {
            "model_id": "ctown",
            "name": "C-Town",
            "description": "Test network",
        }
    )

    scenario = {
        "schema_version": "1.0",
        "scenario_id": "scenario_test_01",
        "name": "Test Scenario",
        "description": "Test",
        "attack_type": "pump_failure",
        "model_id": "ctown",
        "target_components": ["PU1"],
        "timing": {"start_time_sec": 3600, "duration_sec": 1800},
        "parameters": {},
    }
    db.create_scenario(scenario)

    run = {
        "run_id": "run_test_01",
        "scenario_id": "scenario_test_01",
        "model_id": "ctown",
        "status": "pending",
    }
    db.save_run(run)

    # Save the run summary.
    summary = {"baseline": {"converged": True}}
    db.save_run_summary("run_test_01", summary)

    # Save individual simulation measurements.
    results = {
        "baseline": {
            "node": {
                "pressure": pd.DataFrame(
                    {"J1": [45.2, 44.9]},
                    index=[0, 3600],
                )
            },
            "link": {},
        },
        "attack": {
            "node": {
                "pressure": pd.DataFrame(
                    {"J1": [45.2, 30.1]},
                    index=[0, 3600],
                )
            },
            "link": {},
        },
    }

    db.save_run_details("run_test_01", results)

    # Close the database.
    db.close()

    # Reopen the same database.
    db = SQLiteStorage(db_path)

    # Verify that all original data still exists.
    assert db.get_model("ctown")["name"] == "C-Town"
    assert db.get_scenario("scenario_test_01") == scenario
    assert db.get_run("run_test_01")["status"] == "pending"
    assert db.get_run_summary("run_test_01") == summary

    # Verify that individual measurements also persisted.
    details = db.get_run_details("run_test_01")

    assert len(details) == 4
    assert any(
        detail["case_name"] == "attack"
        and detail["component_id"] == "J1"
        and detail["time_seconds"] == 3600.0
        and detail["value"] == 30.1
        for detail in details
    )

    db.close()


def test_delete_scenario(tmp_path):
    db_path = tmp_path / "delete_test.sqlite"
    db = SQLiteStorage(db_path)

    db.register_model(
        {
            "model_id": "ctown",
            "name": "C-Town",
            "description": "Test network",
        }
    )

    scenario = {
        "schema_version": "1.0",
        "scenario_id": "scenario_test_01",
        "name": "Test Scenario",
        "description": "Test",
        "attack_type": "pump_failure",
        "model_id": "ctown",
        "target_components": ["PU1"],
        "timing": {"start_time_sec": 3600, "duration_sec": 1800},
        "parameters": {},
    }

    db.create_scenario(scenario)
    db.delete_scenario("scenario_test_01")

    assert db.get_scenario("scenario_test_01") is None

    db.close()


def test_cannot_delete_scenario_with_historical_runs(storage):
    with pytest.raises(ValueError, match="historical runs"):
        storage.delete_scenario("scenario_test_01")

    assert storage.get_scenario("scenario_test_01") is not None


def test_delete_missing_scenario(storage):
    with pytest.raises(KeyError, match="Scenario does not exist"):
        storage.delete_scenario("missing_scenario")


def test_duplicate_scenario_rejected(storage):
    scenario = storage.get_scenario("scenario_test_01")

    with pytest.raises(ValueError, match="already exists"):
        storage.create_scenario(scenario)


def test_duplicate_run_rejected(storage):
    run = storage.get_run("run_test_01")

    with pytest.raises(ValueError, match="Run already exists"):
        storage.save_run(run)


def test_run_status_transitions(storage):
    storage.update_run_status("run_test_01", "running")
    assert storage.get_run("run_test_01")["status"] == "running"

    storage.update_run_status("run_test_01", "succeeded")
    assert storage.get_run("run_test_01")["status"] == "succeeded"

    with pytest.raises(ValueError):
        storage.update_run_status("run_test_01", "failed")


def test_duplicate_model_rejected(storage):
    model = {
        "model_id": "ctown",
        "name": "C-Town",
        "description": "Test water network",
    }

    with pytest.raises(ValueError, match="Model already exists"):
        storage.register_model(model)


def test_invalid_run_status_transition(storage):
    with pytest.raises(ValueError):
        storage.update_run_status("run_test_01", "succeeded")

    assert storage.get_run("run_test_01")["status"] == "pending"


def test_run_status_history(storage):
    storage.update_run_status("run_test_01", "running")
    storage.update_run_status("run_test_01", "succeeded")

    run = storage.get_run("run_test_01")
    history = run["status_history"]

    assert len(history) == 3
    assert history[0]["new_status"] == "pending"
    assert history[1]["new_status"] == "running"
    assert history[2]["new_status"] == "succeeded"


def test_database_migration_is_recorded(storage):
    result = storage.connection.execute(
        "SELECT version FROM schema_migrations"
    ).fetchall()

    versions = [row["version"] for row in result]

    assert 1 in versions


def test_migrations_are_not_applied_twice(tmp_path):
    db_path = tmp_path / "migration_test.sqlite"

    db = SQLiteStorage(db_path)
    db.close()

    # Reopen the same database.
    db = SQLiteStorage(db_path)

    result = db.connection.execute(
        "SELECT COUNT(*) AS count FROM schema_migrations"
    ).fetchone()

    assert result["count"] == 1

    db.close()
