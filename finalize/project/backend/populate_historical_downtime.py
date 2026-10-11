"""
Populate realistic historical downtime logs across multiple months in smart_maintenance_tracker database.
Includes breakdowns within months, multi-month split breakdowns, and ongoing breakdowns.
"""
import uuid
import pymysql
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from db import get_db

def populate():
    conn = get_db()
    c = conn.cursor()

    # Fix any invalid negative minutes in existing logs
    c.execute("""
        UPDATE downtime_logs 
        SET minutes = TIMESTAMPDIFF(MINUTE, start_time, end_time),
            estimated_loss = ABS(estimated_loss)
        WHERE end_time IS NOT NULL AND (minutes < 0 OR estimated_loss < 0)
    """)

    # Get machines
    c.execute("SELECT id, code, name, cost_per_hour FROM equipment")
    equip_list = c.fetchall()
    eq_map = {eq['code']: eq for eq in equip_list}

    # Check existing historical logs (prior to October 2026)
    c.execute("SELECT COUNT(*) AS cnt FROM downtime_logs WHERE start_time < '2026-10-01 00:00:00'")
    prior_count = c.fetchone()['cnt']
    print(f"Existing prior logs count: {prior_count}")

    # Get a valid user
    c.execute("SELECT id FROM users LIMIT 1")
    user_row = c.fetchone()
    user_id = user_row['id'] if user_row else None

    # Get max request code
    c.execute("SELECT COALESCE(MAX(CAST(SUBSTRING(request_code, 5) AS UNSIGNED)), 0) AS max_n FROM maintenance_requests WHERE request_code REGEXP '^REQ-[0-9]+$'")
    code_counter = int(c.fetchone()['max_n'] or 0) + 100

    if prior_count < 10:
        print("Inserting historical downtime logs across past 6 months...")
        
        # Historical breakdown events
        # Format: (equipment_code, start_time, end_time, planned, description)
        historical_events = [
            # May 2026
            ('CNC-01', datetime(2026, 5, 4, 9, 30), datetime(2026, 5, 4, 15, 0), False, "Spindle drive overload failure"), # 5.5h
            ('HYD-01', datetime(2026, 5, 12, 14, 0), datetime(2026, 5, 12, 18, 30), False, "Hydraulic pressure regulator valve leak"), # 4.5h
            ('COMP-01', datetime(2026, 5, 20, 8, 0), datetime(2026, 5, 20, 11, 0), True, "Quarterly air filter & oil replacement"), # 3.0h (planned)

            # June 2026
            ('ELC-01', datetime(2026, 6, 8, 11, 15), datetime(2026, 6, 8, 19, 45), False, "Main breaker tripped due to line spike"), # 8.5h
            ('CNC-01', datetime(2026, 6, 17, 13, 0), datetime(2026, 6, 17, 18, 0), False, "Axis motor thermal cutout"), # 5.0h
            ('WELD-01', datetime(2026, 6, 25, 7, 30), datetime(2026, 6, 25, 13, 30), False, "Robotic arm servo calibration fault"), # 6.0h
            ('CONV-01', datetime(2026, 6, 28, 10, 0), datetime(2026, 6, 28, 14, 0), True, "Conveyor belt tension alignment"), # 4.0h (planned)

            # July 2026
            ('HYD-01', datetime(2026, 7, 5, 8, 0), datetime(2026, 7, 5, 16, 0), False, "Hydraulic manifold seal rupture"), # 8.0h
            ('GRND-01', datetime(2026, 7, 14, 10, 30), datetime(2026, 7, 14, 17, 30), False, "Grinding spindle vibration limit exceeded"), # 7.0h
            ('PUMP-01', datetime(2026, 7, 22, 12, 0), datetime(2026, 7, 22, 16, 0), False, "Coolant pump impeller blockage"), # 4.0h
            # CROSS-MONTH BREAKDOWN: July 30 to August 2 (spans across July and August!)
            # Total 60 hours: 34 hours in July (Jul 30 14:00 to Aug 1 00:00), 26 hours in August (Aug 1 00:00 to Aug 2 02:00)
            ('CNC-01', datetime(2026, 7, 30, 14, 0), datetime(2026, 8, 2, 2, 0), False, "CNC controller motherboard failure and replacement"),

            # August 2026
            ('WELD-01', datetime(2026, 8, 10, 9, 0), datetime(2026, 8, 10, 18, 0), False, "Torch wire feed assembly jam"), # 9.0h
            ('ELC-01', datetime(2026, 8, 18, 8, 30), datetime(2026, 8, 18, 15, 30), True, "Scheduled electrical safety inspection"), # 7.0h (planned)
            ('HYD-01', datetime(2026, 8, 24, 11, 0), datetime(2026, 8, 24, 21, 0), False, "High pressure hose burst and oil cleanup"), # 10.0h
            # CROSS-MONTH BREAKDOWN: August 31 16:00 to September 1 10:00 (spans August and September!)
            # Total 18 hours: 8 hours in August, 10 hours in September
            ('COMP-01', datetime(2026, 8, 31, 16, 0), datetime(2026, 9, 1, 10, 0), False, "Compressor motor overheating emergency shutdown"),

            # September 2026
            ('CNC-01', datetime(2026, 9, 7, 8, 0), datetime(2026, 9, 7, 17, 30), False, "Pneumatic tool changer clamping failure"), # 9.5h
            ('CONV-01', datetime(2026, 9, 15, 9, 30), datetime(2026, 9, 15, 16, 30), False, "Roller bearing seizure on main drive section"), # 7.0h
            ('GRND-01', datetime(2026, 9, 23, 13, 0), datetime(2026, 9, 23, 19, 0), True, "Periodic wheel dressing & lubrication"), # 6.0h (planned)

            # CROSS-MONTH BREAKDOWN: September 29 20:00 to October 1 08:00 (spans Sept and Oct!)
            # Total 36 hours: 28 hours in September, 8 hours in October
            ('WELD-01', datetime(2026, 9, 29, 20, 0), datetime(2026, 10, 1, 8, 0), False, "Robotic controller comms bus failure"),

            # October 2026 (completed events)
            ('CNC-01', datetime(2026, 10, 3, 10, 0), datetime(2026, 10, 3, 16, 30), False, "Hydraulic drawbar pressure sensor failure"), # 6.5h
            ('HYD-01', datetime(2026, 10, 6, 8, 0), datetime(2026, 10, 6, 14, 0), True, "Pre-shift planned safety check"), # 6.0h (planned)
        ]

        # Insert events
        for eq_code, s_time, e_time, planned, desc in historical_events:
            eq = eq_map.get(eq_code)
            if not eq:
                continue
            
            diff_min = int((e_time - s_time).total_seconds() / 60)
            cost_hr = float(eq['cost_per_hour'])
            loss = round((diff_min / 60.0) * cost_hr, 2)
            dl_id = str(uuid.uuid4())

            # Create matching maintenance request
            req_id = str(uuid.uuid4())
            req_code = f"REQ-{code_counter:04d}"
            code_counter += 1
            c.execute("""
                INSERT INTO maintenance_requests
                (id, request_code, type, source, equipment_id, problem_type, urgency,
                 production_stopped, safety_risk, description, reported_by, reported_at, status, priority_score, priority_level)
                VALUES (%s, %s, 'breakdown', 'system', %s, 'mechanical', 3, TRUE, FALSE, %s, %s, %s, 'completed', 75, 'High')
            """, (req_id, req_code, eq['id'], desc, user_id, s_time.strftime('%Y-%m-%d %H:%M:%S')))

            c.execute("""
                INSERT INTO downtime_logs
                (id, equipment_id, request_id, start_time, end_time, minutes, planned, estimated_loss, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'closed', %s)
            """, (
                dl_id, eq['id'], req_id,
                s_time.strftime('%Y-%m-%d %H:%M:%S'),
                e_time.strftime('%Y-%m-%d %H:%M:%S'),
                diff_min, planned, loss,
                s_time.strftime('%Y-%m-%d %H:%M:%S')
            ))

        conn.commit()
        print("Historical downtime logs created successfully!")
    else:
        print("Historical logs already exist.")

    conn.commit()
    conn.close()

if __name__ == '__main__':
    populate()
