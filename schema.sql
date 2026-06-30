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
  province VARCHAR(128) NULL,
  city VARCHAR(128) NULL,
  rating TINYINT UNSIGNED NOT NULL DEFAULT 0,
  approve_date VARCHAR(32) NULL,
  expire_date VARCHAR(32) NULL,
  trustseal_url VARCHAR(512) NULL,
  source_page INT UNSIGNED NULL,
  source_row INT UNSIGNED NULL,
  scrape_run_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_enamad_record (enamad_id, code),
  KEY idx_domain (domain),
  KEY idx_business_name (business_name(191)),
  KEY idx_province_city (province, city),
  KEY idx_scrape_run_id (scrape_run_id),
  CONSTRAINT fk_enamad_domains_scrape_run
    FOREIGN KEY (scrape_run_id) REFERENCES scrape_runs (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
