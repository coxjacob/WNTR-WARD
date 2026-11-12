
"""Shared interface for WARD storage implementations."""

from typing import Any, Protocol


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

    def list_scenarios(
        self, model_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List scenarios, optionally filtered by model."""
        ...

    def update_scenario(
        self, scenario_id: str, scenario: dict[str, Any]
    ) -> None:
        """Update an existing scenario without changing its ID."""
        ...

    def delete_scenario(self, scenario_id: str) -> None:
        """Delete a scenario if no historical runs depend on it."""
        ...

    # Simulation run operations
    def save_run(self, run: dict[str, Any]) -> None:
        """Save a simulation run and its results."""
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

    def close(self) -> None:
        """Release storage resources."""
        ...
