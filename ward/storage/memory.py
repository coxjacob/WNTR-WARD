"""In-memory implementation of WARD storage."""

import math
from copy import deepcopy
from datetime import datetime, timezone

from ward.scenario import WaterScenario
from ward.storage.base import ALLOWED_TRANSITIONS, run_metadata

ALLOWED_STATUSES = {"pending", "running", "succeeded", "failed"}


class MemoryStorage:
    """Store WARD data temporarily in Python dictionaries."""

    def __init__(self):
        self.models = {}
        self.scenarios = {}
        self.runs = {}
        self.status_history = {}
        self.run_summaries = {}
        self.run_details = {}

    # Model operations

    def register_model(self, model):
        """Register a model. Reject duplicate IDs."""
        model_id = model["model_id"]

        if model_id in self.models:
            raise ValueError(f"Model already exists: {model_id}")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.models[model_id] = {
            "model_id": model_id,
            "name": model["name"],
            "description": model.get("description", ""),
            "created_at": now,
            "updated_at": now,
        }

    def get_model(self, model_id):
        """Return a model copy, or None for a missing ID."""
        model = self.models.get(model_id)
        return deepcopy(model) if model is not None else None

    # Scenario operations

    def create_scenario(self, scenario):
        """Validate and save scenario JSON. Reject duplicates and missing models."""
        scenario = WaterScenario.model_validate(scenario).model_dump(mode="json")
        scenario_id = scenario["scenario_id"]
        model_id = scenario["model_id"]

        if scenario_id in self.scenarios:
            raise ValueError(f"Scenario already exists: {scenario_id}")

        if model_id not in self.models:
            raise KeyError(f"Model does not exist: {model_id}")

        self.scenarios[scenario_id] = deepcopy(scenario)

    def get_scenario(self, scenario_id):
        """Return a scenario copy, or None for a missing ID."""
        scenario = self.scenarios.get(scenario_id)
        return deepcopy(scenario) if scenario is not None else None

    def list_scenarios(self, model_id=None):
        """Return scenario copies ordered by ID, optionally filtered by model."""
        scenarios = self.scenarios.values()

        if model_id is not None:
            scenarios = [
                scenario for scenario in scenarios if scenario["model_id"] == model_id
            ]

        return deepcopy(sorted(scenarios, key=lambda scenario: scenario["scenario_id"]))

    def update_scenario(self, scenario_id, scenario):
        """Replace a scenario with validated JSON while preserving its ID."""
        if scenario_id not in self.scenarios:
            raise KeyError(f"Scenario does not exist: {scenario_id}")

        if scenario["scenario_id"] != scenario_id:
            raise ValueError("Scenario ID cannot be changed")

        scenario = WaterScenario.model_validate(scenario).model_dump(mode="json")
        model_id = scenario["model_id"]

        if model_id not in self.models:
            raise KeyError(f"Model does not exist: {model_id}")

        self.scenarios[scenario_id] = deepcopy(scenario)

    def delete_scenario(self, scenario_id: str) -> None:
        """Delete a scenario. Reject missing IDs and historical run dependencies."""
        if scenario_id not in self.scenarios:
            raise KeyError(f"Scenario does not exist: {scenario_id}")

        if any(run["scenario_id"] == scenario_id for run in self.runs.values()):
            raise ValueError("Cannot delete a scenario with historical runs")

        del self.scenarios[scenario_id]

    # Simulation run operations

    def save_run(self, run, *, scenario_snapshot=None):
        """Save a pending or running record using the Storage run contract."""
        run_id = run["run_id"]
        scenario_id = run["scenario_id"]
        model_id = run["model_id"]
        status = run["status"]

        if run_id in self.runs:
            raise ValueError(f"Run already exists: {run_id}")

        scenario = (
            self.scenarios.get(scenario_id)
            if scenario_snapshot is None
            else WaterScenario.model_validate(scenario_snapshot).model_dump(mode="json")
        )
        if scenario is None:
            raise KeyError(f"Scenario does not exist: {scenario_id}")
        if scenario["scenario_id"] != scenario_id:
            raise ValueError("Run scenario ID does not match snapshot")

        if model_id not in self.models:
            raise KeyError(f"Model does not exist: {model_id}")

        if scenario["model_id"] != model_id:
            raise ValueError("Run model does not match scenario model")

        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid run status: {status}")

        if status not in {"pending", "running"}:
            raise ValueError("A new run must start as pending or running")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.runs[run_id] = {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "model_id": model_id,
            "status": status,
            "scenario_snapshot": deepcopy(scenario),
            "metadata": run_metadata(run),
            "created_at": now,
            "started_at": now if status == "running" else None,
            "completed_at": None,
            "error_message": None,
        }

        self.status_history[run_id] = [
            {
                "previous_status": None,
                "new_status": status,
                "details": "Run created",
                "changed_at": now,
            }
        ]

    def get_run(self, run_id):
        """Return a run snapshot and status history, or None for a missing ID."""
        run = self.runs.get(run_id)

        if run is None:
            return None

        result = deepcopy(run)
        result["status_history"] = deepcopy(self.status_history[run_id])

        return result

    def list_runs(
        self,
        scenario_id=None,
        model_id=None,
        status=None,
    ):
        """List run records with optional scenario, model, and status filters."""
        runs = []

        for run in self.runs.values():
            if scenario_id is not None and run["scenario_id"] != scenario_id:
                continue

            if model_id is not None and run["model_id"] != model_id:
                continue

            if status is not None and run["status"] != status:
                continue

            runs.append(
                {key: value for key, value in run.items() if key != "scenario_snapshot"}
            )

        return deepcopy(runs)

    def update_run_status(self, run_id, new_status, details=None):
        """Apply an allowed transition and record its timestamp and details."""
        if run_id not in self.runs:
            raise KeyError(f"Run does not exist: {run_id}")

        if new_status not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid run status: {new_status}")

        current_status = self.runs[run_id]["status"]

        if new_status not in ALLOWED_TRANSITIONS[current_status]:
            raise ValueError(
                f"Invalid status transition: {current_status} -> {new_status}"
            )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.runs[run_id]["status"] = new_status
        if new_status == "running":
            self.runs[run_id]["started_at"] = now
        else:
            self.runs[run_id]["completed_at"] = now
            self.runs[run_id]["error_message"] = (
                details if new_status == "failed" else None
            )

        self.status_history[run_id].append(
            {
                "previous_status": current_status,
                "new_status": new_status,
                "details": details,
                "changed_at": now,
            }
        )

    def save_run_summary(self, run_id, summary):
        """Save summary information for a simulation run."""
        if run_id not in self.runs:
            raise KeyError(f"Run does not exist: {run_id}")
        if run_id in self.run_summaries:
            raise ValueError(f"Summary already exists for run: {run_id}")

        self.run_summaries[run_id] = deepcopy(summary)

    def get_run_summary(self, run_id):
        """Retrieve summary information for a simulation run."""
        summary = self.run_summaries.get(run_id)
        return deepcopy(summary) if summary is not None else None

    def save_run_details(self, run_id, results):
        """Save individual measurements for a simulation run."""
        if run_id not in self.runs:
            raise KeyError(f"Run does not exist: {run_id}")
        if run_id in self.run_details:
            raise ValueError(f"Details already exist for run: {run_id}")

        records = []

        for case_name, case_results in results.items():
            for component_type in ("node", "link"):
                measurements = case_results.get(component_type, {})

                for metric_name, frame in measurements.items():
                    for time_seconds, row in frame.iterrows():
                        for component_id, value in row.items():
                            if not math.isfinite(float(value)):
                                continue

                            records.append(
                                {
                                    "case_name": case_name,
                                    "component_type": component_type,
                                    "component_id": str(component_id),
                                    "metric_name": metric_name,
                                    "time_seconds": float(time_seconds),
                                    "value": float(value),
                                    "unit": "",
                                }
                            )

        self.run_details[run_id] = records

    def get_run_details(self, run_id):
        """Retrieve individual measurements for a simulation run."""
        return deepcopy(
            sorted(
                self.run_details.get(run_id, []),
                key=lambda record: (
                    record["case_name"],
                    record["component_type"],
                    record["metric_name"],
                    record["component_id"],
                    record["time_seconds"],
                ),
            )
        )

    def close(self):
        """Clear all stored data."""
        self.models.clear()
        self.scenarios.clear()
        self.runs.clear()
        self.status_history.clear()
        self.run_summaries.clear()
        self.run_details.clear()
