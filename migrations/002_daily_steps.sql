-- Run against the existing iot_visualization database.
-- This migration adds one table and preserves existing user/device data.
CREATE TABLE IF NOT EXISTS daily_steps (
  user_id BIGINT UNSIGNED NOT NULL,
  steps BIGINT UNSIGNED NOT NULL DEFAULT 0,
  record_date DATE NOT NULL,
  PRIMARY KEY (user_id, record_date),
  CONSTRAINT fk_daily_steps_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB;
