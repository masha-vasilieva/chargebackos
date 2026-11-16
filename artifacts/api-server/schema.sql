-- Apply to a new external development database before starting the service.
-- Replit's managed production schema is handled by Publish, not startup DDL.
CREATE TABLE IF NOT EXISTS chargeback_demo_sessions (
    id varchar(64) PRIMARY KEY,
    state jsonb NOT NULL
);
