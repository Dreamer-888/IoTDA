-- Run once on an existing database before starting the updated backend.
-- New databases initialized with mysql_schema.sql already have this column.
SET SESSION time_zone = '+00:00';
ALTER TABLE users
  ADD COLUMN session_version BIGINT UNSIGNED NOT NULL DEFAULT 1
  AFTER password_hash;
