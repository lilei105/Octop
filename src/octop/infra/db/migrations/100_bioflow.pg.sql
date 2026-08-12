-- Schema v100: BioFlow integration — bio script library + analysis workflow tasks.
CREATE TABLE IF NOT EXISTS bio_script_folders (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bio_scripts (
  id TEXT PRIMARY KEY,
  folder_id TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  tool_id TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL DEFAULT '1.0.0',
  file_path TEXT NOT NULL DEFAULT '',
  md_content TEXT NOT NULL DEFAULT '',
  inputs TEXT NOT NULL DEFAULT '[]',
  outputs TEXT NOT NULL DEFAULT '[]',
  runtime_min REAL NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0,
  weight REAL NOT NULL DEFAULT 0,
  verified INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1,
  valid_from BIGINT,
  valid_until BIGINT,
  uploaded_by INTEGER NOT NULL DEFAULT 0,
  verified_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bio_scripts_folder ON bio_scripts(folder_id);
CREATE INDEX IF NOT EXISTS idx_bio_scripts_avail ON bio_scripts(is_active, verified);

CREATE TABLE IF NOT EXISTS bio_analysis_tasks (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  thread_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planning',
  need_text TEXT NOT NULL DEFAULT '',
  candidates_json TEXT NOT NULL DEFAULT '[]',
  selected_candidate_json TEXT NOT NULL DEFAULT '',
  required_files_json TEXT NOT NULL DEFAULT '[]',
  file_mappings_json TEXT NOT NULL DEFAULT '[]',
  generated_code TEXT NOT NULL DEFAULT '',
  code_origin TEXT NOT NULL DEFAULT '',
  run_dir TEXT NOT NULL DEFAULT '',
  exec_status TEXT NOT NULL DEFAULT '',
  exec_started_at BIGINT,
  exec_finished_at BIGINT,
  exec_exit_code INTEGER,
  exec_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bio_tasks_agent_thread ON bio_analysis_tasks(agent_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_bio_tasks_user ON bio_analysis_tasks(user_id);

UPDATE _schema_version SET version = 100;
