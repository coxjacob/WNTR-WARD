"""Shared interface for WARD storage implementations."""

import json
from typing import Any, Protocol

ALLOWED_TRANSITIONS = {
    "pending": {"running", "failed"},
    "running": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}


def run_metadata(run: dict[str, Any]) -> dict[str, Any]:
    """Copy JSON run configuration while rejecting live Python objects."""
    unknown_fields = run.keys() - {
        "run_id",
        "scenario_id",
        "model_id",
        "status",
        "metadata",
    }
    if unknown_fields:
        raise ValueError(
            f"Put run configuration in metadata: {', '.join(sorted(unknown_fields))}"
        )
    metadata = run.get("metadata", {})
    if not isinstance(metadata, dict):
        raise TypeError("Run metadata must be a JSON object")
    return json.loads(json.dumps(metadata, allow_nan=False))


class Storage(Protocol):
    """Operations supported by all WARD storage implementations."""

    # Model operations
    def register_model(self, model: dict[str, Any]) -> None:
        """Register a water network model."""
        ...

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Retrieve a model by ID."""
        ...

    # Scenario operations
    def create_scenario(self, scenario: dict[str, Any]) -> None:
        """Create a scenario. Reject duplicate scenario IDs."""
        ...

    def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        """Retrieve a scenario by ID."""
        ...

    def list_scenarios(self, model_id: str | None = None) -> list[dict[str, Any]]:
        """List scenarios, optionally filtered by model."""
        ...

    def update_scenario(self, scenario_id: str, scenario: dict[str, Any]) -> None:
        """Update an existing scenario without changing its ID."""
        ...

    def delete_scenario(self, scenario_id: str) -> None:
        """Delete a scenario if no historical runs depend on it."""
        ...

    # Simulation run operations
    def save_run(self, run: dict[str, Any]) -> None:
        """Save a pending or running record and its scenario snapshot.

        Parameters
        ----------
        run : dict
            Required keys are run_id, scenario_id, model_id, and status.
            Optional metadata contains JSON configuration such as model content
            identity, simulator version, duration, and timesteps. Duplicate IDs
            raise ValueError. Missing scenarios or models raise KeyError.
        """
        ...

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Retrieve a run and its saved results."""
        ...

    def list_runs(
        self,
        scenario_id: str | None = None,
        model_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List runs with optional filters."""
        ...

    def update_run_status(
        self,
        run_id: str,
        new_status: str,
        details: str | None = None,
    ) -> None:
        """Change a run's status and record its history."""
        ...

    def save_run_summary(self, run_id: str, summary: dict[str, Any]) -> None:
        """Save a JSON summary once. Reject missing runs and duplicate summaries."""
        ...

    def get_run_summary(self, run_id: str) -> dict[str, Any] | None:
        """Return a summary copy, or None when no summary is saved."""
        ...

    def save_run_details(self, run_id: str, results: dict[str, Any]) -> None:
        """Save baseline/attack node/link metric DataFrames once per run."""
        ...

    def get_run_details(self, run_id: str) -> list[dict[str, Any]]:
        """Return measurement records sorted by case, component, metric, and time."""
        ...

    def close(self) -> None:
        """Release storage resources."""
        ...
