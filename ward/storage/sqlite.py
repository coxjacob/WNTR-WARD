
"""SQLite implementation of WARD storage."""

import json
import math
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteStorage:
    """Store WARD data persistently in a SQLite database."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

        
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row

        
        self.connection.execute("PRAGMA foreign_keys = ON")


        self._apply_migrations()



    def register_model(self, model: dict[str, Any]) -> None:
        """Register a water network model."""

        try:
            self.connection.execute(
                """
                INSERT INTO models (model_id, name, description)
                VALUES (?, ?, ?)
                """,
                (
                    model["model_id"],
                    model["name"],
                    model.get("description", ""),
                ),
            )
            self.connection.commit()

        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError(
                f"Model already exists: {model['model_id']}"
            ) from exc


    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Retrieve a model by ID."""

        row = self.connection.execute(
            """
            SELECT model_id, name, description, created_at, updated_at
            FROM models
            WHERE model_id = ?
            """,
            (model_id,),
        ).fetchone()

        return dict(row) if row is not None else None


    def create_scenario(self, scenario: dict[str, Any]) -> None:
        """Create a scenario and store its definition as JSON."""

        scenario_id = scenario["scenario_id"]
        model_id = scenario["model_id"]

        model = self.get_model(model_id)

        if model is None:
            raise KeyError(f"Model does not exist: {model_id}")

        existing = self.get_scenario(scenario_id)

        if existing is not None:
            raise ValueError(f"Scenario already exists: {scenario_id}")

        scenario_json = json.dumps(scenario)

        try:
            self.connection.execute(
                """
                INSERT INTO scenarios (
                    scenario_id,
                    model_id,
                    schema_version,
                    scenario_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    scenario_id,
                    model_id,
                    scenario.get("schema_version", "1.0"),
                    scenario_json,
                ),
            )
            self.connection.commit()

        except sqlite3.IntegrityError:
            self.connection.rollback()
            raise


    def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        """Retrieve a scenario and reconstruct its JSON definition."""

        row = self.connection.execute(
            """
            SELECT scenario_json
            FROM scenarios
            WHERE scenario_id = ?
            """,
            (scenario_id,),
        ).fetchone()

        if row is None:
            return None

        return json.loads(row["scenario_json"])

    
    def list_scenarios(
        self, model_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List scenarios, optionally filtered by model."""

        if model_id is None:
            rows = self.connection.execute(
                """
                SELECT scenario_json
                FROM scenarios
                ORDER BY scenario_id
                """
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT scenario_json
                FROM scenarios
                WHERE model_id = ?
                ORDER BY scenario_id
                """,
                (model_id,),
            ).fetchall()

        return [
            json.loads(row["scenario_json"])
            for row in rows
        ]


    def update_scenario(
        self, scenario_id: str, scenario: dict[str, Any]
    ) -> None:
        """Update an existing scenario."""

        if self.get_scenario(scenario_id) is None:
            raise KeyError(f"Scenario does not exist: {scenario_id}")

        if scenario["scenario_id"] != scenario_id:
            raise ValueError("Scenario ID cannot be changed")

        model_id = scenario["model_id"]

        if self.get_model(model_id) is None:
            raise KeyError(f"Model does not exist: {model_id}")

        scenario_json = json.dumps(scenario)

        try:
            self.connection.execute(
                """
                UPDATE scenarios
                SET model_id = ?,
                    schema_version = ?,
                    scenario_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE scenario_id = ?
                """,
                (
                    model_id,
                    scenario.get("schema_version", "1.0"),
                    scenario_json,
                    scenario_id,
                ),
            )
            self.connection.commit()

        except sqlite3.IntegrityError:
            self.connection.rollback()
            raise


    def delete_scenario(self, scenario_id: str) -> None:
        """Delete a scenario if no historical runs depend on it."""

        if self.get_scenario(scenario_id) is None:
            raise KeyError(f"Scenario does not exist: {scenario_id}")

        try:
            self.connection.execute(
                """
                DELETE FROM scenarios
                WHERE scenario_id = ?
                """,
                (scenario_id,),
            )
            self.connection.commit()

        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError(
                "Cannot delete a scenario with historical runs"
            ) from exc


    def save_run(self, run: dict[str, Any]) -> None:
        """Save a simulation run and its scenario snapshot."""

        run_id = run["run_id"]
        scenario_id = run["scenario_id"]
        model_id = run["model_id"]
        status = run["status"]

        
        existing = self.connection.execute(
            "SELECT run_id FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

        if existing is not None:
            raise ValueError(f"Run already exists: {run_id}")

        
        scenario = self.get_scenario(scenario_id)

        if scenario is None:
            raise KeyError(f"Scenario does not exist: {scenario_id}")

       
        if self.get_model(model_id) is None:
            raise KeyError(f"Model does not exist: {model_id}")

        if scenario["model_id"] != model_id:
            raise ValueError("Run model does not match scenario model")

        if status not in {"pending", "running"}:
            raise ValueError(
                "A new run must start as pending or running"
            )

       
        scenario_snapshot = json.dumps(scenario)

        try:
            self.connection.execute(
                """
                INSERT INTO runs (
                    run_id,
                    scenario_id,
                    model_id,
                    scenario_snapshot,
                    status,
                    started_at
                )
                VALUES (?, ?, ?, ?, ?, 
                    CASE WHEN ? = 'running'
                    THEN CURRENT_TIMESTAMP ELSE NULL END)
                """,
                (
                    run_id,
                    scenario_id,
                    model_id,
                    scenario_snapshot,
                    status,
                    status,
                ),
            )

            
            self.connection.execute(
                """
                INSERT INTO run_status_history (
                    run_id,
                    previous_status,
                    new_status,
                    details
                )
                VALUES (?, NULL, ?, 'Run created')
                """,
                (run_id, status),
            )

            self.connection.commit()

        except sqlite3.IntegrityError:
            self.connection.rollback()
            raise


    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Retrieve a run, its scenario snapshot, and status history."""

        row = self.connection.execute(
            """
            SELECT *
            FROM runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

        if row is None:
            return None

        run = dict(row)

        
        run["scenario_snapshot"] = json.loads(
            run["scenario_snapshot"]
        )

        
        history_rows = self.connection.execute(
            """
            SELECT previous_status, new_status, changed_at, details
            FROM run_status_history
            WHERE run_id = ?
            ORDER BY history_id
            """,
            (run_id,),
        ).fetchall()

        run["status_history"] = [
            dict(history_row) for history_row in history_rows
        ]

        return run


    def list_runs(
        self,
        scenario_id: str | None = None,
        model_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List saved runs, optionally filtering by scenario, model, or status."""

        query = """
            SELECT
                run_id,
                scenario_id,
                model_id,
                status,
                started_at,
                completed_at,
                error_message,
                created_at
            FROM runs
        """

        conditions = []
        parameters = []

        if scenario_id is not None:
            conditions.append("scenario_id = ?")
            parameters.append(scenario_id)

        if model_id is not None:
            conditions.append("model_id = ?")
            parameters.append(model_id)

        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at"

        rows = self.connection.execute(
            query, parameters
        ).fetchall()

        return [dict(row) for row in rows]


    def update_run_status(
        self,
        run_id: str,
        new_status: str,
        details: str | None = None,
    ) -> None:
        """Update a run's status and record the change in its history."""

        allowed_transitions = {
            "pending": {"running", "failed"},
            "running": {"succeeded", "failed"},
            "succeeded": set(),
            "failed": set(),
        }

        row = self.connection.execute(
            """
            SELECT status
            FROM runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

        if row is None:
            raise KeyError(f"Run does not exist: {run_id}")

        current_status = row["status"]

        if new_status not in allowed_transitions[current_status]:
            raise ValueError(
                f"Invalid status transition: {current_status} -> {new_status}"
            )

        try:
            if new_status == "running":
                self.connection.execute(
                    """
                    UPDATE runs
                    SET status = ?,
                        started_at = CURRENT_TIMESTAMP
                    WHERE run_id = ?
                    """,
                    (new_status, run_id),
                )

            elif new_status in {"succeeded", "failed"}:
                self.connection.execute(
                    """
                    UPDATE runs
                    SET status = ?,
                        completed_at = CURRENT_TIMESTAMP,
                        error_message = ?
                    WHERE run_id = ?
                    """,
                    (
                        new_status,
                        details if new_status == "failed" else None,
                        run_id,
                    ),
                )

            self.connection.execute(
                """
                INSERT INTO run_status_history (
                    run_id,
                    previous_status,
                    new_status,
                    details
                )
                VALUES (?, ?, ?, ?)
                """,
                (run_id, current_status, new_status, details),
            )

            self.connection.commit()

        except sqlite3.Error:
            self.connection.rollback()
            raise



    def _apply_migrations(self):
        """Apply database migrations that have not run yet."""

        migration_folder = (
            Path(__file__).resolve().parents[2] / "migrations"
        )

        migration_files = sorted(migration_folder.glob("*.sql"))

        
        table_exists = self.connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'schema_migrations'
            """
        ).fetchone()

        if table_exists:
            applied_versions = {
                row["version"]
                for row in self.connection.execute(
                    "SELECT version FROM schema_migrations"
                )
            }
        else:
            applied_versions = set()

        
        for migration_file in migration_files:
            version = int(migration_file.stem.split("_", maxsplit=1)[0])

            if version in applied_versions:
                continue

            sql = migration_file.read_text(encoding="utf-8")

            try:
                self.connection.executescript(
                    f"BEGIN;\n{sql}\nCOMMIT;"
                )
            except sqlite3.Error:
                self.connection.rollback()
                raise


    def save_run_summary(self, run_id: str, summary: dict[str, Any]) -> None:
        """Save simulation summary information as JSON."""

        run = self.get_run(run_id)

        if run is None:
            raise KeyError(f"Run does not exist: {run_id}")

        existing = self.connection.execute(
            "SELECT run_id FROM result_summaries WHERE run_id = ?",
            (run_id,),
        ).fetchone()

        if existing is not None:
            raise ValueError(f"Summary already exists for run: {run_id}")

        summary_json = json.dumps(summary)

        self.connection.execute(
            """
            INSERT INTO result_summaries (run_id, summary_json)
            VALUES (?, ?)
            """,
            (run_id, summary_json),
        )

        self.connection.commit()

    def get_run_summary(self, run_id: str) -> dict[str, Any] | None:
        """Retrieve the saved summary for a simulation run."""

        row = self.connection.execute(
            """
            SELECT summary_json
            FROM result_summaries
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

        if row is None:
            return None

        return json.loads(row["summary_json"])


    def save_run_details(self, run_id: str, results: dict[str, Any]) -> None:
        """Save individual node and link measurements for a simulation run."""

        if self.get_run(run_id) is None:
            raise KeyError(f"Run does not exist: {run_id}")

        existing = self.connection.execute(
            "SELECT detail_id FROM result_details WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchone()

        if existing is not None:
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
                                (
                                    run_id,
                                    case_name,
                                    component_type,
                                    str(component_id),
                                    metric_name,
                                    float(time_seconds),
                                    float(value),
                                    "",
                                )
                            )

        try:
            self.connection.executemany(
                """
                INSERT INTO result_details (
                    run_id,
                    case_name,
                    component_type,
                    component_id,
                    metric_name,
                    time_seconds,
                    value,
                    unit
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )

            self.connection.commit()

        except sqlite3.Error:
            self.connection.rollback()
            raise


    def get_run_details(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieve individual measurements for a simulation run."""

        rows = self.connection.execute(
            """
            SELECT
                case_name,
                component_type,
                component_id,
                metric_name,
                time_seconds,
                value,
                unit
            FROM result_details
            WHERE run_id = ?
            ORDER BY case_name, component_type, metric_name,
                    component_id, time_seconds
            """,
            (run_id,),
        ).fetchall()

        return [dict(row) for row in rows]
        

    def close(self) -> None:
        """Close the database connection."""

        self.connection.close()

