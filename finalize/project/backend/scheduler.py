"""
Scheduler - Implements SRS section 4.3:
- Runs hourly to generate preventive requests
- Also handles SLA monitoring and escalation (FR-34)
- Recalculates priority scores every 15 minutes (FR-21)
"""
import uuid
import json
import math
from datetime import datetime, timezone, date, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from db import get_db
from priority_engine import calculate_priority_score, get_waiting_hours, get_days_overdue, get_sla_targets, calculate_downtime_minutes, calculate_smart_priority
from assignment_engine import assign_request

_scheduler = None


def start_scheduler():
    """Start the APScheduler background scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = BackgroundScheduler(timezone='UTC')
    _scheduler.add_job(run_preventive_scheduler, 'interval', hours=1, id='preventive_scheduler',
                       next_run_time=datetime.now(timezone.utc))
    _scheduler.add_job(recalculate_priorities, 'interval', minutes=15, id='priority_recalc',
                       next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30))
    _scheduler.add_job(check_sla_and_escalate, 'interval', minutes=5, id='sla_check',
                       next_run_time=datetime.now(timezone.utc) + timedelta(seconds=60))
    _scheduler.add_job(process_queued_retry, 'interval', minutes=1, id='queue_retry',
                       next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10))
    _scheduler.start()
    print("Scheduler started.")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()


def run_preventive_scheduler():
    """
    SRS 4.3: Create a preventive request for every due schedule.
    A schedule is due when: next_due_date - lead_days <= today
    Never creates a second request while one is open for that schedule.
    """
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            today = date.today().isoformat()
            cursor.execute(
                """SELECT ms.*, e.zone AS equip_zone, e.criticality
                   FROM maintenance_schedules ms
                   JOIN equipment e ON e.id = ms.equipment_id
                   WHERE ms.active = TRUE
                     AND ms.next_due_date IS NOT NULL
                     AND DATE_SUB(ms.next_due_date, INTERVAL ms.lead_days DAY) <= %s""",
                (today,)
            )
            schedules = cursor.fetchall()

            created = 0
            for sched in schedules:
                # Check if open request already exists for this schedule
                cursor.execute(
                    """SELECT id FROM maintenance_requests
                       WHERE schedule_id = %s AND status NOT IN ('completed', 'closed', 'cancelled')
                       LIMIT 1""",
                    (sched['id'],)
                )
                if cursor.fetchone():
                    continue  # Already has an open request

                # Calculate priority
                days_overdue = get_days_overdue(sched['next_due_date'])
                priority = calculate_priority_score(
                    sched['criticality'], 1,  # urgency starts at 1 for preventive
                    False, False, 0, days_overdue
                )

                # Generate request code
                request_id = str(uuid.uuid4())
                cursor.execute(
                    "SELECT COALESCE(MAX(CAST(SUBSTRING(request_code, 5) AS UNSIGNED)), 0) + 1 AS next_num "
                    "FROM maintenance_requests WHERE request_code REGEXP '^REQ-[0-9]+$'"
                )
                row = cursor.fetchone()
                code = f"REQ-{str(row['next_num']).zfill(4)}"

                cursor.execute(
                    """INSERT INTO maintenance_requests (
                         id, request_code, type, source, equipment_id, schedule_id,
                         problem_type, urgency, production_stopped, safety_risk,
                         description, reported_at, status,
                         priority_score, priority_level, score_breakdown
                       ) VALUES (%s, %s, 'preventive', 'system', %s, %s,
                                 NULL, 1, FALSE, FALSE,
                                 %s, NOW(), 'open', %s, %s, %s)""",
                    (
                        request_id, code, sched['equipment_id'], sched['id'],
                        f"Preventive: {sched['task_name']}",
                        priority['score'], priority['level'],
                        json.dumps(priority['breakdown'])
                    )
                )

                # Log status history
                cursor.execute(
                    """INSERT INTO status_history (id, request_id, from_status, to_status, changed_by_name, reason)
                       VALUES (%s, %s, NULL, 'open', 'System', 'Preventive request auto-generated')""",
                    (str(uuid.uuid4()), request_id)
                )

                created += 1

            conn.commit()
            if created > 0:
                print(f"Preventive scheduler: created {created} request(s)")
            return created
    except Exception as e:
        conn.rollback()
        print(f"Preventive scheduler error: {e}")
        return 0
    finally:
        conn.close()

    # Try to assign newly created preventive requests
    if created > 0:
        from assignment_engine import process_queued_requests
        process_queued_requests()


def recalculate_priorities():
    """
    FR-21 / Smart Downtime Priority Recalculation:
    Recalculates 5-factor priority scores as downtime elapses.
    When downtime crosses threshold (30m, 60m, 120m, 240m), urgency rating and score update.
    """
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT mr.id, mr.urgency, mr.production_stopped, mr.safety_risk,
                          mr.safety_risk_rating, mr.production_impact_rating, mr.workaround_rating,
                          mr.safety_hazard_flag, mr.reported_at, mr.actual_breakdown_time,
                          mr.schedule_id, mr.type, mr.priority_score, mr.priority_level,
                          e.criticality, dl.start_time AS downtime_start
                   FROM maintenance_requests mr
                   JOIN equipment e ON e.id = mr.equipment_id
                   LEFT JOIN downtime_logs dl ON dl.request_id = mr.id AND dl.end_time IS NULL
                   WHERE mr.status IN ('open', 'queued', 'assigned', 'on_hold', 'rework_required')"""
            )
            requests = cursor.fetchall()

            # Load priority rules
            cursor.execute("SELECT * FROM priority_rules WHERE active = TRUE LIMIT 1")
            rules = cursor.fetchone()

            for req in requests:
                # Determine start timestamp for downtime calculation
                dt_start = req.get('downtime_start') or req.get('actual_breakdown_time') or req.get('reported_at')
                dt_minutes = calculate_downtime_minutes(dt_start)

                s_rate = req.get('safety_risk_rating') or (5 if req.get('safety_risk') else 1)
                p_rate = req.get('production_impact_rating') or (5 if req.get('production_stopped') else 2)
                w_rate = req.get('workaround_rating') or 3
                c_rate = req.get('criticality') or 3
                safety_flag = bool(req.get('safety_hazard_flag') or req.get('safety_risk') or s_rate >= 4)

                result = calculate_smart_priority(
                    safety_risk_rating=s_rate,
                    production_impact_rating=p_rate,
                    machine_criticality=c_rate,
                    downtime_minutes=dt_minutes,
                    workaround_rating=w_rate,
                    safety_hazard_flag=safety_flag,
                    urgency_rating=int(req.get('urgency') or 3),
                    rules=rules
                )

                if result['score'] != req['priority_score'] or result['level'] != req['priority_level']:
                    cursor.execute(
                        """UPDATE maintenance_requests SET
                             priority_score = %s, priority_level = %s, score_breakdown = %s,
                             updated_at = NOW()
                           WHERE id = %s""",
                        (result['score'], result['level'], json.dumps(result['breakdown']), req['id'])
                    )

            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Priority recalc error: {e}")
    finally:
        conn.close()


def check_sla_and_escalate():
    """
    FR-34: Check SLA timers and escalate overdue requests.
    - At 100% of target → flag Delayed, notify supervisor
    - At 150% of target → notify administrator
    """
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT id, priority_level, reported_at, status, `delayed`, escalated_to
                   FROM maintenance_requests
                   WHERE status NOT IN ('completed', 'closed', 'cancelled')"""
            )
            requests = cursor.fetchall()

            for req in requests:
                targets = get_sla_targets(req['priority_level'])
                reported_at = req['reported_at']
                if isinstance(reported_at, str):
                    reported_at = datetime.fromisoformat(reported_at.replace('Z', '+00:00'))
                if reported_at.tzinfo is None:
                    reported_at = reported_at.replace(tzinfo=timezone.utc)

                elapsed_min = math.floor((datetime.now(timezone.utc) - reported_at).total_seconds() / 60)
                target_min = targets['response'] if req['status'] in ('open', 'queued') else targets['resolution']

                if elapsed_min >= target_min and not req['delayed']:
                    # Set delayed flag and notify supervisors
                    cursor.execute(
                        "UPDATE maintenance_requests SET `delayed` = TRUE, escalated_to = 'supervisor', "
                        "updated_at = NOW() WHERE id = %s",
                        (req['id'],)
                    )
                    _notify_role(cursor, 'supervisor', req['id'],
                                 'delay', 'SLA Breach - Response Overdue',
                                 f"Request is overdue by {elapsed_min - target_min} min")

                elif elapsed_min >= int(target_min * 1.5) and req.get('escalated_to') != 'administrator':
                    # Escalate to administrator
                    cursor.execute(
                        "UPDATE maintenance_requests SET escalated_to = 'administrator', "
                        "updated_at = NOW() WHERE id = %s",
                        (req['id'],)
                    )
                    _notify_role(cursor, 'administrator', req['id'],
                                 'escalation', 'SLA Escalation - Critical Delay',
                                 f"Request has been delayed for {elapsed_min} minutes (150% of target)")

            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"SLA check error: {e}")
    finally:
        conn.close()


def _notify_role(cursor, role, request_id, notif_type, title, message):
    """Send notifications to all users with the given role."""
    cursor.execute(
        "SELECT id FROM users WHERE role = %s AND active = TRUE", (role,)
    )
    users = cursor.fetchall()
    for user in users:
        # Avoid duplicate notifications
        cursor.execute(
            "SELECT id FROM notifications WHERE user_id = %s AND request_id = %s AND type = %s LIMIT 1",
            (user['id'], request_id, notif_type)
        )
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO notifications (id, user_id, type, title, message, request_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), user['id'], notif_type, title, message, request_id)
            )


def process_queued_retry():
    """Retry assigning queued requests every minute (FR-25)."""
    from assignment_engine import process_queued_requests
    process_queued_requests()
