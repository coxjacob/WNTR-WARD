"""In-memory implementation of WARD storage."""

import math
from copy import deepcopy

ALLOWED_STATUSES = {"pending", "running", "succeeded", "failed"}

ALLOWED_TRANSITIONS = {
    "pending": {"running", "failed"},
    "running": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}


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
        model_id = model["model_id"]

        if model_id in self.models:
            raise ValueError(f"Model already exists: {model_id}")

        self.models[model_id] = deepcopy(model)

    def get_model(self, model_id):
        model = self.models.get(model_id)
        return deepcopy(model) if model is not None else None

    # Scenario operations

    def create_scenario(self, scenario):
        scenario_id = scenario["scenario_id"]
        model_id = scenario["model_id"]

        if scenario_id in self.scenarios:
            raise ValueError(f"Scenario already exists: {scenario_id}")

        if model_id not in self.models:
            raise KeyError(f"Model does not exist: {model_id}")

        self.scenarios[scenario_id] = deepcopy(scenario)

    def get_scenario(self, scenario_id):
        scenario = self.scenarios.get(scenario_id)
        return deepcopy(scenario) if scenario is not None else None

    def list_scenarios(self, model_id=None):
        scenarios = self.scenarios.values()

        if model_id is not None:
            scenarios = [
                scenario for scenario in scenarios if scenario["model_id"] == model_id
            ]

        return deepcopy(list(scenarios))

    def update_scenario(self, scenario_id, scenario):
        if scenario_id not in self.scenarios:
            raise KeyError(f"Scenario does not exist: {scenario_id}")

        if scenario["scenario_id"] != scenario_id:
            raise ValueError("Scenario ID cannot be changed")

        model_id = scenario["model_id"]

        if model_id not in self.models:
            raise KeyError(f"Model does not exist: {model_id}")

        self.scenarios[scenario_id] = deepcopy(scenario)

    def delete_scenario(self, scenario_id: str) -> None:
        if scenario_id not in self.scenarios:
            raise KeyError(f"Scenario does not exist: {scenario_id}")

        if any(run["scenario_id"] == scenario_id for run in self.runs.values()):
            raise ValueError("Cannot delete a scenario with historical runs")

        del self.scenarios[scenario_id]

    # Simulation run operations

    def save_run(self, run):
        run_id = run["run_id"]
        scenario_id = run["scenario_id"]
        model_id = run["model_id"]
        status = run["status"]

        if run_id in self.runs:
            raise ValueError(f"Run already exists: {run_id}")

        if scenario_id not in self.scenarios:
            raise KeyError(f"Scenario does not exist: {scenario_id}")

        if model_id not in self.models:
            raise KeyError(f"Model does not exist: {model_id}")

        if self.scenarios[scenario_id]["model_id"] != model_id:
            raise ValueError("Run model does not match scenario model")

        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid run status: {status}")

        if status not in {"pending", "running"}:
            raise ValueError("A new run must start as pending or running")

        self.runs[run_id] = deepcopy(run)

        self.status_history[run_id] = [
            {
                "previous_status": None,
                "new_status": status,
                "details": "Run created",
            }
        ]

    def get_run(self, run_id):
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
        runs = []

        for run in self.runs.values():
            if scenario_id is not None and run["scenario_id"] != scenario_id:
                continue

            if model_id is not None and run["model_id"] != model_id:
                continue

            if status is not None and run["status"] != status:
                continue

            runs.append(run)

        return deepcopy(runs)

    def update_run_status(self, run_id, new_status, details=None):
        if run_id not in self.runs:
            raise KeyError(f"Run does not exist: {run_id}")

        if new_status not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid run status: {new_status}")

        current_status = self.runs[run_id]["status"]

        if new_status not in ALLOWED_TRANSITIONS[current_status]:
            raise ValueError(
                f"Invalid status transition: {current_status} -> {new_status}"
            )

        self.runs[run_id]["status"] = new_status

        self.status_history[run_id].append(
            {
                "previous_status": current_status,
                "new_status": new_status,
                "details": details,
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
        return deepcopy(self.run_details.get(run_id, []))

    def close(self):
        """Clear all stored data."""
        self.models.clear()
        self.scenarios.clear()
        self.runs.clear()
        self.status_history.clear()
        self.run_summaries.clear()
        self.run_details.clear()
