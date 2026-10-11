-- ============================================================
-- SMART MAINTENANCE TRACKER - MySQL / MariaDB Schema
-- Import this file into phpMyAdmin
-- Database: smart_maintenance_tracker
-- ============================================================

CREATE DATABASE IF NOT EXISTS smart_maintenance_tracker CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE smart_maintenance_tracker;

SET FOREIGN_KEY_CHECKS = 0;

-- ============================================================
-- 1. USERS TABLE (Authentication)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
  id CHAR(36) PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  full_name VARCHAR(255) NOT NULL,
  role ENUM('operator', 'technician', 'supervisor', 'administrator') NOT NULL DEFAULT 'operator',
  phone VARCHAR(20),
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_users_email (email),
  INDEX idx_users_role (role)
);

-- ============================================================
-- 2. SKILLS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS skills (
  id CHAR(36) PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 3. TECHNICIANS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS technicians (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36),
  name VARCHAR(255) NOT NULL,
  email VARCHAR(255) UNIQUE,
  shift_start TIME,
  shift_end TIME,
  working_days JSON,
  availability ENUM('available', 'busy', 'on_leave') NOT NULL DEFAULT 'available',
  zone VARCHAR(100),
  phone VARCHAR(20),
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_technicians_availability (availability),
  INDEX idx_technicians_active (active)
);

-- ============================================================
-- 4. TECHNICIAN_SKILLS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS technician_skills (
  id CHAR(36) PRIMARY KEY,
  technician_id CHAR(36) NOT NULL,
  skill_id CHAR(36) NOT NULL,
  level TINYINT NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 3),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_tech_skill (technician_id, skill_id),
  FOREIGN KEY (technician_id) REFERENCES technicians(id) ON DELETE CASCADE,
  FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
);

-- ============================================================
-- 5. EQUIPMENT TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS equipment (
  id CHAR(36) PRIMARY KEY,
  code VARCHAR(50) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  zone VARCHAR(100) NOT NULL,
  criticality TINYINT NOT NULL CHECK (criticality BETWEEN 1 AND 5),
  cost_per_hour DECIMAL(10,2) NOT NULL DEFAULT 0,
  status ENUM('running', 'down', 'under_maintenance') NOT NULL DEFAULT 'running',
  manufacturer VARCHAR(255),
  install_date DATE,
  notes TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_equipment_status (status),
  INDEX idx_equipment_code (code),
  INDEX idx_equipment_active (active)
);

-- ============================================================
-- 6. PRIORITY_RULES TABLE (NFR-15: stored as data)
-- ============================================================
CREATE TABLE IF NOT EXISTS priority_rules (
  id CHAR(36) PRIMARY KEY,
  weight_safety DECIMAL(5,2) NOT NULL DEFAULT 30.0,
  weight_production DECIMAL(5,2) NOT NULL DEFAULT 25.0,
  weight_criticality DECIMAL(5,2) NOT NULL DEFAULT 20.0,
  weight_downtime DECIMAL(5,2) NOT NULL DEFAULT 15.0,
  weight_workaround DECIMAL(5,2) NOT NULL DEFAULT 10.0,
  threshold_critical INT NOT NULL DEFAULT 80,
  threshold_high INT NOT NULL DEFAULT 60,
  threshold_medium INT NOT NULL DEFAULT 35,
  threshold_low INT NOT NULL DEFAULT 20,
  weight_criticality_urgency_product INT NOT NULL DEFAULT 1,
  weight_production_stopped INT NOT NULL DEFAULT 10,
  weight_safety_risk INT NOT NULL DEFAULT 10,
  weight_waiting_hour INT NOT NULL DEFAULT 1,
  waiting_cap INT NOT NULL DEFAULT 10,
  weight_days_overdue INT NOT NULL DEFAULT 2,
  p1_threshold INT NOT NULL DEFAULT 30,
  p2_threshold INT NOT NULL DEFAULT 20,
  p3_threshold INT NOT NULL DEFAULT 10,
  p1_response_minutes INT NOT NULL DEFAULT 15,
  p1_resolution_minutes INT NOT NULL DEFAULT 240,
  p2_response_minutes INT NOT NULL DEFAULT 60,
  p2_resolution_minutes INT NOT NULL DEFAULT 480,
  p3_response_minutes INT NOT NULL DEFAULT 480,
  p3_resolution_minutes INT NOT NULL DEFAULT 1440,
  p4_response_minutes INT NOT NULL DEFAULT 2880,
  p4_resolution_minutes INT NOT NULL DEFAULT 4320,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ============================================================
-- 7. MAINTENANCE_SCHEDULES TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS maintenance_schedules (
  id CHAR(36) PRIMARY KEY,
  equipment_id CHAR(36) NOT NULL,
  task_name VARCHAR(255) NOT NULL,
  skill_needed CHAR(36),
  interval_type ENUM('days', 'run_hours') NOT NULL,
  interval_value INT NOT NULL CHECK (interval_value > 0),
  lead_days INT NOT NULL DEFAULT 3,
  last_service_date DATE,
  next_due_date DATE,
  estimated_duration_minutes INT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  defer_count INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
  FOREIGN KEY (skill_needed) REFERENCES skills(id) ON DELETE SET NULL,
  INDEX idx_schedules_equipment (equipment_id),
  INDEX idx_schedules_next_due (next_due_date),
  INDEX idx_schedules_active (active)
);

-- ============================================================
-- 8. MAINTENANCE_REQUESTS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS maintenance_requests (
  id CHAR(36) PRIMARY KEY,
  request_code VARCHAR(20) NOT NULL UNIQUE,
  type ENUM('breakdown', 'preventive') NOT NULL,
  source ENUM('operator', 'supervisor', 'system') NOT NULL,
  equipment_id CHAR(36) NOT NULL,
  schedule_id CHAR(36),
  problem_type ENUM('electrical', 'mechanical', 'hydraulic', 'pneumatic', 'control', 'other'),
  urgency TINYINT CHECK (urgency BETWEEN 1 AND 5),
  safety_risk_rating TINYINT NOT NULL DEFAULT 1 CHECK (safety_risk_rating BETWEEN 1 AND 5),
  production_impact_rating TINYINT NOT NULL DEFAULT 1 CHECK (production_impact_rating BETWEEN 1 AND 5),
  workaround_rating TINYINT NOT NULL DEFAULT 1 CHECK (workaround_rating BETWEEN 1 AND 5),
  safety_hazard_flag BOOLEAN NOT NULL DEFAULT FALSE,
  safety_review_notes TEXT,
  production_stopped BOOLEAN NOT NULL DEFAULT FALSE,
  safety_risk BOOLEAN NOT NULL DEFAULT FALSE,
  description TEXT,
  photo_url VARCHAR(500),
  completion_photo_url LONGTEXT,
  reported_by CHAR(36),
  reported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actual_breakdown_time DATETIME,
  status ENUM('open', 'queued', 'assigned', 'in_progress', 'on_hold', 'pending_verification', 'rework_required', 'completed', 'closed', 'cancelled') NOT NULL DEFAULT 'open',
  priority_score INT NOT NULL DEFAULT 0,
  priority_level VARCHAR(20) NOT NULL DEFAULT 'Low',
  score_breakdown JSON,
  assigned_technician_id CHAR(36),
  recommended_technician_id CHAR(36),
  recommendation_reason TEXT,
  work_done TEXT,
  root_cause TEXT,
  parts_used TEXT,
  time_spent_minutes INT,
  follow_up_needed BOOLEAN DEFAULT FALSE,
  hold_reason TEXT,
  cancel_reason TEXT,
  delay_reason TEXT,
  `delayed` BOOLEAN NOT NULL DEFAULT FALSE,
  escalated_to VARCHAR(50),
  completed_at DATETIME,
  closed_at DATETIME,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
  FOREIGN KEY (schedule_id) REFERENCES maintenance_schedules(id) ON DELETE SET NULL,
  FOREIGN KEY (reported_by) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (assigned_technician_id) REFERENCES technicians(id) ON DELETE SET NULL,
  INDEX idx_requests_status (status),
  INDEX idx_requests_priority (priority_score DESC),
  INDEX idx_requests_equipment (equipment_id),
  INDEX idx_requests_technician (assigned_technician_id),
  INDEX idx_requests_type (type),
  INDEX idx_requests_reported_at (reported_at)
);

-- ============================================================
-- 9. ASSIGNMENTS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS assignments (
  id CHAR(36) PRIMARY KEY,
  request_id CHAR(36) NOT NULL,
  technician_id CHAR(36) NOT NULL,
  assigned_by CHAR(36),
  assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  unassigned_at DATETIME,
  reason TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (technician_id) REFERENCES technicians(id) ON DELETE CASCADE,
  FOREIGN KEY (assigned_by) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_assignments_request (request_id),
  INDEX idx_assignments_technician (technician_id),
  INDEX idx_assignments_active (active)
);

-- ============================================================
-- 10. DOWNTIME_LOGS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS downtime_logs (
  id CHAR(36) PRIMARY KEY,
  equipment_id CHAR(36) NOT NULL,
  request_id CHAR(36),
  start_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  technician_assignment_time DATETIME,
  repair_start_time DATETIME,
  repair_completion_time DATETIME,
  verification_completed_at DATETIME,
  returned_to_service_at DATETIME,
  authorized_by CHAR(36),
  status ENUM('open', 'closed') NOT NULL DEFAULT 'open',
  end_time DATETIME,
  minutes INT,
  planned BOOLEAN NOT NULL DEFAULT FALSE,
  estimated_loss DECIMAL(12,2) DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE SET NULL,
  INDEX idx_downtime_equipment (equipment_id),
  INDEX idx_downtime_start (start_time),
  INDEX idx_downtime_request (request_id)
);

-- ============================================================
-- 10B. VERIFICATION_HISTORY TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS verification_history (
  id CHAR(36) PRIMARY KEY,
  request_id CHAR(36) NOT NULL,
  reviewer_id CHAR(36),
  reviewer_name VARCHAR(255),
  decision ENUM('passed', 'rework_required') NOT NULL,
  comments TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (reviewer_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_vh_request (request_id),
  INDEX idx_vh_created (created_at)
);

-- ============================================================
-- 11. STATUS_HISTORY TABLE (audit trail - NFR-10)
-- ============================================================
CREATE TABLE IF NOT EXISTS status_history (
  id CHAR(36) PRIMARY KEY,
  request_id CHAR(36) NOT NULL,
  from_status VARCHAR(20),
  to_status VARCHAR(20) NOT NULL,
  changed_by CHAR(36),
  changed_by_name VARCHAR(255),
  reason TEXT,
  changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_status_history_request (request_id),
  INDEX idx_status_history_time (changed_at)
);

-- ============================================================
-- 12. NOTIFICATIONS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS notifications (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  type ENUM('assignment', 'escalation', 'delay', 'completion', 'queue', 'preemption', 'info') NOT NULL,
  title VARCHAR(255) NOT NULL,
  message TEXT NOT NULL,
  request_id CHAR(36),
  `read` BOOLEAN NOT NULL DEFAULT FALSE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE CASCADE,
  INDEX idx_notifications_user_unread (user_id, `read`),
  INDEX idx_notifications_created (created_at)
);

-- ============================================================
-- 13. REQUEST_COMMENTS TABLE (FR-15)
-- ============================================================
CREATE TABLE IF NOT EXISTS request_comments (
  id CHAR(36) PRIMARY KEY,
  request_id CHAR(36) NOT NULL,
  user_id CHAR(36),
  author_name VARCHAR(255),
  comment TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_comments_request (request_id)
);

SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================
-- SEED DATA
-- ============================================================

-- Skills
INSERT IGNORE INTO skills (id, name) VALUES
  (UUID(), 'Electrical'),
  (UUID(), 'Mechanical'),
  (UUID(), 'Hydraulic'),
  (UUID(), 'Pneumatic'),
  (UUID(), 'Control');

-- Priority Rules (defaults from SRS section 4.1)
INSERT IGNORE INTO priority_rules (
  id, weight_criticality_urgency_product, weight_production_stopped, weight_safety_risk,
  weight_waiting_hour, waiting_cap, weight_days_overdue,
  p1_threshold, p2_threshold, p3_threshold,
  p1_response_minutes, p1_resolution_minutes,
  p2_response_minutes, p2_resolution_minutes,
  p3_response_minutes, p3_resolution_minutes,
  p4_response_minutes, p4_resolution_minutes, active
) VALUES (
  UUID(), 1, 10, 10, 1, 10, 2,
  30, 20, 10,
  15, 240,
  60, 480,
  480, 1440,
  2880, 4320,
  TRUE
);

-- Add completion_photo_url if upgrading from older schema
ALTER TABLE maintenance_requests ADD COLUMN IF NOT EXISTS completion_photo_url TEXT DEFAULT NULL;
