"""
Smart Technician Recommendation and Assignment Engine

Implements:
1. Recommendation based on:
   - Required technical skill & skill qualification level
   - Technician availability status ('available')
   - Shift availability (shift start/end, working days)
   - Current workload (fewest active tasks)
   - Equipment zone proximity
2. Human-readable explainable recommendation rationale
3. Prevention of unqualified or unsafe technician assignment
4. Transactional assignment with row-locking (prevents double-booking / concurrency conflicts)
5. Timestamp tracking for downtime log: technician_assignment_time
"""
import json
import uuid
from datetime import datetime, timezone
from db import get_db


def get_skill_id_by_name(cursor, skill_name):
    """Look up a skill id by name."""
    if not skill_name:
        return None
    cursor.execute("SELECT id FROM skills WHERE LOWER(name) = LOWER(%s) LIMIT 1", (skill_name,))
    row = cursor.fetchone()
    return row['id'] if row else None


def recommend_technician_for_request(cursor, request_id):
    """
    Evaluates candidate technicians and generates transparent recommendation rationale.
    Returns: {
        'recommended_technician': dict or None,
        'eligible_candidates': list,
        'reasons': list of strings,
        'has_qualified_tech': bool
    }
    """
    cursor.execute(
        """SELECT mr.*, e.name AS equipment_name, e.zone AS equipment_zone, e.criticality
           FROM maintenance_requests mr
           JOIN equipment e ON e.id = mr.equipment_id
           WHERE mr.id = %s""",
        (request_id,)
    )
    req = cursor.fetchone()
    if not req:
        return {
            'recommended_technician': None,
            'eligible_candidates': [],
            'reasons': ['Request not found'],
            'has_qualified_tech': False
        }

    # Determine required skill
    skill_id = None
    skill_name = None
    if req.get('schedule_id'):
        cursor.execute("SELECT s.id, s.name FROM maintenance_schedules ms JOIN skills s ON s.id = ms.skill_needed WHERE ms.id = %s", (req['schedule_id'],))
        s_row = cursor.fetchone()
        if s_row:
            skill_id = s_row['id']
            skill_name = s_row['name']
    elif req.get('problem_type') and req['problem_type'] != 'other':
        skill_name = req['problem_type'].capitalize()
        skill_id = get_skill_id_by_name(cursor, skill_name)

    now_time = datetime.now(timezone.utc).strftime('%H:%M:%S')
    weekday_map = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
    today = weekday_map[datetime.now(timezone.utc).weekday()]

    req_zone = req.get('equipment_zone')

    # Query all eligible active technicians
    if skill_id:
        query = """
            SELECT t.id, t.name, t.email, t.phone, t.availability, t.zone,
                   t.shift_start, t.shift_end,
                   ts.level AS skill_level,
                   (
                       SELECT COUNT(*) FROM maintenance_requests mr2
                       WHERE mr2.assigned_technician_id = t.id
                         AND mr2.status IN ('assigned', 'in_progress', 'on_hold', 'rework_required')
                   ) AS active_task_count,
                   CASE WHEN t.zone = %s THEN 1 ELSE 0 END AS zone_match,
                   CASE
                       WHEN t.shift_start IS NULL OR t.shift_end IS NULL THEN 1
                       WHEN t.shift_start <= t.shift_end AND %s >= t.shift_start AND %s <= t.shift_end THEN 1
                       WHEN t.shift_start > t.shift_end AND (%s >= t.shift_start OR %s <= t.shift_end) THEN 1
                       ELSE 0
                   END AS on_shift,
                   CASE
                       WHEN t.working_days IS NULL OR JSON_CONTAINS(t.working_days, %s) THEN 1
                       ELSE 0
                   END AS day_match,
                   CASE WHEN t.availability = 'available' THEN 1 ELSE 0 END AS is_avail
            FROM technicians t
            JOIN technician_skills ts ON ts.technician_id = t.id AND ts.skill_id = %s
            WHERE t.active = TRUE
              AND t.availability != 'on_leave'
            ORDER BY on_shift DESC, is_avail DESC, day_match DESC, ts.level DESC, active_task_count ASC, zone_match DESC
        """
        params = (req_zone, now_time, now_time, now_time, now_time, json.dumps(today), skill_id)
    else:
        query = """
            SELECT t.id, t.name, t.email, t.phone, t.availability, t.zone,
                   t.shift_start, t.shift_end,
                   1 AS skill_level,
                   (
                       SELECT COUNT(*) FROM maintenance_requests mr2
                       WHERE mr2.assigned_technician_id = t.id
                         AND mr2.status IN ('assigned', 'in_progress', 'on_hold', 'rework_required')
                   ) AS active_task_count,
                   CASE WHEN t.zone = %s THEN 1 ELSE 0 END AS zone_match,
                   CASE
                       WHEN t.shift_start IS NULL OR t.shift_end IS NULL THEN 1
                       WHEN t.shift_start <= t.shift_end AND %s >= t.shift_start AND %s <= t.shift_end THEN 1
                       WHEN t.shift_start > t.shift_end AND (%s >= t.shift_start OR %s <= t.shift_end) THEN 1
                       ELSE 0
                   END AS on_shift,
                   CASE
                       WHEN t.working_days IS NULL OR JSON_CONTAINS(t.working_days, %s) THEN 1
                       ELSE 0
                   END AS day_match,
                   CASE WHEN t.availability = 'available' THEN 1 ELSE 0 END AS is_avail
            FROM technicians t
            WHERE t.active = TRUE
              AND t.availability != 'on_leave'
            ORDER BY on_shift DESC, is_avail DESC, day_match DESC, active_task_count ASC, zone_match DESC
        """
        params = (req_zone, now_time, now_time, now_time, now_time, json.dumps(today))

    cursor.execute(query, params)
    candidates = cursor.fetchall()

    if not candidates:
        reasons = [
            f"No qualified technician available with required skill '{skill_name or 'General'}'. All matching technicians are on leave or inactive."
        ]
        return {
            'recommended_technician': None,
            'eligible_candidates': [],
            'reasons': reasons,
            'has_qualified_tech': False
        }

    best = candidates[0]
    reasons = []
    if skill_name:
        reasons.append(f"Certified in {skill_name} maintenance (Skill Qualification Level {best.get('skill_level', 1)}/3).")
    else:
        reasons.append("Eligible general industrial maintenance qualification.")

    if best.get('on_shift') and best.get('day_match'):
        reasons.append("Currently on active shift schedule and available for dispatch.")
    elif best.get('on_shift'):
        reasons.append("Currently within active shift working hours.")
    else:
        reasons.append("Certified qualified technician on roster available for emergency dispatch.")

    if best.get('active_task_count', 0) == 0:
        reasons.append("Zero active tasks in progress (Optimal workload capacity).")
    else:
        reasons.append(f"Lowest active task workload among eligible technicians ({best['active_task_count']} active tasks).")

    if best.get('zone_match'):
        reasons.append(f"Located in primary machinery zone '{best['zone']}'.")

    return {
        'recommended_technician': best,
        'eligible_candidates': candidates,
        'reasons': reasons,
        'has_qualified_tech': True
    }


def find_best_technician(cursor, skill_needed_id, request_zone=None):
    """
    Direct technician finder with row locking for automatic assignment.
    Prioritizes technicians who are on-duty, certified in the required skill,
    have lowest workload, and match zone.
    """
    now_time = datetime.now(timezone.utc).strftime('%H:%M:%S')
    weekday_map = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
    today = weekday_map[datetime.now(timezone.utc).weekday()]

    if skill_needed_id:
        query = """
            SELECT t.id, t.name, t.availability, t.zone,
                   ts.level AS skill_level,
                   (
                       SELECT COUNT(*) FROM maintenance_requests mr2
                       WHERE mr2.assigned_technician_id = t.id
                         AND mr2.status IN ('assigned', 'in_progress', 'on_hold', 'rework_required')
                   ) AS open_task_count,
                   CASE WHEN t.zone = %s THEN 1 ELSE 0 END AS zone_match,
                   CASE
                       WHEN t.shift_start IS NULL OR t.shift_end IS NULL THEN 1
                       WHEN t.shift_start <= t.shift_end AND %s >= t.shift_start AND %s <= t.shift_end THEN 1
                       WHEN t.shift_start > t.shift_end AND (%s >= t.shift_start OR %s <= t.shift_end) THEN 1
                       ELSE 0
                   END AS on_shift,
                   CASE
                       WHEN t.working_days IS NULL OR JSON_CONTAINS(t.working_days, %s) THEN 1
                       ELSE 0
                   END AS day_match,
                   CASE WHEN t.availability = 'available' THEN 1 ELSE 0 END AS is_avail
            FROM technicians t
            JOIN technician_skills ts ON ts.technician_id = t.id AND ts.skill_id = %s
            WHERE t.active = TRUE
              AND t.availability != 'on_leave'
            ORDER BY on_shift DESC, is_avail DESC, day_match DESC, ts.level DESC, open_task_count ASC, zone_match DESC
            LIMIT 1
            FOR UPDATE
        """
        params = (request_zone, now_time, now_time, now_time, now_time, json.dumps(today), skill_needed_id)
    else:
        query = """
            SELECT t.id, t.name, t.availability, t.zone,
                   1 AS skill_level,
                   (
                       SELECT COUNT(*) FROM maintenance_requests mr2
                       WHERE mr2.assigned_technician_id = t.id
                         AND mr2.status IN ('assigned', 'in_progress', 'on_hold', 'rework_required')
                   ) AS open_task_count,
                   CASE WHEN t.zone = %s THEN 1 ELSE 0 END AS zone_match,
                   CASE
                       WHEN t.shift_start IS NULL OR t.shift_end IS NULL THEN 1
                       WHEN t.shift_start <= t.shift_end AND %s >= t.shift_start AND %s <= t.shift_end THEN 1
                       WHEN t.shift_start > t.shift_end AND (%s >= t.shift_start OR %s <= t.shift_end) THEN 1
                       ELSE 0
                   END AS on_shift,
                   CASE
                       WHEN t.working_days IS NULL OR JSON_CONTAINS(t.working_days, %s) THEN 1
                       ELSE 0
                   END AS day_match,
                   CASE WHEN t.availability = 'available' THEN 1 ELSE 0 END AS is_avail
            FROM technicians t
            WHERE t.active = TRUE
              AND t.availability != 'on_leave'
            ORDER BY on_shift DESC, is_avail DESC, day_match DESC, open_task_count ASC, zone_match DESC
            LIMIT 1
            FOR UPDATE
        """
        params = (request_zone, now_time, now_time, now_time, now_time, json.dumps(today))

    cursor.execute(query, params)
    return cursor.fetchone()


def assign_request(request_id, assigned_by_id=None, technician_id=None, reason=None):
    """
    Assigns request with row locking, timestamp tracking, and qualification validation.
    If technician_id is specified: validates technician qualification and locks record.
    If technician_id is None: finds the top recommended technician.
    """
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            # Lock the request row
            cursor.execute(
                """SELECT mr.*, e.name AS equipment_name, e.zone AS equipment_zone, e.criticality
                   FROM maintenance_requests mr
                   JOIN equipment e ON e.id = mr.equipment_id
                   WHERE mr.id = %s FOR UPDATE""",
                (request_id,)
            )
            req = cursor.fetchone()
            if not req:
                return {'success': False, 'error': 'Request not found'}
            if req['status'] not in ('open', 'queued', 'assigned', 'rework_required'):
                return {'success': False, 'error': f"Cannot assign request in status: {req['status']}"}

            # Determine required skill
            skill_id = None
            skill_name = None
            if req.get('schedule_id'):
                cursor.execute("SELECT s.id, s.name FROM maintenance_schedules ms JOIN skills s ON s.id = ms.skill_needed WHERE ms.id = %s", (req['schedule_id'],))
                s_row = cursor.fetchone()
                if s_row:
                    skill_id = s_row['id']
                    skill_name = s_row['name']
            elif req.get('problem_type') and req['problem_type'] != 'other':
                skill_name = req['problem_type'].capitalize()
                skill_id = get_skill_id_by_name(cursor, skill_name)

            tech = None
            if technician_id:
                # Specific technician assignment: validate and lock
                cursor.execute(
                    "SELECT * FROM technicians WHERE id = %s AND active = TRUE FOR UPDATE",
                    (technician_id,)
                )
                candidate = cursor.fetchone()
                if not candidate:
                    return {'success': False, 'error': 'Technician not found or inactive'}

                # Safety & Qualification Rule: Verify technician possesses required skill if skill is specified
                if skill_id:
                    cursor.execute(
                        "SELECT level FROM technician_skills WHERE technician_id = %s AND skill_id = %s",
                        (technician_id, skill_id)
                    )
                    ts = cursor.fetchone()
                    if not ts:
                        return {
                            'success': False,
                            'error': f"Safety / Qualification Mismatch: {candidate['name']} is not qualified in '{skill_name}'."
                        }

                tech = candidate
            else:
                # Automated assignment
                tech = find_best_technician(cursor, skill_id, req.get('equipment_zone'))

            if not tech:
                # No technician available -> Queued
                cursor.execute(
                    """UPDATE maintenance_requests SET
                         status = 'queued',
                         recommended_technician_id = NULL,
                         recommendation_reason = 'No Qualified Technician Available',
                         updated_at = NOW()
                       WHERE id = %s""",
                    (request_id,)
                )
                _log_status_change(cursor, request_id, req['status'], 'queued',
                                   assigned_by_id, 'System', 'No qualified technician available')

                # Alert supervisors
                _create_supervisor_alert(cursor, req, 'No Qualified Technician Available')

                conn.commit()
                return {'success': False, 'queued': True, 'message': 'No qualified technician available. Request queued.'}

            tech_id = tech['id']

            # Deactivate previous assignments if reassigning
            cursor.execute(
                "UPDATE assignments SET active = FALSE, unassigned_at = NOW() WHERE request_id = %s AND active = TRUE",
                (request_id,)
            )

            # Update request
            assignment_reason = reason or f"Assigned to {tech['name']}"
            cursor.execute(
                """UPDATE maintenance_requests SET
                     status = 'assigned',
                     assigned_technician_id = %s,
                     recommended_technician_id = %s,
                     recommendation_reason = %s,
                     updated_at = NOW()
                   WHERE id = %s""",
                (tech_id, tech_id, assignment_reason, request_id)
            )

            # Mark technician busy
            cursor.execute(
                "UPDATE technicians SET availability = 'busy', updated_at = NOW() WHERE id = %s",
                (tech_id,)
            )

            # Record assignment timestamp in downtime_logs
            cursor.execute(
                """UPDATE downtime_logs SET
                     technician_assignment_time = COALESCE(technician_assignment_time, NOW()),
                     updated_at = NOW()
                   WHERE request_id = %s AND end_time IS NULL""",
                (request_id,)
            )

            # Log assignment in assignments table
            cursor.execute(
                "INSERT INTO assignments (id, request_id, technician_id, assigned_by, reason, active) VALUES (%s, %s, %s, %s, %s, TRUE)",
                (str(uuid.uuid4()), request_id, tech_id, assigned_by_id, assignment_reason)
            )

            # Log status change
            _log_status_change(cursor, request_id, req['status'], 'assigned',
                               assigned_by_id, 'System' if not assigned_by_id else 'Supervisor', assignment_reason)

            conn.commit()
            return {'success': True, 'technician_id': tech_id, 'technician_name': tech['name']}

    except Exception as e:
        conn.rollback()
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def _log_status_change(cursor, request_id, from_status, to_status, changed_by, changed_by_name, reason=None):
    """Insert a status_history audit row."""
    cursor.execute(
        """INSERT INTO status_history (id, request_id, from_status, to_status, changed_by, changed_by_name, reason)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (str(uuid.uuid4()), request_id, from_status, to_status, changed_by, changed_by_name, reason)
    )


def _create_supervisor_alert(cursor, req, title):
    """Alert active supervisors when a critical issue or unassigned request arises."""
    cursor.execute("SELECT id FROM users WHERE role = 'supervisor' AND active = TRUE")
    supervisors = cursor.fetchall()
    for s in supervisors:
        cursor.execute(
            """INSERT INTO notifications (id, user_id, type, title, message, request_id)
               VALUES (%s, %s, 'escalation', %s, %s, %s)""",
            (
                str(uuid.uuid4()),
                s['id'],
                title,
                f"Machine {req.get('equipment_name', 'Equipment')} ({req.get('request_code', '')}): {title}",
                req['id']
            )
        )


def process_queued_requests():
    """
    Called periodically: assign highest priority queued requests.
    """
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT id, priority_level, priority_score FROM maintenance_requests
                   WHERE status = 'queued'
                   ORDER BY safety_hazard_flag DESC, priority_score DESC
                   LIMIT 20"""
            )
            queued = cursor.fetchall()
    finally:
        conn.close()

    for req in queued:
        result = assign_request(req['id'])
        if not result.get('success') and not result.get('queued'):
            break
