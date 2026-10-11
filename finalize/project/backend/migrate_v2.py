"""
Migration script v2: Smart Downtime-Aware Priority & Assignment additions.
Safely adds new columns and tables to smart_maintenance_tracker database.
"""
import pymysql
import os
from dotenv import load_dotenv

load_dotenv()

def run_migration():
    host = os.getenv('DB_HOST', 'localhost')
    port = int(os.getenv('DB_PORT', 3306))
    user = os.getenv('DB_USER', 'root')
    password = os.getenv('DB_PASSWORD', '')
    database = os.getenv('DB_NAME', 'smart_maintenance_tracker')

    conn = pymysql.connect(host=host, port=port, user=user, password=password, database=database)
    cursor = conn.cursor()

    def column_exists(table, column):
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
        return cursor.fetchone() is not None

    print("Running migration v2...")

    # 1. Update maintenance_requests status and priority_level enum / columns
    try:
        cursor.execute("""
            ALTER TABLE maintenance_requests
            MODIFY COLUMN status ENUM('open', 'queued', 'assigned', 'in_progress', 'on_hold', 'pending_verification', 'rework_required', 'completed', 'closed', 'cancelled') NOT NULL DEFAULT 'open',
            MODIFY COLUMN priority_level VARCHAR(20) NOT NULL DEFAULT 'Low'
        """)
        print("Updated maintenance_requests status & priority_level column types.")
    except Exception as e:
        print(f"Notice on modifying status/priority_level: {e}")

    # Add new fields to maintenance_requests
    req_columns = [
        ("actual_breakdown_time", "DATETIME NULL AFTER reported_at"),
        ("safety_risk_rating", "TINYINT NOT NULL DEFAULT 1 AFTER urgency"),
        ("production_impact_rating", "TINYINT NOT NULL DEFAULT 1 AFTER safety_risk_rating"),
        ("workaround_rating", "TINYINT NOT NULL DEFAULT 1 AFTER production_impact_rating"),
        ("safety_hazard_flag", "BOOLEAN NOT NULL DEFAULT FALSE AFTER workaround_rating"),
        ("safety_review_notes", "TEXT NULL AFTER safety_hazard_flag"),
        ("recommended_technician_id", "CHAR(36) NULL AFTER assigned_technician_id"),
        ("recommendation_reason", "TEXT NULL AFTER recommended_technician_id"),
    ]

    for col, col_def in req_columns:
        if not column_exists("maintenance_requests", col):
            cursor.execute(f"ALTER TABLE maintenance_requests ADD COLUMN `{col}` {col_def}")
            print(f"Added column {col} to maintenance_requests.")
        else:
            print(f"Column {col} already exists in maintenance_requests.")

    # 2. Add columns to downtime_logs
    dt_columns = [
        ("technician_assignment_time", "DATETIME NULL AFTER start_time"),
        ("repair_start_time", "DATETIME NULL AFTER technician_assignment_time"),
        ("repair_completion_time", "DATETIME NULL AFTER repair_start_time"),
        ("verification_completed_at", "DATETIME NULL AFTER repair_completion_time"),
        ("returned_to_service_at", "DATETIME NULL AFTER verification_completed_at"),
        ("authorized_by", "CHAR(36) NULL AFTER returned_to_service_at"),
        ("status", "ENUM('open', 'closed') NOT NULL DEFAULT 'open' AFTER authorized_by"),
    ]

    for col, col_def in dt_columns:
        if not column_exists("downtime_logs", col):
            cursor.execute(f"ALTER TABLE downtime_logs ADD COLUMN `{col}` {col_def}")
            print(f"Added column {col} to downtime_logs.")
        else:
            print(f"Column {col} already exists in downtime_logs.")

    # 3. Add columns to priority_rules
    pr_columns = [
        ("weight_safety", "DECIMAL(5,2) NOT NULL DEFAULT 30.0"),
        ("weight_production", "DECIMAL(5,2) NOT NULL DEFAULT 25.0"),
        ("weight_criticality", "DECIMAL(5,2) NOT NULL DEFAULT 20.0"),
        ("weight_downtime", "DECIMAL(5,2) NOT NULL DEFAULT 15.0"),
        ("weight_workaround", "DECIMAL(5,2) NOT NULL DEFAULT 10.0"),
        ("threshold_critical", "INT NOT NULL DEFAULT 80"),
        ("threshold_high", "INT NOT NULL DEFAULT 60"),
        ("threshold_medium", "INT NOT NULL DEFAULT 35"),
        ("threshold_low", "INT NOT NULL DEFAULT 20"),
    ]

    for col, col_def in pr_columns:
        if not column_exists("priority_rules", col):
            cursor.execute(f"ALTER TABLE priority_rules ADD COLUMN `{col}` {col_def}")
            print(f"Added column {col} to priority_rules.")
        else:
            print(f"Column {col} already exists in priority_rules.")

    # 4. Create verification_history table
    cursor.execute("""
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
        )
    """)
    print("verification_history table verified / created.")

    conn.commit()
    conn.close()
    print("Migration v2 completed successfully!")

if __name__ == '__main__':
    run_migration()
