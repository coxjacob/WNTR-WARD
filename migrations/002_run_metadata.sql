ALTER TABLE runs ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE runs ADD COLUMN details_saved INTEGER NOT NULL DEFAULT 0;
UPDATE runs SET details_saved = 1
WHERE EXISTS (SELECT 1 FROM result_details WHERE result_details.run_id = runs.run_id);

INSERT INTO schema_migrations (version) VALUES (2);
