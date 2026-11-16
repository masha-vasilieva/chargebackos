CREATE TABLE IF NOT EXISTS sandbox_owners (
  id varchar(64) PRIMARY KEY, expires double precision NOT NULL
);
CREATE TABLE IF NOT EXISTS sandbox_runs (
  id varchar(64) PRIMARY KEY, owner varchar(64) NOT NULL REFERENCES sandbox_owners(id) ON DELETE CASCADE,
  agent_hash varchar(64) NOT NULL, created double precision NOT NULL,
  expires double precision NOT NULL, deletes double precision NOT NULL,
  consent boolean NOT NULL DEFAULT false, research_ref varchar(64), data jsonb NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS sandbox_runs_research_ref_unique
  ON sandbox_runs(research_ref) WHERE research_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS sandbox_runs_owner ON sandbox_runs(owner);
CREATE INDEX IF NOT EXISTS sandbox_runs_deletes ON sandbox_runs(deletes);
CREATE TABLE IF NOT EXISTS sandbox_limits (
  key varchar(128) PRIMARY KEY, bucket bigint NOT NULL, count integer NOT NULL
);
CREATE TABLE IF NOT EXISTS sandbox_control (
  id integer PRIMARY KEY CHECK (id = 1), paused boolean NOT NULL DEFAULT false
);
INSERT INTO sandbox_control(id, paused) VALUES (1, false) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS sandbox_retention_health (
  id integer PRIMARY KEY CHECK (id = 1),
  consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  startup_unverified boolean NOT NULL DEFAULT true
);
INSERT INTO sandbox_retention_health(id, consecutive_failures, startup_unverified)
VALUES (1, 0, true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS sandbox_oauth (
  id varchar(80) PRIMARY KEY, expires double precision NOT NULL, data jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS sandbox_oauth_expires ON sandbox_oauth(expires);

CREATE TABLE IF NOT EXISTS sandbox_usage_daily (
  activity_date date PRIMARY KEY,
  run_created bigint NOT NULL DEFAULT 0 CHECK (run_created >= 0),
  agent_action_accepted bigint NOT NULL DEFAULT 0 CHECK (agent_action_accepted >= 0),
  run_completed bigint NOT NULL DEFAULT 0 CHECK (run_completed >= 0)
);

CREATE TABLE IF NOT EXISTS sandbox_research_export_events (
  id bigserial PRIMARY KEY,
  record_ref varchar(64) NOT NULL,
  event_type varchar(32) NOT NULL CHECK (
    event_type IN ('contribution_enabled', 'consent_withdrawn', 'legacy_current')
  ),
  occurred_at double precision NOT NULL,
  scenario varchar(64) NOT NULL,
  consent_version varchar(32) NOT NULL,
  expires_at double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS sandbox_research_export_events_ref
  ON sandbox_research_export_events(record_ref);

CREATE TABLE IF NOT EXISTS sandbox_research_daily (
  activity_date date NOT NULL,
  scenario varchar(64) NOT NULL,
  transition varchar(32) NOT NULL CHECK (
    transition IN ('contribution_enabled', 'consent_withdrawn')
  ),
  count bigint NOT NULL DEFAULT 0 CHECK (count >= 0),
  PRIMARY KEY (activity_date, scenario, transition)
);

CREATE TABLE IF NOT EXISTS sandbox_research_export_audit (
  id bigserial PRIMARY KEY,
  requested_at double precision NOT NULL,
  key_identity varchar(64) NOT NULL,
  operation varchar(16) NOT NULL CHECK (operation IN ('export', 'stats')),
  outcome varchar(32) NOT NULL,
  records_count integer NOT NULL DEFAULT 0 CHECK (records_count >= 0),
  withdrawals_count integer NOT NULL DEFAULT 0 CHECK (withdrawals_count >= 0),
  cursor_from bigint,
  cursor_to bigint,
  manifest_checksum varchar(64)
);
