PRAGMA foreign_keys = ON;


CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS models (
    model_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

.
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    scenario_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (model_id)
        REFERENCES models(model_id)
        ON DELETE RESTRICT
);


CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    scenario_snapshot TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (scenario_id)
        REFERENCES scenarios(scenario_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (model_id)
        REFERENCES models(model_id)
        ON DELETE RESTRICT,

    CHECK (status IN ('pending', 'running', 'succeeded', 'failed'))
);


CREATE TABLE IF NOT EXISTS run_status_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details TEXT,

    FOREIGN KEY (run_id)
        REFERENCES runs(run_id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS result_summaries (
    run_id TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (run_id)
        REFERENCES runs(run_id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS result_details (
    detail_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    case_name TEXT NOT NULL,
    component_type TEXT NOT NULL,
    component_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    time_seconds REAL NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',

    FOREIGN KEY (run_id)
        REFERENCES runs(run_id)
        ON DELETE CASCADE,

    CHECK (case_name IN ('baseline', 'attack')),
    CHECK (component_type IN ('node', 'link'))
);



INSERT INTO schema_migrations (version)
VALUES (1);
