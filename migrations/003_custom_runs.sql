CREATE TABLE runs_new (
    run_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    model_id TEXT NOT NULL REFERENCES models(model_id) ON DELETE RESTRICT,
    scenario_snapshot TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    details_saved INTEGER NOT NULL DEFAULT 0,
    library_scenario_id TEXT REFERENCES scenarios(scenario_id) ON DELETE RESTRICT
);
INSERT INTO runs_new SELECT *, scenario_id FROM runs;
DROP TABLE runs;
ALTER TABLE runs_new RENAME TO runs;
INSERT INTO schema_migrations (version) VALUES (3);
