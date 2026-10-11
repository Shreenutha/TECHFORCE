"""
Create demo data for Smart Maintenance Tracker
Run this AFTER importing schema.sql into phpMyAdmin.

Usage: python create_demo_data.py
"""
import uuid
import json
import bcrypt
from datetime import datetime, timezone, timedelta, date
from db import get_db


def hash_password(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def run():
    conn = get_db()
    try:
        with conn.cursor() as c:
            print("Creating demo users...")

            # Helper to get or create user
            def get_or_create_user(email, password, full_name, role):
                c.execute("SELECT id FROM users WHERE email = %s", (email,))
                r = c.fetchone()
                if r:
                    return r['id']
                uid = str(uuid.uuid4())
                c.execute("""INSERT INTO users (id, email, password_hash, full_name, role)
                             VALUES (%s, %s, %s, %s, %s)""",
                          (uid, email, hash_password(password), full_name, role))
                return uid

            admin_id = get_or_create_user('admin@demo.com', 'Admin@123', 'Admin User', 'administrator')
            sup_id = get_or_create_user('supervisor@demo.com', 'Sup@123', 'Supervisor Singh', 'supervisor')
            op1_id = get_or_create_user('operator@demo.com', 'Op@123', 'Operator Ravi', 'operator')
            op2_id = get_or_create_user('operator2@demo.com', 'Op@123', 'Operator Priya', 'operator')
            tech_u1 = get_or_create_user('tech1@demo.com', 'Tech@123', 'Technician Arun', 'technician')
            tech_u2 = get_or_create_user('tech2@demo.com', 'Tech@123', 'Technician Meena', 'technician')
            tech_u3 = get_or_create_user('tech3@demo.com', 'Tech@123', 'Technician Kumar', 'technician')

            print("Creating skills (if not exists)...")
            skill_ids = {}
            for skill_name in ['Electrical', 'Mechanical', 'Hydraulic', 'Pneumatic', 'Control']:
                skill_id = str(uuid.uuid4())
                c.execute("INSERT IGNORE INTO skills (id, name) VALUES (%s, %s)", (skill_id, skill_name))
                c.execute("SELECT id FROM skills WHERE name = %s", (skill_name,))
                row = c.fetchone()
                skill_ids[skill_name] = row['id']

            def get_or_create_tech(user_id, name, email, shift_start, shift_end, working_days, zone, phone):
                c.execute("SELECT id FROM technicians WHERE email = %s", (email,))
                r = c.fetchone()
                if r:
                    return r['id']
                tid = str(uuid.uuid4())
                c.execute("""INSERT INTO technicians (id, user_id, name, email, shift_start, shift_end,
                               working_days, availability, zone, phone)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, 'available', %s, %s)""",
                          (tid, user_id, name, email, shift_start, shift_end, json.dumps(working_days), zone, phone))
                return tid

            tech1_id = get_or_create_tech(tech_u1, 'Arun Kumar', 'tech1@demo.com', '08:00', '16:00', ['Mon','Tue','Wed','Thu','Fri','Sat'], 'Line 1', '9900001111')
            tech2_id = get_or_create_tech(tech_u2, 'Meena Raj', 'tech2@demo.com', '08:00', '16:00', ['Mon','Tue','Wed','Thu','Fri','Sat'], 'Line 2', '9900002222')
            tech3_id = get_or_create_tech(tech_u3, 'Kumar Das', 'tech3@demo.com', '16:00', '00:00', ['Mon','Tue','Wed','Thu','Fri','Sat'], 'Line 3', '9900003333')

            print("Adding technician skills...")
            # Arun: Mechanical (3), Electrical (2)
            for sid, lv in [(skill_ids['Mechanical'], 3), (skill_ids['Electrical'], 2)]:
                c.execute("INSERT IGNORE INTO technician_skills (id, technician_id, skill_id, level) VALUES (%s, %s, %s, %s)",
                          (str(uuid.uuid4()), tech1_id, sid, lv))

            # Meena: Electrical (3), Control (2)
            for sid, lv in [(skill_ids['Electrical'], 3), (skill_ids['Control'], 2)]:
                c.execute("INSERT IGNORE INTO technician_skills (id, technician_id, skill_id, level) VALUES (%s, %s, %s, %s)",
                          (str(uuid.uuid4()), tech2_id, sid, lv))

            # Kumar: Hydraulic (3), Pneumatic (3), Mechanical (1)
            for sid, lv in [(skill_ids['Hydraulic'], 3), (skill_ids['Pneumatic'], 3), (skill_ids['Mechanical'], 1)]:
                c.execute("INSERT IGNORE INTO technician_skills (id, technician_id, skill_id, level) VALUES (%s, %s, %s, %s)",
                          (str(uuid.uuid4()), tech3_id, sid, lv))

            print("Creating equipment...")
            equipment_data = [
                ('CNC-01', 'CNC Press Machine', 'Line 1', 5, 5000, 'running', 'MAZAK', '2021-01-15'),
                ('HYD-01', 'Hydraulic Press', 'Line 2', 4, 3500, 'running', 'Parker', '2020-06-01'),
                ('ELC-01', 'Main Control Panel', 'Electrical', 5, 8000, 'running', 'Siemens', '2019-03-10'),
                ('COMP-01', 'Air Compressor', 'Utilities', 3, 2000, 'running', 'Atlas Copco', '2022-01-20'),
                ('CONV-01', 'Assembly Conveyor', 'Line 3', 4, 4000, 'running', 'Bosch', '2021-07-15'),
                ('PUMP-01', 'Coolant Pump', 'Line 1', 2, 1500, 'running', 'Grundfos', '2023-01-10'),
                ('WELD-01', 'Welding Robot', 'Line 2', 5, 6000, 'running', 'KUKA', '2020-09-01'),
                ('GRND-01', 'Surface Grinder', 'Line 3', 3, 2500, 'running', 'Okuma', '2022-05-20'),
            ]
            equip_ids = {}
            for eq in equipment_data:
                eq_id = str(uuid.uuid4())
                c.execute("""INSERT IGNORE INTO equipment (id, code, name, zone, criticality, cost_per_hour,
                               status, manufacturer, install_date)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                          (eq_id, eq[0], eq[1], eq[2], eq[3], eq[4], eq[5], eq[6], eq[7]))
                c.execute("SELECT id FROM equipment WHERE code = %s", (eq[0],))
                row = c.fetchone()
                equip_ids[eq[0]] = row['id']

            print("Creating preventive schedules...")
            schedules = [
                (equip_ids['CNC-01'], 'Lubricate spindle bearings', 'Mechanical', 30, 3,
                 (date.today() - timedelta(days=25)).isoformat(),
                 (date.today() + timedelta(days=5)).isoformat(), 60),
                (equip_ids['HYD-01'], 'Check hydraulic fluid level', 'Hydraulic', 15, 2,
                 (date.today() - timedelta(days=13)).isoformat(),
                 (date.today() + timedelta(days=2)).isoformat(), 30),
                (equip_ids['ELC-01'], 'Inspect control panel wiring', 'Electrical', 90, 7,
                 (date.today() - timedelta(days=85)).isoformat(),
                 (date.today() + timedelta(days=5)).isoformat(), 120),
                (equip_ids['COMP-01'], 'Change air filter', None, 30, 3,
                 (date.today() - timedelta(days=30)).isoformat(),
                 date.today().isoformat(), 45),
                (equip_ids['WELD-01'], 'Clean and calibrate welding torch', 'Mechanical', 45, 5,
                 (date.today() - timedelta(days=40)).isoformat(),
                 (date.today() - timedelta(days=2)).isoformat(), 90),
            ]
            for s in schedules:
                sched_id = str(uuid.uuid4())
                skill_id = skill_ids.get(s[2]) if s[2] else None
                c.execute("""INSERT INTO maintenance_schedules
                               (id, equipment_id, task_name, skill_needed, interval_type, interval_value,
                                lead_days, last_service_date, next_due_date, estimated_duration_minutes, active)
                             VALUES (%s, %s, %s, %s, 'days', %s, %s, %s, %s, %s, TRUE)""",
                          (sched_id, s[0], s[1], skill_id, s[3], s[4], s[5], s[6], s[7]))

            print("Creating demo requests...")
            # Helper: generate code
            def gen_code(cursor):
                cursor.execute(
                    "SELECT COALESCE(MAX(CAST(SUBSTRING(request_code, 5) AS UNSIGNED)), 0) + 1 AS n "
                    "FROM maintenance_requests WHERE request_code REGEXP '^REQ-[0-9]+$'"
                )
                row = cursor.fetchone()
                return f"REQ-{str(row['n']).zfill(4)}"

            # Request 1: P1 breakdown on CNC-01 (completed)
            r1_id = str(uuid.uuid4())
            r1_code = gen_code(c)
            r1_reported = datetime.now(timezone.utc) - timedelta(hours=5)
            c.execute("""INSERT INTO maintenance_requests
                           (id, request_code, type, source, equipment_id, problem_type, urgency,
                            production_stopped, safety_risk, description, reported_by, reported_at, status,
                            priority_score, priority_level, score_breakdown, assigned_technician_id,
                            work_done, root_cause, time_spent_minutes, completed_at)
                         VALUES (%s, %s, 'breakdown', 'operator', %s, 'mechanical', 4, TRUE, FALSE,
                                 'CNC press vibration excessive, machine halted', %s, %s, 'completed',
                                 30, 'P1', %s, %s,
                                 'Replaced worn spindle bearing and re-lubricated', 'Bearing wear due to overload',
                                 120, NOW())""",
                      (r1_id, r1_code, equip_ids['CNC-01'], op1_id, r1_reported.strftime('%Y-%m-%d %H:%M:%S'),
                       json.dumps({'criticality_urgency': 20, 'production_stopped': 10, 'safety_risk': 0,
                                   'waiting_time': 0, 'days_overdue': 0}),
                       tech1_id))

            # Downtime log for r1 (closed)
            c.execute("""INSERT INTO downtime_logs (id, equipment_id, request_id, start_time, end_time, minutes, planned, estimated_loss)
                         VALUES (%s, %s, %s, %s, NOW(), 120, FALSE, %s)""",
                      (str(uuid.uuid4()), equip_ids['CNC-01'], r1_id,
                       r1_reported.strftime('%Y-%m-%d %H:%M:%S'), 5000 * 2))

            # Request 2: P2 breakdown on HYD-01 (in_progress)
            r2_id = str(uuid.uuid4())
            r2_code = gen_code(c)
            r2_reported = datetime.now(timezone.utc) - timedelta(hours=2)
            c.execute("""INSERT INTO maintenance_requests
                           (id, request_code, type, source, equipment_id, problem_type, urgency,
                            production_stopped, safety_risk, description, reported_by, reported_at, status,
                            priority_score, priority_level, score_breakdown, assigned_technician_id)
                         VALUES (%s, %s, 'breakdown', 'operator', %s, 'hydraulic', 3, FALSE, FALSE,
                                 'Hydraulic leak detected near cylinder seal', %s, %s, 'in_progress',
                                 12, 'P2', %s, %s)""",
                      (r2_id, r2_code, equip_ids['HYD-01'], op1_id, r2_reported.strftime('%Y-%m-%d %H:%M:%S'),
                       json.dumps({'criticality_urgency': 12, 'production_stopped': 0, 'safety_risk': 0,
                                   'waiting_time': 2, 'days_overdue': 0}),
                       tech3_id))
            c.execute("UPDATE technicians SET availability = 'busy', updated_at = NOW() WHERE id = %s", (tech3_id,))
            c.execute("INSERT INTO downtime_logs (id, equipment_id, request_id, planned) VALUES (%s, %s, %s, FALSE)",
                      (str(uuid.uuid4()), equip_ids['HYD-01'], r2_id))
            c.execute("UPDATE equipment SET status = 'down', updated_at = NOW() WHERE id = %s", (equip_ids['HYD-01'],))

            # Request 3: P1 queued on ELC-01
            r3_id = str(uuid.uuid4())
            r3_code = gen_code(c)
            r3_reported = datetime.now(timezone.utc) - timedelta(minutes=30)
            c.execute("""INSERT INTO maintenance_requests
                           (id, request_code, type, source, equipment_id, problem_type, urgency,
                            production_stopped, safety_risk, description, reported_by, reported_at, status,
                            priority_score, priority_level, score_breakdown)
                         VALUES (%s, %s, 'breakdown', 'supervisor', %s, 'electrical', 4, TRUE, TRUE,
                                 'Control panel sparking, immediate safety hazard', %s, %s, 'queued',
                                 40, 'P1', %s)""",
                      (r3_id, r3_code, equip_ids['ELC-01'], sup_id, r3_reported.strftime('%Y-%m-%d %H:%M:%S'),
                       json.dumps({'criticality_urgency': 20, 'production_stopped': 10, 'safety_risk': 10,
                                   'waiting_time': 0, 'days_overdue': 0})))
            c.execute("INSERT INTO downtime_logs (id, equipment_id, request_id, planned) VALUES (%s, %s, %s, FALSE)",
                      (str(uuid.uuid4()), equip_ids['ELC-01'], r3_id))
            c.execute("UPDATE equipment SET status = 'down', updated_at = NOW() WHERE id = %s", (equip_ids['ELC-01'],))

            # Request 4: P3 open on CONV-01
            r4_id = str(uuid.uuid4())
            r4_code = gen_code(c)
            r4_reported = datetime.now(timezone.utc) - timedelta(hours=10)
            c.execute("""INSERT INTO maintenance_requests
                           (id, request_code, type, source, equipment_id, problem_type, urgency,
                            production_stopped, safety_risk, description, reported_by, reported_at, status,
                            priority_score, priority_level, score_breakdown)
                         VALUES (%s, %s, 'breakdown', 'operator', %s, 'mechanical', 2, FALSE, FALSE,
                                 'Conveyor belt making grinding noise, reduced speed', %s, %s, 'open',
                                 10, 'P3', %s)""",
                      (r4_id, r4_code, equip_ids['CONV-01'], op2_id, r4_reported.strftime('%Y-%m-%d %H:%M:%S'),
                       json.dumps({'criticality_urgency': 8, 'production_stopped': 0, 'safety_risk': 0,
                                   'waiting_time': 2, 'days_overdue': 0})))

            # Request 5: Assigned P2 on WELD-01
            r5_id = str(uuid.uuid4())
            r5_code = gen_code(c)
            r5_reported = datetime.now(timezone.utc) - timedelta(hours=1)
            c.execute("""INSERT INTO maintenance_requests
                           (id, request_code, type, source, equipment_id, problem_type, urgency,
                            production_stopped, safety_risk, description, reported_by, reported_at, status,
                            priority_score, priority_level, score_breakdown, assigned_technician_id)
                         VALUES (%s, %s, 'breakdown', 'operator', %s, 'control', 3, FALSE, FALSE,
                                 'Welding robot not executing programmed path correctly', %s, %s, 'assigned',
                                 15, 'P2', %s, %s)""",
                      (r5_id, r5_code, equip_ids['WELD-01'], op1_id, r5_reported.strftime('%Y-%m-%d %H:%M:%S'),
                       json.dumps({'criticality_urgency': 15, 'production_stopped': 0, 'safety_risk': 0,
                                   'waiting_time': 0, 'days_overdue': 0}),
                       tech2_id))
            c.execute("UPDATE technicians SET availability = 'busy', updated_at = NOW() WHERE id = %s", (tech2_id,))
            c.execute("INSERT INTO assignments (id, request_id, technician_id, assigned_by, active) VALUES (%s, %s, %s, %s, TRUE)",
                      (str(uuid.uuid4()), r5_id, tech2_id, sup_id))
            c.execute("UPDATE equipment SET status = 'under_maintenance', updated_at = NOW() WHERE id = %s", (equip_ids['WELD-01'],))

            # Status history
            for r_id, statuses in [
                (r1_id, [('open', 'assigned'), ('assigned', 'in_progress'), ('in_progress', 'completed')]),
                (r2_id, [('open', 'assigned'), ('assigned', 'in_progress')]),
                (r3_id, [('open', 'queued')]),
                (r5_id, [('open', 'assigned')]),
            ]:
                for fs, ts in statuses:
                    c.execute("""INSERT INTO status_history (id, request_id, from_status, to_status, changed_by_name)
                                 VALUES (%s, %s, %s, %s, 'System')""",
                              (str(uuid.uuid4()), r_id, fs, ts))

            # Notification for queued P1
            c.execute("""INSERT INTO notifications (id, user_id, type, title, message, request_id)
                         VALUES (%s, %s, 'queue', 'P1 Request Queued', 'ELC-01 control panel sparking - no technician available with Electrical skill on shift', %s)""",
                      (str(uuid.uuid4()), sup_id, r3_id))

            conn.commit()
            print("\nDemo data created successfully!")
            print("\nLogin credentials:")
            print("  Administrator: admin@demo.com / Admin@123")
            print("  Supervisor:    supervisor@demo.com / Sup@123")
            print("  Operator:      operator@demo.com / Op@123")
            print("  Technician:    tech1@demo.com / Tech@123")
            print("  Technician:    tech2@demo.com / Tech@123")

    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    run()
