CREATE DATABASE IF NOT EXISTS enamad
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE enamad;

CREATE TABLE IF NOT EXISTS scrape_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP NULL DEFAULT NULL,
  start_page INT UNSIGNED NOT NULL,
  pages_requested INT UNSIGNED NOT NULL,
  pages_fetched INT UNSIGNED NOT NULL DEFAULT 0,
  records_saved INT UNSIGNED NOT NULL DEFAULT 0,
  status VARCHAR(32) NOT NULL DEFAULT 'running',
  notes TEXT NULL,
  PRIMARY KEY (id),
  KEY idx_scrape_runs_started_at (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS enamad_domains (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  enamad_id VARCHAR(64) NOT NULL,
  code VARCHAR(128) NOT NULL,
  domain VARCHAR(255) NOT NULL,
  business_name VARCHAR(512) NULL,
  owner_name VARCHAR(512) NULL,
  business_address VARCHAR(1024) NULL,
  phone VARCHAR(64) NULL,
  email VARCHAR(255) NULL,
  phone_type VARCHAR(16) NULL,
  mobile_phone VARCHAR(16) NULL,
  email_normalized VARCHAR(255) NULL,
  work_hours VARCHAR(128) NULL,
  province VARCHAR(128) NULL,
  city VARCHAR(128) NULL,
  rating TINYINT UNSIGNED NOT NULL DEFAULT 0,
  approve_date VARCHAR(32) NULL,
  expire_date VARCHAR(32) NULL,
  trustseal_url VARCHAR(512) NULL,
  enamad_status VARCHAR(16) NULL,
  source_page INT UNSIGNED NULL,
  source_row INT UNSIGNED NULL,
  scrape_run_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_enamad_record (enamad_id, code),
  KEY idx_domain (domain),
  KEY idx_business_name (business_name(191)),
  KEY idx_owner_name (owner_name(191)),
  KEY idx_province_city (province, city),
  KEY idx_scrape_run_id (scrape_run_id),
  KEY idx_source_order (source_page, source_row, id),
  KEY idx_phone_type (phone_type),
  KEY idx_rating (rating),
  KEY idx_updated_at (updated_at),
  KEY idx_approve_date (approve_date),
  KEY idx_email_normalized (email_normalized(64)),
  CONSTRAINT fk_enamad_domains_scrape_run
    FOREIGN KEY (scrape_run_id) REFERENCES scrape_runs (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS enamad_domain_services (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  enamad_id VARCHAR(64) NOT NULL,
  code VARCHAR(128) NOT NULL,
  row_num INT UNSIGNED NOT NULL,
  service_title VARCHAR(512) NOT NULL,
  license_issuer VARCHAR(512) NULL,
  license_number VARCHAR(128) NULL,
  valid_from VARCHAR(32) NULL,
  valid_to VARCHAR(32) NULL,
  status VARCHAR(64) NULL,
  scrape_run_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_service_row (enamad_id, code, row_num),
  KEY idx_service_title (service_title(191)),
  KEY idx_service_status (status),
  CONSTRAINT fk_services_domain
    FOREIGN KEY (enamad_id, code) REFERENCES enamad_domains (enamad_id, code)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scraper_state (
  state_key VARCHAR(64) NOT NULL,
  state_value VARCHAR(255) NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (state_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS bot_users (
  platform VARCHAR(16) NOT NULL DEFAULT 'telegram',
  user_id BIGINT NOT NULL,
  username VARCHAR(255) NULL,
  first_name VARCHAR(255) NULL,
  last_name VARCHAR(255) NULL,
  interaction_count INT UNSIGNED NOT NULL DEFAULT 0,
  last_action VARCHAR(64) NULL,
  first_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (platform, user_id),
  KEY idx_bot_users_last_seen (last_seen)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS admin_users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  display_name VARCHAR(128) NULL,
  role VARCHAR(32) NOT NULL DEFAULT 'admin',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_admin_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS projects (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name VARCHAR(128) NOT NULL,
  slug VARCHAR(64) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_by BIGINT UNSIGNED NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_projects_slug (slug),
  KEY idx_projects_active (is_active),
  CONSTRAINT fk_projects_creator
    FOREIGN KEY (created_by) REFERENCES admin_users (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS project_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  project_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  role VARCHAR(32) NOT NULL DEFAULT 'admin',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_project_member (project_id, user_id),
  KEY idx_members_user (user_id),
  CONSTRAINT fk_members_project
    FOREIGN KEY (project_id) REFERENCES projects (id)
    ON DELETE CASCADE,
  CONSTRAINT fk_members_user
    FOREIGN KEY (user_id) REFERENCES admin_users (id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_settings (
  project_id BIGINT UNSIGNED NOT NULL,
  setting_key VARCHAR(64) NOT NULL,
  setting_value TEXT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (project_id, setting_key),
  KEY idx_crm_settings_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS message_templates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  project_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(128) NOT NULL,
  channel VARCHAR(16) NOT NULL,
  provider VARCHAR(32) NOT NULL DEFAULT 'kavenegar',
  description TEXT NULL,
  kavenegar_template VARCHAR(128) NULL,
  token_mapping JSON NULL,
  sms_preview_text TEXT NULL,
  email_subject VARCHAR(512) NULL,
  email_body TEXT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_templates_channel (channel),
  KEY idx_templates_active (is_active),
  KEY idx_templates_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS automation_rules (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  project_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(128) NOT NULL,
  trigger_type VARCHAR(32) NOT NULL DEFAULT 'new_domain',
  template_id BIGINT UNSIGNED NOT NULL,
  channel VARCHAR(16) NOT NULL,
  mobile_only TINYINT(1) NOT NULL DEFAULT 1,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_rules_active (is_active),
  KEY idx_rules_project (project_id),
  CONSTRAINT fk_rules_template
    FOREIGN KEY (template_id) REFERENCES message_templates (id)
    ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS message_campaigns (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  project_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(128) NOT NULL,
  channel VARCHAR(16) NOT NULL,
  template_id BIGINT UNSIGNED NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'draft',
  target_type VARCHAR(32) NOT NULL DEFAULT 'manual',
  target_domain_ids JSON NULL,
  mobile_only TINYINT(1) NOT NULL DEFAULT 1,
  created_by BIGINT UNSIGNED NULL,
  total_count INT UNSIGNED NOT NULL DEFAULT 0,
  sent_count INT UNSIGNED NOT NULL DEFAULT 0,
  failed_count INT UNSIGNED NOT NULL DEFAULT 0,
  skipped_count INT UNSIGNED NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TIMESTAMP NULL DEFAULT NULL,
  finished_at TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_campaigns_status (status),
  KEY idx_campaigns_project (project_id),
  CONSTRAINT fk_campaigns_template
    FOREIGN KEY (template_id) REFERENCES message_templates (id)
    ON DELETE RESTRICT,
  CONSTRAINT fk_campaigns_admin
    FOREIGN KEY (created_by) REFERENCES admin_users (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS message_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  project_id BIGINT UNSIGNED NULL,
  campaign_id BIGINT UNSIGNED NULL,
  automation_rule_id BIGINT UNSIGNED NULL,
  domain_id BIGINT UNSIGNED NULL,
  channel VARCHAR(16) NOT NULL,
  recipient VARCHAR(255) NULL,
  recipient_type VARCHAR(32) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  provider_message_id VARCHAR(64) NULL,
  error_message TEXT NULL,
  template_id BIGINT UNSIGNED NULL,
  sent_at TIMESTAMP NULL DEFAULT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_logs_campaign (campaign_id),
  KEY idx_logs_domain (domain_id),
  KEY idx_logs_status (status),
  KEY idx_logs_created (created_at),
  KEY idx_logs_project (project_id),
  CONSTRAINT fk_logs_campaign
    FOREIGN KEY (campaign_id) REFERENCES message_campaigns (id)
    ON DELETE SET NULL,
  CONSTRAINT fk_logs_rule
    FOREIGN KEY (automation_rule_id) REFERENCES automation_rules (id)
    ON DELETE SET NULL,
  CONSTRAINT fk_logs_domain
    FOREIGN KEY (domain_id) REFERENCES enamad_domains (id)
    ON DELETE SET NULL,
  CONSTRAINT fk_logs_template
    FOREIGN KEY (template_id) REFERENCES message_templates (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_call_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  project_id BIGINT UNSIGNED NOT NULL,
  domain_id BIGINT UNSIGNED NOT NULL,
  created_by BIGINT UNSIGNED NULL,
  phone_used VARCHAR(64) NULL,
  outcome VARCHAR(32) NOT NULL,
  notes TEXT NULL,
  called_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  next_follow_up_at DATE NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_calls_domain (domain_id),
  KEY idx_calls_outcome (outcome),
  KEY idx_calls_follow_up (next_follow_up_at),
  KEY idx_calls_called_at (called_at),
  KEY idx_calls_project (project_id),
  CONSTRAINT fk_calls_domain
    FOREIGN KEY (domain_id) REFERENCES enamad_domains (id)
    ON DELETE CASCADE,
  CONSTRAINT fk_calls_admin
    FOREIGN KEY (created_by) REFERENCES admin_users (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
