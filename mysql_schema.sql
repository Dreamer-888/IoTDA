-- MySQL 8.0+; application timestamps are UTC.
SET SESSION time_zone = '+00:00';
CREATE DATABASE IF NOT EXISTS iot_visualization
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE iot_visualization;

CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL,
  email VARCHAR(254) NULL,
  password_hash VARCHAR(255) NOT NULL,
  session_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
    ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_users_username (username),
  UNIQUE KEY uk_users_email (email)
) ENGINE=InnoDB;

-- Daily totals combine all devices belonging to the same application user.
-- record_date is a calendar date in Asia/Shanghai, not a UTC date.
CREATE TABLE IF NOT EXISTS daily_steps (
  user_id BIGINT UNSIGNED NOT NULL,
  steps BIGINT UNSIGNED NOT NULL DEFAULT 0,
  record_date DATE NOT NULL,
  PRIMARY KEY (user_id, record_date),
  CONSTRAINT fk_daily_steps_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB;

-- One device belongs to one application user in the initial version.
-- Insert a row only after the cloud device has been registered and ownership verified.
CREATE TABLE IF NOT EXISTS devices (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NOT NULL,
  cloud_device_id VARCHAR(128) NOT NULL,
  cloud_product_id VARCHAR(128) NOT NULL,
  display_name VARCHAR(100) NOT NULL,
  hardware_model VARCHAR(100) NULL,
  sample_interval_ms INT UNSIGNED NULL,
  last_seen_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
    ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_devices_cloud_device_id (cloud_device_id),
  KEY idx_devices_user_id (user_id),
  CONSTRAINT fk_devices_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT chk_devices_sample_interval
    CHECK (sample_interval_ms IS NULL OR sample_interval_ms > 0)
) ENGINE=InnoDB;
