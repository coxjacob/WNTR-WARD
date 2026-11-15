
import json
import pandas as pd
from ward.storage.sqlite import SQLiteStorage

db = SQLiteStorage("data/task8_test.sqlite")


model = {
    "model_id": "ctown",
    "name": "C-Town",
    "description": "C-Town water distribution network",
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


print("Retrieved scenario:")
print(db.get_scenario("scenario_test_01"))


print("\nAll scenarios:")
print(db.list_scenarios())


scenario["name"] = "Updated Pump Failure"
db.update_scenario("scenario_test_01", scenario)

print("\nUpdated scenario:")
print(db.get_scenario("scenario_test_01"))


try:
    db.create_scenario(scenario)
except ValueError as exc:
    print("\nDuplicate rejected:", exc)


run = {
    "run_id": "run_test_01",
    "scenario_id": "scenario_test_01",
    "model_id": "ctown",
    "status": "pending",
}

db.save_run(run)


print("\nTesting result details:")

fake_results = {
    "baseline": {
        "node": {
            "pressure": pd.DataFrame(
                {
                    "J1": [45.2, 44.9],
                    "J2": [43.8, 43.5],
                },
                index=[0, 3600],
            )
        },
        "link": {},
    },
    "attack": {
        "node": {
            "pressure": pd.DataFrame(
                {
                    "J1": [45.2, 30.1],
                    "J2": [43.8, 29.5],
                },
                index=[0, 3600],
            )
        },
        "link": {},
    },
}

db.save_run_details("run_test_01", fake_results)

saved_details = db.connection.execute(
    """
    SELECT *
    FROM result_details
    WHERE run_id = ?
    ORDER BY case_name, component_id, time_seconds
    """,
    ("run_test_01",),
).fetchall()

for detail in saved_details:
    print(dict(detail))


    print("\nTesting result detail retrieval:")
retrieved_details = db.get_run_details("run_test_01")

print("Retrieved measurement count:", len(retrieved_details))

for detail in retrieved_details:
    print(detail)

assert len(retrieved_details) == 8
print("Result detail retrieval passed!")


print("\nTesting run summary:")

summary = {
    "baseline": {
        "simulator": "WNTRSimulator",
        "wntr_version": "test",
        "elapsed_seconds": 2.5,
        "converged": True,
        "warnings": [],
        "failure": None,
    },
    "attack": {
        "simulator": "WNTRSimulator",
        "wntr_version": "test",
        "elapsed_seconds": 3.1,
        "converged": True,
        "warnings": [],
        "failure": None,
    },
}

db.save_run_summary("run_test_01", summary)

saved_summary = db.connection.execute(
    "SELECT summary_json FROM result_summaries WHERE run_id = ?",
    ("run_test_01",),
).fetchone()

print("Saved summary:", json.loads(saved_summary["summary_json"]))

print("\nTesting summary retrieval:")

retrieved_summary = db.get_run_summary("run_test_01")

print("Retrieved summary:", retrieved_summary)


saved_run = db.get_run("run_test_01")

print("Retrieved run:", saved_run)
print("Scenario snapshot:", saved_run["scenario_snapshot"])
print("Status history:", saved_run["status_history"])

print("\nAll runs:")
print(db.list_runs())

print("\nPending runs:")
print(db.list_runs(status="pending"))

print("\nRuns for C-Town:")
print(db.list_runs(model_id="ctown"))

print("\nRuns for scenario_test_01:")
print(db.list_runs(scenario_id="scenario_test_01"))

print("\nUpdating run to running:")
db.update_run_status(
    "run_test_01",
    "running",
    details="Simulation started",
)

print(db.get_run("run_test_01"))

print("\nUpdating run to succeeded:")
db.update_run_status(
    "run_test_01",
    "succeeded",
    details="Simulation completed successfully",
)

print(db.get_run("run_test_01"))

print("\nTesting invalid status transition:")

try:
    db.update_run_status(
        "run_test_01",
        "running",
        details="Trying to restart completed run",
    )
except ValueError as exc:
    print("Invalid transition rejected:", exc)

print("\nCreating a second run:")

failed_run = {
    "run_id": "run_test_02",
    "scenario_id": "scenario_test_01",
    "model_id": "ctown",
    "status": "pending",
}

db.save_run(failed_run)

db.update_run_status(
    "run_test_02",
    "running",
    details="Simulation started",
)

db.update_run_status(
    "run_test_02",
    "failed",
    details="Simulation failed due to convergence error",
)

print(db.get_run("run_test_02"))

print("\nTesting missing run:")

try:
    db.update_run_status(
        "run_does_not_exist",
        "running",
    )
except KeyError as exc:
    print("Missing run rejected:", exc)

try:
    db.delete_scenario("scenario_test_01")
except ValueError as exc:
    print("\nDeletion rejected:", exc)

print("\nScenario still exists:")
print(db.get_scenario("scenario_test_01"))

db.close()