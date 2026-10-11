"""
Smart Maintenance Tracker - Flask REST API
Connects React frontend to MySQL via phpMyAdmin/XAMPP
"""
import os
import sys
import uuid
import json
import math
import bcrypt
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required,
    get_jwt_identity, get_jwt
)
from dotenv import load_dotenv

from db import get_db
from priority_engine import (
    calculate_priority_score, get_waiting_hours,
    get_days_overdue, get_sla_targets
)
from assignment_engine import assign_request, process_queued_requests
from scheduler import start_scheduler, run_preventive_scheduler, recalculate_priorities

load_dotenv()

app = Flask(__name__)
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'change-me-in-production')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=24)

CORS(app, origins=[os.getenv('FRONTEND_URL', 'http://localhost:5173'),
                   'http://localhost:3000', 'http://localhost:5174'],
     supports_credentials=True)

jwt = JWTManager(app)

from decimal import Decimal
from datetime import time, date
from flask.json.provider import DefaultJSONProvider

class CustomJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, timedelta):
            total_seconds = int(obj.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            if seconds:
                return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            return f"{hours:02d}:{minutes:02d}"
        if isinstance(obj, time):
            return obj.strftime('%H:%M:%S')
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)

app.json = CustomJSONProvider(app)

# ============================================================
# Helpers
# ============================================================

def success(data=None, status=200, **kwargs):
    resp = {'success': True}
    if data is not None:
        resp['data'] = data
    resp.update(kwargs)
    return jsonify(resp), status


def error(message, status=400):
    return jsonify({'success': False, 'error': message}), status


from werkzeug.exceptions import HTTPException

@app.errorhandler(500)
def handle_500(e):
    import traceback
    traceback.print_exc()
    return jsonify({'success': False, 'error': 'Internal server error', 'detail': str(e)}), 500


@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({'success': False, 'error': e.description}), e.code
    import traceback
    traceback.print_exc()
    return jsonify({'success': False, 'error': str(e)}), 500


def require_roles(*roles):
    """Role-based access decorator (FR-3)."""
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            identity = get_jwt_identity()
            claims = get_jwt()
            role = claims.get('role', '')
            if role not in roles:
                return error('Access denied', 403)
            g.user_id = identity
            g.user_role = role
            g.user_name = claims.get('name', '')
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def all_roles(fn):
    """Allow any authenticated user."""
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        identity = get_jwt_identity()
        claims = get_jwt()
        g.user_id = identity
        g.user_role = claims.get('role', '')
        g.user_name = claims.get('name', '')
        return fn(*args, **kwargs)
    return wrapper


def log_status_change(cursor, request_id, from_status, to_status, changed_by, changed_by_name, reason=None):
    cursor.execute(
        """INSERT INTO status_history (id, request_id, from_status, to_status, changed_by, changed_by_name, reason)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (str(uuid.uuid4()), request_id, from_status, to_status, changed_by, changed_by_name, reason)
    )


def create_notification(cursor, user_id, notif_type, title, message, request_id=None):
    cursor.execute(
        """INSERT INTO notifications (id, user_id, type, title, message, request_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (str(uuid.uuid4()), user_id, notif_type, title, message, request_id)
    )


def notify_role(cursor, role, notif_type, title, message, request_id=None):
    cursor.execute("SELECT id FROM users WHERE role = %s AND active = TRUE", (role,))
    for u in cursor.fetchall():
        create_notification(cursor, u['id'], notif_type, title, message, request_id)


def next_request_code(cursor):
    cursor.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTRING(request_code, 5) AS UNSIGNED)), 0) + 1 AS n "
        "FROM maintenance_requests WHERE request_code REGEXP '^REQ-[0-9]+$'"
    )
    row = cursor.fetchone()
    return f"REQ-{str(row['n']).zfill(4)}"


# ============================================================
# AUTH ROUTES (FR-1, FR-2)
# ============================================================

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or not password:
        return error('Email and password required')

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE email = %s AND active = TRUE", (email,))
            user = cursor.fetchone()
    finally:
        conn.close()

    if not user:
        return error('Invalid credentials', 401)

    try:
        password_matches = bcrypt.checkpw(password.encode(), user['password_hash'].encode())
    except Exception:
        password_matches = False

    if not password_matches:
        return error('Invalid credentials', 401)

    token = create_access_token(
        identity=user['id'],
        additional_claims={'role': user['role'], 'name': user['full_name'], 'email': user['email']}
    )
    return success({
        'token': token,
        'user': {
            'id': user['id'],
            'email': user['email'],
            'full_name': user['full_name'],
            'role': user['role'],
            'phone': user['phone'],
        }
    })


@app.route('/api/auth/me', methods=['GET'])
@all_roles
def get_me():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, email, full_name, role, phone, active FROM users WHERE id = %s", (g.user_id,))
            user = cursor.fetchone()
    finally:
        conn.close()
    if not user:
        return error('User not found', 404)
    return success(user)


# ============================================================
# EQUIPMENT ROUTES (FR-5, FR-6, FR-10)
# ============================================================

@app.route('/api/equipment', methods=['GET'])
@all_roles
def list_equipment():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM equipment WHERE active = TRUE ORDER BY code")
            rows = cursor.fetchall()
    finally:
        conn.close()
    return success(rows)


@app.route('/api/equipment', methods=['POST'])
@require_roles('administrator')
def create_equipment():
    data = request.get_json()
    required = ['code', 'name', 'zone', 'criticality', 'cost_per_hour']
    for f in required:
        if not data.get(f) and data.get(f) != 0:
            return error(f'{f} is required')

    eq_id = str(uuid.uuid4())
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO equipment (id, code, name, zone, criticality, cost_per_hour,
                     status, manufacturer, install_date, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    eq_id,
                    data['code'].upper(),
                    data['name'],
                    data['zone'],
                    int(data['criticality']),
                    float(data['cost_per_hour']),
                    data.get('status', 'running'),
                    data.get('manufacturer'),
                    data.get('install_date'),
                    data.get('notes'),
                )
            )
            conn.commit()
            cursor.execute("SELECT * FROM equipment WHERE id = %s", (eq_id,))
            eq = cursor.fetchone()
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success(eq, 201)


@app.route('/api/equipment/<eq_id>', methods=['GET'])
@all_roles
def get_equipment(eq_id):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM equipment WHERE id = %s", (eq_id,))
            eq = cursor.fetchone()
            if not eq:
                return error('Equipment not found', 404)
            # History
            cursor.execute(
                """SELECT mr.*, t.name AS technician_name
                   FROM maintenance_requests mr
                   LEFT JOIN technicians t ON t.id = mr.assigned_technician_id
                   WHERE mr.equipment_id = %s ORDER BY mr.reported_at DESC LIMIT 50""",
                (eq_id,)
            )
            requests = cursor.fetchall()
            cursor.execute(
                "SELECT * FROM downtime_logs WHERE equipment_id = %s ORDER BY start_time DESC LIMIT 50",
                (eq_id,)
            )
            downtime = cursor.fetchall()
            cursor.execute(
                """SELECT ms.*, s.name AS skill_name
                   FROM maintenance_schedules ms
                   LEFT JOIN skills s ON s.id = ms.skill_needed
                   WHERE ms.equipment_id = %s ORDER BY ms.next_due_date""",
                (eq_id,)
            )
            schedules = cursor.fetchall()
    finally:
        conn.close()

    for r in requests:
        if r.get('score_breakdown') and isinstance(r['score_breakdown'], str):
            try:
                r['score_breakdown'] = json.loads(r['score_breakdown'])
            except Exception:
                pass

    return success({'equipment': eq, 'requests': requests, 'downtime': downtime, 'schedules': schedules})


@app.route('/api/equipment/<eq_id>', methods=['PATCH'])
@require_roles('administrator')
def update_equipment(eq_id):
    data = request.get_json()
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM equipment WHERE id = %s", (eq_id,))
            if not cursor.fetchone():
                return error('Equipment not found', 404)
            fields = []
            values = []
            for f in ['name', 'zone', 'criticality', 'cost_per_hour', 'status',
                      'manufacturer', 'install_date', 'notes', 'active']:
                if f in data:
                    fields.append(f'{f} = %s')
                    values.append(data[f])
            if not fields:
                return error('No fields to update')
            values.append(eq_id)
            cursor.execute(
                f"UPDATE equipment SET {', '.join(fields)}, updated_at = NOW() WHERE id = %s",
                values
            )
            conn.commit()
            cursor.execute("SELECT * FROM equipment WHERE id = %s", (eq_id,))
            eq = cursor.fetchone()
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success(eq)


# ============================================================
# SCHEDULES ROUTES (FR-7, FR-8, FR-9)
# ============================================================

@app.route('/api/schedules', methods=['GET'])
@require_roles('supervisor', 'administrator')
def list_schedules():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT ms.*, e.name AS equipment_name, e.code AS equipment_code,
                          e.zone AS equipment_zone, s.name AS skill_name
                   FROM maintenance_schedules ms
                   JOIN equipment e ON e.id = ms.equipment_id
                   LEFT JOIN skills s ON s.id = ms.skill_needed
                   ORDER BY ms.next_due_date"""
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return success(rows)


@app.route('/api/schedules', methods=['POST'])
@require_roles('supervisor', 'administrator')
def create_schedule():
    data = request.get_json()
    required = ['equipment_id', 'task_name', 'interval_type', 'interval_value', 'lead_days']
    for f in required:
        if not data.get(f):
            return error(f'{f} is required')

    last_service = data.get('last_service_date')
    interval_type = data['interval_type']
    interval_value = int(data['interval_value'])

    # Calculate next due date
    next_due = None
    if last_service and interval_type == 'days':
        from datetime import date as d_cls
        last = datetime.strptime(last_service, '%Y-%m-%d').date()
        next_due = (last + timedelta(days=interval_value)).isoformat()

    sched_id = str(uuid.uuid4())
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            # Resolve skill name to ID
            skill_id = None
            if data.get('skill_needed'):
                cursor.execute("SELECT id FROM skills WHERE name = %s OR id = %s LIMIT 1",
                               (data['skill_needed'], data['skill_needed']))
                s = cursor.fetchone()
                if s:
                    skill_id = s['id']

            cursor.execute(
                """INSERT INTO maintenance_schedules (
                     id, equipment_id, task_name, skill_needed, interval_type, interval_value,
                     lead_days, last_service_date, next_due_date, estimated_duration_minutes, active
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)""",
                (
                    sched_id, data['equipment_id'], data['task_name'], skill_id,
                    interval_type, interval_value,
                    int(data['lead_days']), last_service, next_due,
                    data.get('estimated_duration_minutes'),
                )
            )
            conn.commit()
            cursor.execute(
                """SELECT ms.*, e.name AS equipment_name, s.name AS skill_name
                   FROM maintenance_schedules ms
                   JOIN equipment e ON e.id = ms.equipment_id
                   LEFT JOIN skills s ON s.id = ms.skill_needed
                   WHERE ms.id = %s""",
                (sched_id,)
            )
            sched = cursor.fetchone()
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success(sched, 201)


@app.route('/api/schedules/<sched_id>', methods=['PATCH'])
@require_roles('supervisor', 'administrator')
def update_schedule(sched_id):
    data = request.get_json()
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM maintenance_schedules WHERE id = %s", (sched_id,))
            existing = cursor.fetchone()
            if not existing:
                return error('Schedule not found', 404)

            fields = []
            values = []
            for f in ['task_name', 'interval_type', 'interval_value', 'lead_days',
                      'last_service_date', 'next_due_date', 'estimated_duration_minutes', 'active']:
                if f in data:
                    fields.append(f'{f} = %s')
                    values.append(data[f])

            if 'skill_needed' in data:
                if data['skill_needed']:
                    cursor.execute("SELECT id FROM skills WHERE name = %s OR id = %s LIMIT 1",
                                   (data['skill_needed'], data['skill_needed']))
                    s = cursor.fetchone()
                    fields.append('skill_needed = %s')
                    values.append(s['id'] if s else None)
                else:
                    fields.append('skill_needed = %s')
                    values.append(None)

            if fields:
                values.append(sched_id)
                cursor.execute(
                    f"UPDATE maintenance_schedules SET {', '.join(fields)}, updated_at = NOW() WHERE id = %s",
                    values
                )
            conn.commit()
            cursor.execute(
                """SELECT ms.*, e.name AS equipment_name, s.name AS skill_name
                   FROM maintenance_schedules ms JOIN equipment e ON e.id = ms.equipment_id
                   LEFT JOIN skills s ON s.id = ms.skill_needed WHERE ms.id = %s""",
                (sched_id,)
            )
            sched = cursor.fetchone()
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success(sched)


# ============================================================
# REQUESTS ROUTES (FR-11 to FR-17)
# ============================================================

@app.route('/api/requests', methods=['GET'])
@all_roles
def list_requests():
    status_f = request.args.get('status')
    priority_f = request.args.get('priority')
    equipment_f = request.args.get('equipment')
    tech_f = request.args.get('technician')
    type_f = request.args.get('type')
    limit = min(int(request.args.get('limit', 100)), 500)

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            where = ['1=1']
            params = []
            if status_f:
                where.append('mr.status = %s')
                params.append(status_f)
            if priority_f:
                where.append('mr.priority_level = %s')
                params.append(priority_f)
            if equipment_f:
                where.append('mr.equipment_id = %s')
                params.append(equipment_f)
            if tech_f:
                where.append('mr.assigned_technician_id = %s')
                params.append(tech_f)
            if type_f:
                where.append('mr.type = %s')
                params.append(type_f)
            # Operator/technician scope: only their own or assigned
            if g.user_role == 'operator':
                where.append('mr.reported_by = %s')
                params.append(g.user_id)
            elif g.user_role == 'technician':
                where.append(
                    '(mr.reported_by = %s OR mr.assigned_technician_id IN '
                    '(SELECT id FROM technicians WHERE user_id = %s))'
                )
                params.extend([g.user_id, g.user_id])

            cursor.execute(
                f"""SELECT mr.*, e.name AS equipment_name, e.code AS equipment_code,
                           e.zone AS equipment_zone, e.criticality AS equipment_criticality,
                           e.status AS equipment_status,
                           t.name AS technician_name
                    FROM maintenance_requests mr
                    JOIN equipment e ON e.id = mr.equipment_id
                    LEFT JOIN technicians t ON t.id = mr.assigned_technician_id
                    WHERE {' AND '.join(where)}
                    ORDER BY mr.safety_hazard_flag DESC, mr.priority_score DESC, mr.production_impact_rating DESC, mr.reported_at ASC
                    LIMIT %s""",
                params + [limit]
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    for r in rows:
        if r.get('score_breakdown') and isinstance(r['score_breakdown'], str):
            try:
                r['score_breakdown'] = json.loads(r['score_breakdown'])
            except Exception:
                pass

    from priority_engine import sort_requests_by_priority
    rows = sort_requests_by_priority(rows)

    return success(rows)


@app.route('/api/requests', methods=['POST'])
@require_roles('operator', 'supervisor', 'administrator')
def create_request():
    """FR-11: Create a breakdown request."""
    data = request.get_json()

    # Validate required fields (Form C)
    required = ['equipment_id', 'problem_type', 'urgency', 'description']
    for f in required:
        if not data.get(f):
            return error(f'{f} is required')
    if len(data['description']) < 10:
        return error('Description must be at least 10 characters')
    if int(data['urgency']) not in range(1, 5):
        return error('Urgency must be 1 to 4')

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM equipment WHERE id = %s AND active = TRUE", (data['equipment_id'],))
            equip = cursor.fetchone()
            if not equip:
                return error('Equipment not found')

            # FR-13: Edge case - already Down equipment with open request
            if equip['status'] == 'down':
                cursor.execute(
                    """SELECT id FROM maintenance_requests WHERE equipment_id = %s
                       AND status NOT IN ('completed', 'closed', 'cancelled') AND type = 'breakdown' LIMIT 1""",
                    (data['equipment_id'],)
                )
                existing = cursor.fetchone()
                if existing:
                    # Link to existing request, notify supervisor
                    notify_role(
                        cursor, 'supervisor', 'info',
                        'Duplicate Breakdown Reported',
                        f"A new breakdown was reported for {equip['name']} which is already Down. Linked to existing request.",
                        existing['id']
                    )
                    conn.commit()
                    return success({'message': 'Equipment already down, linked to existing request',
                                    'existing_request_id': existing['id']}, 200)

            # Extract 5-factor inputs
            s_rating = int(data.get('safety_risk_rating') or (5 if data.get('safety_risk') else 1))
            p_rating = int(data.get('production_impact_rating') or (5 if data.get('production_stopped') else (int(data['urgency']) + 1 if int(data['urgency']) <= 3 else 5)))
            w_rating = int(data.get('workaround_rating') or 3)
            c_rating = int(equip.get('criticality') or 3)
            safety_flag = bool(data.get('safety_hazard_flag') or data.get('safety_risk') or s_rating >= 4)

            # Determine downtime start timestamp
            actual_time = data.get('actual_breakdown_time')
            if actual_time:
                try:
                    dt_start = datetime.fromisoformat(str(actual_time).replace('Z', '+00:00'))
                except Exception:
                    dt_start = datetime.now(timezone.utc)
            else:
                dt_start = datetime.now(timezone.utc)

            # Calculate initial downtime in minutes for score calculation
            initial_downtime_minutes = max(0.0, (datetime.now(timezone.utc) - (dt_start.replace(tzinfo=timezone.utc) if dt_start.tzinfo is None else dt_start)).total_seconds() / 60.0)

            # Load priority rules if available
            cursor.execute("SELECT * FROM priority_rules WHERE active = TRUE LIMIT 1")
            rules = cursor.fetchone()

            # Calculate smart priority using percentage-based algorithm
            from priority_engine import calculate_smart_priority
            priority = calculate_smart_priority(
                safety_risk_rating=s_rating,
                production_impact_rating=p_rating,
                machine_criticality=c_rating,
                downtime_minutes=initial_downtime_minutes,
                workaround_rating=w_rating,
                safety_hazard_flag=safety_flag,
                urgency_rating=int(data.get('urgency', 3)),
                rules=rules
            )

            req_id = str(uuid.uuid4())
            req_code = next_request_code(cursor)

            cursor.execute(
                """INSERT INTO maintenance_requests (
                     id, request_code, type, source, equipment_id,
                     problem_type, urgency, safety_risk_rating, production_impact_rating,
                     workaround_rating, safety_hazard_flag, safety_review_notes,
                     production_stopped, safety_risk, description, photo_url,
                     reported_by, reported_at, actual_breakdown_time, status,
                     priority_score, priority_level, score_breakdown,
                     recommended_technician_id, recommendation_reason
                   ) VALUES (%s, %s, 'breakdown', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, 'open', %s, %s, %s, %s, %s)""",
                (
                    req_id, req_code,
                    g.user_role, data['equipment_id'],
                    data['problem_type'], int(data['urgency']),
                    s_rating, p_rating, w_rating, safety_flag,
                    data.get('safety_review_notes'),
                    bool(data.get('production_stopped', False) or p_rating >= 4),
                    bool(data.get('safety_risk', False) or s_rating >= 4),
                    data['description'], data.get('photo_url'),
                    g.user_id,
                    dt_start.strftime('%Y-%m-%d %H:%M:%S'),
                    priority['score'], priority['level'],
                    json.dumps(priority['breakdown']),
                    None, priority['safety_warning'] if priority.get('is_safety_escalated') else None
                )
            )

            # Set equipment to Down, start downtime record using dt_start
            cursor.execute(
                "UPDATE equipment SET status = 'down', updated_at = NOW() WHERE id = %s",
                (data['equipment_id'],)
            )
            downtime_id = str(uuid.uuid4())
            cursor.execute(
                """INSERT INTO downtime_logs (id, equipment_id, request_id, start_time, planned, status)
                   VALUES (%s, %s, %s, %s, FALSE, 'open')""",
                (downtime_id, data['equipment_id'], req_id, dt_start.strftime('%Y-%m-%d %H:%M:%S'))
            )

            # Log status history
            log_status_change(cursor, req_id, None, 'open', g.user_id, g.user_name,
                              'Safety Escalation Triggered' if priority.get('is_safety_escalated') else 'Breakdown reported')

            # If safety escalation, create urgent notifications
            if priority.get('is_safety_escalated'):
                notify_role(
                    cursor, 'supervisor', 'escalation',
                    f'MANDATORY SAFETY ESCALATION: {equip["name"]}',
                    f"Safety Hazard Flagged for {equip['name']} ({req_code}). Immediate safety review required before work begins.",
                    req_id
                )
                notify_role(
                    cursor, 'administrator', 'escalation',
                    f'MANDATORY SAFETY ESCALATION: {equip["name"]}',
                    f"Immediate Safety Escalation on ticket {req_code} for machine {equip['name']}.",
                    req_id
                )

            conn.commit()

        # Try to assign (outside the main transaction)
        assign_result = assign_request(req_id, g.user_id)

        # Send notification to supervisor
        conn2 = get_db()
        try:
            with conn2.cursor() as cursor2:
                notify_role(cursor2, 'supervisor', 'info',
                            f'New {priority["level"]} Breakdown',
                            f'{equip["name"]}: {data["description"][:80]}',
                            req_id)
                if assign_result.get('success'):
                    notify_role(cursor2, 'technician', 'assignment',
                                'Task Assigned',
                                f'You have been assigned to {equip["name"]} ({priority["level"]})',
                                req_id)
                conn2.commit()
        finally:
            conn2.close()

        # Fetch full request
        conn3 = get_db()
        try:
            with conn3.cursor() as cur:
                cur.execute(
                    """SELECT mr.*, e.name AS equipment_name, e.code AS equipment_code
                       FROM maintenance_requests mr JOIN equipment e ON e.id = mr.equipment_id
                       WHERE mr.id = %s""",
                    (req_id,)
                )
                req = cur.fetchone()
        finally:
            conn3.close()

        if req and req.get('score_breakdown') and isinstance(req['score_breakdown'], str):
            try:
                req['score_breakdown'] = json.loads(req['score_breakdown'])
            except Exception:
                pass

        return success(req, 201)

    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()


@app.route('/api/requests/<req_id>', methods=['GET'])
@all_roles
def get_request(req_id):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT mr.*, e.name AS equipment_name, e.code AS equipment_code,
                          e.zone AS equipment_zone, e.criticality AS equipment_criticality,
                          e.cost_per_hour AS equipment_cost_per_hour,
                          e.status AS equipment_status,
                          t.name AS technician_name, t.phone AS technician_phone
                   FROM maintenance_requests mr
                   JOIN equipment e ON e.id = mr.equipment_id
                   LEFT JOIN technicians t ON t.id = mr.assigned_technician_id
                   WHERE mr.id = %s""",
                (req_id,)
            )
            req = cursor.fetchone()
            if not req:
                return error('Request not found', 404)

            cursor.execute(
                "SELECT * FROM status_history WHERE request_id = %s ORDER BY changed_at DESC",
                (req_id,)
            )
            history = cursor.fetchall()

            cursor.execute(
                """SELECT rc.*, u.full_name AS user_name FROM request_comments rc
                   LEFT JOIN users u ON u.id = rc.user_id
                   WHERE rc.request_id = %s ORDER BY rc.created_at""",
                (req_id,)
            )
            comments = cursor.fetchall()

            cursor.execute(
                """SELECT a.*, t.name AS technician_name FROM assignments a
                   JOIN technicians t ON t.id = a.technician_id
                   WHERE a.request_id = %s ORDER BY a.assigned_at DESC""",
                (req_id,)
            )
            assignments = cursor.fetchall()

            cursor.execute(
                "SELECT * FROM downtime_logs WHERE request_id = %s ORDER BY start_time DESC",
                (req_id,)
            )
            downtime = cursor.fetchall()
    finally:
        conn.close()

    if req.get('score_breakdown') and isinstance(req['score_breakdown'], str):
        try:
            req['score_breakdown'] = json.loads(req['score_breakdown'])
        except Exception:
            pass

    # Add SLA info
    targets = get_sla_targets(req['priority_level'])
    reported_at = req['reported_at']
    if isinstance(reported_at, datetime):
        if reported_at.tzinfo is None:
            reported_at = reported_at.replace(tzinfo=timezone.utc)
    else:
        reported_at = datetime.fromisoformat(str(reported_at)).replace(tzinfo=timezone.utc)

    elapsed_min = math.floor((datetime.now(timezone.utc) - reported_at).total_seconds() / 60)
    req['sla_targets'] = targets
    req['elapsed_minutes'] = elapsed_min

    return success({
        'request': req,
        'history': history,
        'comments': comments,
        'assignments': assignments,
        'downtime': downtime,
    })


@app.route('/api/requests/<req_id>', methods=['PATCH'])
@require_roles('operator', 'technician', 'supervisor', 'administrator')
def update_request_details(req_id):
    """
    Update request details and immediately recalculate percentage-based priority.
    Recalculates scores when request details change, explains why each task received its priority,
    and handles safety-critical faults with immediate escalation.
    """
    data = request.get_json() or {}
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT mr.*, e.criticality AS equipment_criticality, e.name AS equipment_name
                   FROM maintenance_requests mr
                   JOIN equipment e ON e.id = mr.equipment_id
                   WHERE mr.id = %s FOR UPDATE""",
                (req_id,)
            )
            req = cursor.fetchone()
            if not req:
                return error('Request not found', 404)

            # Check if equipment changed
            new_equip_id = data.get('equipment_id', req['equipment_id'])
            if new_equip_id != req['equipment_id']:
                cursor.execute("SELECT criticality, name FROM equipment WHERE id = %s", (new_equip_id,))
                eq_row = cursor.fetchone()
                c_rating = int(eq_row['criticality']) if eq_row else int(req['equipment_criticality'])
                equip_name = eq_row['name'] if eq_row else req['equipment_name']
            else:
                c_rating = int(req['equipment_criticality'])
                equip_name = req['equipment_name']

            # Extract updated factor ratings
            s_rating = int(data.get('safety_risk_rating', req['safety_risk_rating']))
            p_rating = int(data.get('production_impact_rating', req['production_impact_rating']))
            w_rating = int(data.get('workaround_rating', req['workaround_rating'])) if ('workaround_rating' in data or req['workaround_rating'] is not None) else None
            u_rating = int(data.get('urgency', req['urgency']))
            safety_flag = bool(data.get('safety_hazard_flag', req['safety_hazard_flag']) or s_rating >= 4 or data.get('safety_risk', req['safety_risk']))

            # Compute downtime minutes from actual breakdown time or reported at
            dt_start = req.get('actual_breakdown_time') or req.get('reported_at')
            from priority_engine import calculate_downtime_minutes, calculate_smart_priority
            dt_minutes = calculate_downtime_minutes(dt_start)

            # Fetch rules
            cursor.execute("SELECT * FROM priority_rules WHERE active = TRUE LIMIT 1")
            rules = cursor.fetchone()

            # Recalculate smart percentage priority
            p_res = calculate_smart_priority(
                safety_risk_rating=s_rating,
                production_impact_rating=p_rating,
                machine_criticality=c_rating,
                downtime_minutes=dt_minutes,
                workaround_rating=w_rating,
                safety_hazard_flag=safety_flag,
                urgency_rating=u_rating,
                rules=rules
            )

            # Build update fields
            fields = [
                'urgency = %s', 'safety_risk_rating = %s', 'production_impact_rating = %s',
                'safety_hazard_flag = %s', 'priority_score = %s', 'priority_level = %s',
                'score_breakdown = %s', 'updated_at = NOW()'
            ]
            values = [
                u_rating, s_rating, p_rating, safety_flag,
                p_res['score'], p_res['level'], json.dumps(p_res['breakdown'])
            ]

            if w_rating is not None:
                fields.append('workaround_rating = %s')
                values.append(w_rating)

            if 'problem_type' in data:
                fields.append('problem_type = %s')
                values.append(data['problem_type'])
            if 'description' in data:
                fields.append('description = %s')
                values.append(data['description'])
            if 'safety_review_notes' in data:
                fields.append('safety_review_notes = %s')
                values.append(data['safety_review_notes'])
            if 'equipment_id' in data:
                fields.append('equipment_id = %s')
                values.append(new_equip_id)
            if 'production_stopped' in data:
                fields.append('production_stopped = %s')
                values.append(bool(data['production_stopped']))
            if 'safety_risk' in data:
                fields.append('safety_risk = %s')
                values.append(bool(data['safety_risk']))

            values.append(req_id)
            cursor.execute(f"UPDATE maintenance_requests SET {', '.join(fields)} WHERE id = %s", values)

            # Audit status change / priority recalculation
            prev_score = req['priority_score']
            prev_level = req['priority_level']
            if prev_score != p_res['score'] or prev_level != p_res['level']:
                log_status_change(
                    cursor, req_id, req['status'], req['status'], g.user_id, g.user_name,
                    f"Priority Recalculated: {prev_level} ({prev_score}%) -> {p_res['level']} ({p_res['score']}%)"
                )

            # Safety Escalation check: if newly escalated, notify supervisors & admins
            if p_res.get('is_safety_escalated') and not req.get('safety_hazard_flag') and req.get('safety_risk_rating', 1) < 4:
                notify_role(
                    cursor, 'supervisor', 'escalation',
                    f'MANDATORY SAFETY ESCALATION: {equip_name}',
                    f"Safety Hazard Flagged for {equip_name} ({req['request_code']}). Immediate safety review required.",
                    req_id
                )
                notify_role(
                    cursor, 'administrator', 'escalation',
                    f'MANDATORY SAFETY ESCALATION: {equip_name}',
                    f"Immediate Safety Escalation on ticket {req['request_code']}.",
                    req_id
                )

            conn.commit()

            cursor.execute(
                """SELECT mr.*, e.name AS equipment_name, e.code AS equipment_code,
                          e.zone AS equipment_zone, e.criticality AS equipment_criticality,
                          t.name AS technician_name
                   FROM maintenance_requests mr
                   JOIN equipment e ON e.id = mr.equipment_id
                   LEFT JOIN technicians t ON t.id = mr.assigned_technician_id
                   WHERE mr.id = %s""",
                (req_id,)
            )
            updated_req = cursor.fetchone()
            if updated_req and updated_req.get('score_breakdown') and isinstance(updated_req['score_breakdown'], str):
                try:
                    updated_req['score_breakdown'] = json.loads(updated_req['score_breakdown'])
                except Exception:
                    pass

            return success(updated_req)
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()


@app.route('/api/requests/recalculate', methods=['POST'])
@all_roles
def trigger_recalculate():
    """Recalculate priority scores for all active requests."""
    from scheduler import recalculate_priorities
    recalculate_priorities()
    return success({'message': 'Priority scores recalculated for all active requests.'})


@app.route('/api/requests/<req_id>/recommendation', methods=['GET'])
@all_roles
def get_request_recommendation(req_id):
    """Get smart technician recommendation for a request."""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            from assignment_engine import recommend_technician_for_request
            rec = recommend_technician_for_request(cursor, req_id)
            return success(rec)
    except Exception as e:
        return error(str(e))
    finally:
        conn.close()


@app.route('/api/requests/<req_id>/verify', methods=['POST'])
@require_roles('supervisor', 'administrator')
def verify_request(req_id):
    """Independent repair verification step."""
    data = request.get_json()
    decision = data.get('decision')  # 'passed' or 'rework_required'
    comments = data.get('comments', '')

    if decision not in ('passed', 'rework_required'):
        return error("Decision must be 'passed' or 'rework_required'")

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM maintenance_requests WHERE id = %s FOR UPDATE", (req_id,))
            req = cursor.fetchone()
            if not req:
                return error('Request not found', 404)
            if req['status'] != 'pending_verification':
                return error(f'Request must be in pending_verification status (current: {req["status"]})')

            vh_id = str(uuid.uuid4())
            cursor.execute(
                """INSERT INTO verification_history (id, request_id, reviewer_id, reviewer_name, decision, comments)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (vh_id, req_id, g.user_id, g.user_name, decision, comments)
            )

            if decision == 'rework_required':
                new_status = 'rework_required'
                cursor.execute(
                    "UPDATE maintenance_requests SET status = %s, updated_at = NOW() WHERE id = %s",
                    (new_status, req_id)
                )
                log_status_change(cursor, req_id, 'pending_verification', new_status, g.user_id, g.user_name, f'Verification failed: {comments}')
                if req.get('assigned_technician_id'):
                    cursor.execute("SELECT user_id FROM technicians WHERE id = %s", (req['assigned_technician_id'],))
                    tech_user = cursor.fetchone()
                    if tech_user and tech_user.get('user_id'):
                        create_notification(cursor, tech_user['user_id'], 'info',
                                            'Rework Required',
                                            f'Verification failed for {req["request_code"]}: {comments}',
                                            req_id)
            else:
                # Passed verification
                new_status = 'pending_verification'  # Ready for return to service
                cursor.execute(
                    "UPDATE downtime_logs SET verification_completed_at = NOW(), updated_at = NOW() WHERE request_id = %s AND end_time IS NULL",
                    (req_id,)
                )
                log_status_change(cursor, req_id, 'pending_verification', 'pending_verification', g.user_id, g.user_name, f'Verification passed: {comments}')

            conn.commit()
            return success({'message': f'Verification recorded ({decision})', 'decision': decision})
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()


@app.route('/api/requests/<req_id>/authorize-return-to-service', methods=['POST'])
@require_roles('supervisor', 'administrator')
def authorize_return_to_service(req_id):
    """Authorized Return to Service & Downtime Closure."""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT mr.*, e.name AS equipment_name, e.cost_per_hour, e.id AS equip_id
                   FROM maintenance_requests mr JOIN equipment e ON e.id = mr.equipment_id
                   WHERE mr.id = %s FOR UPDATE""",
                (req_id,)
            )
            req = cursor.fetchone()
            if not req:
                return error('Request not found', 404)
            if req['status'] not in ('pending_verification', 'completed', 'in_progress'):
                return error(f'Request in status {req["status"]} cannot be authorized')

            old_status = req['status']
            new_status = 'completed'

            # 1. Update request status to completed / closed
            cursor.execute(
                """UPDATE maintenance_requests SET status = %s, completed_at = NOW(), closed_at = NOW(), updated_at = NOW()
                   WHERE id = %s""",
                (new_status, req_id)
            )

            # 2. Set equipment status to running
            cursor.execute(
                "UPDATE equipment SET status = 'running', updated_at = NOW() WHERE id = %s",
                (req['equip_id'],)
            )

            # 3. Close downtime log and record return-to-service timestamp
            cursor.execute(
                """SELECT id, start_time FROM downtime_logs
                   WHERE request_id = %s AND end_time IS NULL ORDER BY start_time LIMIT 1""",
                (req_id,)
            )
            dt = cursor.fetchone()
            if dt:
                start = dt['start_time']
                if isinstance(start, str):
                    start = datetime.fromisoformat(start.replace('Z', '+00:00'))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                minutes = int((now - start).total_seconds() / 60)
                hours = minutes / 60.0
                loss = hours * float(req['cost_per_hour'])

                cursor.execute(
                    """UPDATE downtime_logs SET
                         end_time = NOW(),
                         returned_to_service_at = NOW(),
                         authorized_by = %s,
                         status = 'closed',
                         minutes = %s,
                         estimated_loss = %s,
                         updated_at = NOW()
                       WHERE id = %s""",
                    (g.user_id, minutes, loss, dt['id'])
                )

            # 4. Free the assigned technician if no other active tasks
            if req.get('assigned_technician_id'):
                cursor.execute(
                    """SELECT COUNT(*) AS cnt FROM maintenance_requests
                       WHERE assigned_technician_id = %s AND status IN ('assigned', 'in_progress', 'on_hold', 'rework_required')
                         AND id != %s""",
                    (req['assigned_technician_id'], req_id)
                )
                other = cursor.fetchone()
                if (other['cnt'] or 0) == 0:
                    cursor.execute(
                        "UPDATE technicians SET availability = 'available', updated_at = NOW() WHERE id = %s",
                        (req['assigned_technician_id'],)
                    )

            log_status_change(cursor, req_id, old_status, new_status, g.user_id, g.user_name, 'Authorized Return-to-Service')
            conn.commit()
            return success({'message': 'Machine authorized to return to service. Downtime closed.'})
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()


@app.route('/api/requests/<req_id>/status', methods=['PATCH'])
@require_roles('technician', 'supervisor', 'administrator')
def update_request_status(req_id):
    """FR-14, FR-28, FR-29, FR-30: Status workflow."""
    data = request.get_json()
    action = data.get('action')  # start, hold, resume, submit_report, complete, close, cancel

    valid_actions = {'start', 'hold', 'resume', 'submit_report', 'complete', 'close', 'cancel'}
    if action not in valid_actions:
        return error(f'Invalid action. Must be one of: {", ".join(valid_actions)}')

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT mr.*, e.name AS equipment_name, e.cost_per_hour, e.id AS equip_id
                   FROM maintenance_requests mr JOIN equipment e ON e.id = mr.equipment_id
                   WHERE mr.id = %s FOR UPDATE""",
                (req_id,)
            )
            req = cursor.fetchone()
            if not req:
                return error('Request not found', 404)

            old_status = req['status']

            # Validate transitions
            transitions = {
                'start': (['assigned', 'rework_required'], 'in_progress'),
                'hold': (['in_progress'], 'on_hold'),
                'resume': (['on_hold'], 'in_progress'),
                'submit_report': (['in_progress', 'rework_required'], 'pending_verification'),
                'complete': (['in_progress', 'pending_verification'], 'completed'),
                'close': (['completed'], 'closed'),
                'cancel': (['open', 'queued', 'assigned', 'rework_required'], 'cancelled'),
            }
            allowed_from, new_status = transitions[action]
            if old_status not in allowed_from:
                return error(f'Cannot {action} a request in status: {old_status}')

            # Validate required fields per action
            if action == 'hold' and not data.get('hold_reason'):
                return error('Hold reason is required')
            if action in ('submit_report', 'complete'):
                if not data.get('work_done'):
                    return error('work_done is required on repair submission')
                if not data.get('root_cause'):
                    return error('root_cause is required on repair submission')
                if not data.get('time_spent_minutes'):
                    return error('time_spent_minutes is required on repair submission')
            if action == 'cancel' and not data.get('cancel_reason') and g.user_role not in ('administrator',):
                return error('Cancel reason is required')

            update_fields = ['status = %s', 'updated_at = NOW()']
            update_values = [new_status]

            if action == 'hold':
                update_fields.append('hold_reason = %s')
                update_values.append(data.get('hold_reason'))
            elif action in ('submit_report', 'complete'):
                update_fields += ['work_done = %s', 'root_cause = %s', 'parts_used = %s',
                                  'time_spent_minutes = %s', 'follow_up_needed = %s']
                update_values += [
                    data.get('work_done'),
                    data.get('root_cause'),
                    data.get('parts_used'),
                    int(data.get('time_spent_minutes', 0)),
                    bool(data.get('follow_up_needed', False)),
                ]
                # Store completion photo if provided
                if data.get('completion_photo_url'):
                    update_fields.append('completion_photo_url = %s')
                    update_values.append(data['completion_photo_url'])
                if action == 'complete':
                    update_fields.append('completed_at = NOW()')
                if data.get('delay_reason'):
                    update_fields.append('delay_reason = %s')
                    update_values.append(data['delay_reason'])
            elif action == 'close':
                update_fields.append('closed_at = NOW()')
            elif action == 'cancel':
                update_fields.append('cancel_reason = %s')
                update_values.append(data.get('cancel_reason'))

            update_values.append(req_id)
            cursor.execute(
                f"UPDATE maintenance_requests SET {', '.join(update_fields)} WHERE id = %s",
                update_values
            )

            # Record timestamps in downtime logs
            if action == 'start':
                cursor.execute(
                    "UPDATE downtime_logs SET repair_start_time = NOW(), updated_at = NOW() WHERE request_id = %s AND end_time IS NULL",
                    (req_id,)
                )
            elif action == 'submit_report':
                cursor.execute(
                    "UPDATE downtime_logs SET repair_completion_time = NOW(), updated_at = NOW() WHERE request_id = %s AND end_time IS NULL",
                    (req_id,)
                )
                notify_role(cursor, 'supervisor', 'info',
                            'Repair Submitted - Pending Verification',
                            f"Technician completed repair on {req['equipment_name']}. Verification required.",
                            req_id)

            log_status_change(
                cursor, req_id, old_status, new_status,
                g.user_id, g.user_name, data.get('hold_reason') or data.get('cancel_reason')
            )

            # If action is complete (direct completion), handle downtime closure
            if action == 'complete' and req['type'] == 'breakdown':
                cursor.execute(
                    "UPDATE equipment SET status = 'running', updated_at = NOW() WHERE id = %s",
                    (req['equip_id'],)
                )
                cursor.execute(
                    """SELECT id, start_time FROM downtime_logs
                       WHERE request_id = %s AND end_time IS NULL ORDER BY start_time LIMIT 1""",
                    (req_id,)
                )
                dt = cursor.fetchone()
                if dt:
                    start = dt['start_time']
                    if isinstance(start, str):
                        start = datetime.fromisoformat(start)
                    if start.tzinfo is None:
                        start = start.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    minutes = int((now - start).total_seconds() / 60)
                    hours = minutes / 60.0
                    loss = hours * float(req['cost_per_hour'])
                    cursor.execute(
                        """UPDATE downtime_logs SET end_time = NOW(), returned_to_service_at = NOW(),
                             status = 'closed', minutes = %s, estimated_loss = %s,
                             updated_at = NOW() WHERE id = %s""",
                        (minutes, loss, dt['id'])
                    )

                if req.get('assigned_technician_id'):
                    cursor.execute(
                        """SELECT COUNT(*) AS cnt FROM maintenance_requests
                           WHERE assigned_technician_id = %s AND status IN ('assigned', 'in_progress', 'on_hold', 'rework_required')
                             AND id != %s""",
                        (req['assigned_technician_id'], req_id)
                    )
                    other = cursor.fetchone()
                    if (other['cnt'] or 0) == 0:
                        cursor.execute(
                            "UPDATE technicians SET availability = 'available', updated_at = NOW() WHERE id = %s",
                            (req['assigned_technician_id'],)
                        )

            conn.commit()
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()

    return success({'message': f'Status updated to {new_status}', 'new_status': new_status})


@app.route('/api/requests/<req_id>/assign', methods=['POST'])
@require_roles('supervisor', 'administrator')
def assign_request_route(req_id):
    """FR-22, FR-27: Assign or reassign."""
    data = request.get_json()
    tech_id = data.get('technician_id')

    if tech_id:
        # Manual assignment/reassign (FR-27)
        conn = get_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT mr.*, t.id AS old_tech_id FROM maintenance_requests mr
                       LEFT JOIN technicians t ON t.id = mr.assigned_technician_id
                       WHERE mr.id = %s FOR UPDATE""",
                    (req_id,)
                )
                req = cursor.fetchone()
                if not req:
                    return error('Request not found', 404)
                if req['status'] not in ('open', 'queued', 'assigned'):
                    return error(f'Cannot reassign request in status: {req["status"]}')

                cursor.execute("SELECT * FROM technicians WHERE id = %s AND active = TRUE", (tech_id,))
                tech = cursor.fetchone()
                if not tech:
                    return error('Technician not found')

                # Deactivate old assignment
                if req.get('old_tech_id'):
                    cursor.execute(
                        "UPDATE assignments SET active = FALSE, unassigned_at = NOW() WHERE request_id = %s AND active = TRUE",
                        (req_id,)
                    )

                old_status = req['status']
                cursor.execute(
                    """UPDATE maintenance_requests SET status = 'assigned',
                         assigned_technician_id = %s, updated_at = NOW() WHERE id = %s""",
                    (tech_id, req_id)
                )
                cursor.execute(
                    "UPDATE technicians SET availability = 'busy', updated_at = NOW() WHERE id = %s",
                    (tech_id,)
                )
                cursor.execute(
                    """INSERT INTO assignments (id, request_id, technician_id, assigned_by, active)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (str(uuid.uuid4()), req_id, tech_id, g.user_id)
                )
                log_status_change(cursor, req_id, old_status, 'assigned',
                                  g.user_id, g.user_name,
                                  data.get('reason', 'Manual assignment by supervisor'))
                conn.commit()
        except Exception as e:
            conn.rollback()
            return error(str(e))
        finally:
            conn.close()
        return success({'message': 'Assigned successfully'})
    else:
        # Auto-assign
        result = assign_request(req_id, g.user_id)
        if result.get('success'):
            return success({'message': f'Assigned to {result["technician_name"]}'})
        return error(result.get('error') or result.get('message', 'No technician available'))


@app.route('/api/requests/<req_id>/preempt', methods=['POST'])
@require_roles('supervisor', 'administrator')
def confirm_preemption(req_id):
    """FR-26: Supervisor confirms preemption."""
    data = request.get_json()
    target_request_id = data.get('target_request_id')  # The P3/P4 request to preempt

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM maintenance_requests WHERE id = %s FOR UPDATE", (req_id,))
            p1_req = cursor.fetchone()
            if not p1_req or p1_req['status'] != 'queued':
                return error('P1 request must be in Queued status')

            cursor.execute("SELECT * FROM maintenance_requests WHERE id = %s FOR UPDATE", (target_request_id,))
            target = cursor.fetchone()
            if not target or target['status'] not in ('assigned', 'in_progress'):
                return error('Target request must be in Assigned or In Progress status')

            tech_id = target['assigned_technician_id']

            # Put target on hold and back to queue
            cursor.execute(
                """UPDATE maintenance_requests SET status = 'on_hold',
                     hold_reason = 'Preempted by P1 request', updated_at = NOW()
                   WHERE id = %s""",
                (target_request_id,)
            )
            log_status_change(cursor, target_request_id, target['status'], 'on_hold',
                              g.user_id, g.user_name, 'Preempted by P1 request')

            # Assign tech to P1
            cursor.execute(
                """UPDATE maintenance_requests SET status = 'assigned',
                     assigned_technician_id = %s, updated_at = NOW()
                   WHERE id = %s""",
                (tech_id, req_id)
            )
            cursor.execute(
                """INSERT INTO assignments (id, request_id, technician_id, assigned_by, active)
                   VALUES (%s, %s, %s, %s, TRUE)""",
                (str(uuid.uuid4()), req_id, tech_id, g.user_id)
            )
            log_status_change(cursor, req_id, 'queued', 'assigned',
                              g.user_id, g.user_name, 'Preemption confirmed by supervisor')
            conn.commit()
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success({'message': 'Preemption confirmed'})


@app.route('/api/requests/<req_id>/comments', methods=['POST'])
@all_roles
def add_comment(req_id):
    data = request.get_json()
    if not data.get('comment'):
        return error('Comment is required')
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            comment_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO request_comments (id, request_id, user_id, author_name, comment) VALUES (%s, %s, %s, %s, %s)",
                (comment_id, req_id, g.user_id, g.user_name, data['comment'])
            )
            conn.commit()
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success({'message': 'Comment added', 'id': comment_id}, 201)


# ============================================================
# TECHNICIANS ROUTES
# ============================================================

@app.route('/api/technicians', methods=['GET'])
@all_roles
def list_technicians():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT t.*,
                          COUNT(mr.id) AS open_task_count
                   FROM technicians t
                   LEFT JOIN maintenance_requests mr ON mr.assigned_technician_id = t.id
                     AND mr.status IN ('assigned', 'in_progress', 'on_hold')
                   WHERE t.active = TRUE
                   GROUP BY t.id
                   ORDER BY t.name"""
            )
            techs = cursor.fetchall()
            for tech in techs:
                # Parse working_days JSON
                if isinstance(tech.get('working_days'), str):
                    try:
                        tech['working_days'] = json.loads(tech['working_days'])
                    except Exception:
                        pass
                for time_col in ('shift_start', 'shift_end'):
                    val = tech.get(time_col)
                    if isinstance(val, timedelta):
                        s = int(val.total_seconds())
                        tech[time_col] = f"{s // 3600:02d}:{(s % 3600) // 60:02d}"
                    elif isinstance(val, time):
                        tech[time_col] = val.strftime('%H:%M')
                # Get skills
                cursor.execute(
                    """SELECT ts.*, s.name AS skill_name, s.id AS skill_id
                       FROM technician_skills ts JOIN skills s ON s.id = ts.skill_id
                       WHERE ts.technician_id = %s""",
                    (tech['id'],)
                )
                tech['skills'] = cursor.fetchall()
    finally:
        conn.close()
    return success(techs)


@app.route('/api/technicians', methods=['POST'])
@require_roles('administrator')
def create_technician():
    data = request.get_json()
    required = ['name']
    for f in required:
        if not data.get(f):
            return error(f'{f} is required')

    tech_id = str(uuid.uuid4())
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            wd = data.get('working_days', ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'])
            cursor.execute(
                """INSERT INTO technicians (id, name, email, shift_start, shift_end, working_days,
                     availability, zone, phone, active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)""",
                (
                    tech_id, data['name'], data.get('email'),
                    data.get('shift_start'), data.get('shift_end'),
                    json.dumps(wd),
                    data.get('availability', 'available'),
                    data.get('zone'), data.get('phone')
                )
            )
            # Skills
            if data.get('skills'):
                for skill in data['skills']:
                    cursor.execute(
                        "SELECT id FROM skills WHERE name = %s OR id = %s LIMIT 1",
                        (skill.get('name', ''), skill.get('skill_id', ''))
                    )
                    s = cursor.fetchone()
                    if s:
                        cursor.execute(
                            "INSERT IGNORE INTO technician_skills (id, technician_id, skill_id, level) VALUES (%s, %s, %s, %s)",
                            (str(uuid.uuid4()), tech_id, s['id'], skill.get('level', 1))
                        )
            conn.commit()
            cursor.execute("SELECT * FROM technicians WHERE id = %s", (tech_id,))
            tech = cursor.fetchone()
            if tech:
                for time_col in ('shift_start', 'shift_end'):
                    val = tech.get(time_col)
                    if isinstance(val, timedelta):
                        s = int(val.total_seconds())
                        tech[time_col] = f"{s // 3600:02d}:{(s % 3600) // 60:02d}"
                    elif isinstance(val, time):
                        tech[time_col] = val.strftime('%H:%M')
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success(tech, 201)


@app.route('/api/technicians/<tech_id>', methods=['PATCH'])
@require_roles('administrator', 'supervisor')
def update_technician(tech_id):
    data = request.get_json()
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            fields = []
            values = []
            for f in ['name', 'email', 'shift_start', 'shift_end', 'availability', 'zone', 'phone', 'active']:
                if f in data:
                    fields.append(f'{f} = %s')
                    values.append(data[f])
            if 'working_days' in data:
                fields.append('working_days = %s')
                values.append(json.dumps(data['working_days']))
            if fields:
                values.append(tech_id)
                cursor.execute(
                    f"UPDATE technicians SET {', '.join(fields)}, updated_at = NOW() WHERE id = %s",
                    values
                )
            # Update skills if provided
            if 'skills' in data:
                cursor.execute("DELETE FROM technician_skills WHERE technician_id = %s", (tech_id,))
                for skill in (data['skills'] or []):
                    cursor.execute(
                        "SELECT id FROM skills WHERE name = %s OR id = %s LIMIT 1",
                        (skill.get('name', ''), skill.get('skill_id', ''))
                    )
                    s = cursor.fetchone()
                    if s:
                        cursor.execute(
                            "INSERT IGNORE INTO technician_skills (id, technician_id, skill_id, level) VALUES (%s, %s, %s, %s)",
                            (str(uuid.uuid4()), tech_id, s['id'], skill.get('level', 1))
                        )
            conn.commit()
            cursor.execute("SELECT * FROM technicians WHERE id = %s", (tech_id,))
            tech = cursor.fetchone()
            if tech:
                for time_col in ('shift_start', 'shift_end'):
                    val = tech.get(time_col)
                    if isinstance(val, timedelta):
                        s = int(val.total_seconds())
                        tech[time_col] = f"{s // 3600:02d}:{(s % 3600) // 60:02d}"
                    elif isinstance(val, time):
                        tech[time_col] = val.strftime('%H:%M')
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success(tech)


# ============================================================
# DOWNTIME ROUTES (FR-31)
# ============================================================

@app.route('/api/downtime', methods=['GET'])
@require_roles('supervisor', 'administrator')
def list_downtime():
    equipment_id = request.args.get('equipment')
    from_date = request.args.get('from')
    to_date = request.args.get('to')

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            where = ['1=1']
            params = []
            if equipment_id:
                where.append('dl.equipment_id = %s')
                params.append(equipment_id)
            if from_date:
                where.append('dl.start_time >= %s')
                params.append(from_date)
            if to_date:
                where.append('dl.start_time <= %s')
                params.append(to_date + ' 23:59:59')
            cursor.execute(
                f"""SELECT dl.*, e.name AS equipment_name, e.code AS equipment_code,
                           e.cost_per_hour, mr.request_code, mr.type AS request_type
                    FROM downtime_logs dl
                    JOIN equipment e ON e.id = dl.equipment_id
                    LEFT JOIN maintenance_requests mr ON mr.id = dl.request_id
                    WHERE {' AND '.join(where)}
                    ORDER BY dl.start_time DESC LIMIT 500""",
                params
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return success(rows)


# ============================================================
# ANALYTICS (FR-32, FR-33, FR-35, FR-36)
# ============================================================

@app.route('/api/analytics/summary', methods=['GET'])
@require_roles('supervisor', 'administrator')
def analytics_summary():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            today_start = datetime.now(timezone.utc).strftime('%Y-%m-%d') + ' 00:00:00'

            # Equipment down with live downtime
            cursor.execute(
                """SELECT e.*, dl.start_time AS down_since, dl.id AS downtime_id
                   FROM equipment e
                   LEFT JOIN downtime_logs dl ON dl.equipment_id = e.id AND dl.end_time IS NULL
                   WHERE e.status = 'down' AND e.active = TRUE"""
            )
            down_equipment = cursor.fetchall()

            # Open requests
            cursor.execute(
                """SELECT mr.*, e.name AS equipment_name, e.code AS equipment_code
                   FROM maintenance_requests mr JOIN equipment e ON e.id = mr.equipment_id
                   WHERE mr.status NOT IN ('completed', 'closed', 'cancelled')
                   ORDER BY mr.priority_score DESC"""
            )
            open_requests = cursor.fetchall()

            for r in open_requests:
                if r.get('score_breakdown') and isinstance(r['score_breakdown'], str):
                    try:
                        r['score_breakdown'] = json.loads(r['score_breakdown'])
                    except Exception:
                        pass

            # Downtime today
            cursor.execute(
                """SELECT dl.*, e.name AS equipment_name, e.cost_per_hour
                   FROM downtime_logs dl JOIN equipment e ON e.id = dl.equipment_id
                   WHERE dl.start_time >= %s""",
                (today_start,)
            )
            downtime_today = cursor.fetchall()

            # MTTR (last 30 days)
            cursor.execute(
                """SELECT AVG(minutes) AS avg_minutes
                   FROM downtime_logs
                   WHERE minutes IS NOT NULL AND planned = FALSE
                     AND start_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)"""
            )
            mttr_row = cursor.fetchone()
            mttr = float(mttr_row['avg_minutes'] or 0)

            # MTBF per equipment (breakdown count last 30 days)
            cursor.execute(
                """SELECT e.id, e.name, e.code,
                          COUNT(mr.id) AS breakdown_count,
                          SUM(dl.minutes) AS total_downtime_min,
                          SUM(dl.estimated_loss) AS total_loss
                   FROM equipment e
                   LEFT JOIN maintenance_requests mr ON mr.equipment_id = e.id
                     AND mr.type = 'breakdown'
                     AND mr.reported_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                   LEFT JOIN downtime_logs dl ON dl.equipment_id = e.id
                     AND dl.start_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                   WHERE e.active = TRUE
                   GROUP BY e.id, e.name, e.code
                   ORDER BY breakdown_count DESC"""
            )
            equipment_stats = cursor.fetchall()

            # FR-36: Recurring failures - 3+ breakdowns in 30 days
            cursor.execute(
                """SELECT e.id, e.name, e.code, COUNT(mr.id) AS breakdown_count
                   FROM equipment e
                   JOIN maintenance_requests mr ON mr.equipment_id = e.id
                   WHERE mr.type = 'breakdown'
                     AND mr.reported_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                   GROUP BY e.id, e.name, e.code
                   HAVING COUNT(mr.id) >= 3"""
            )
            recurring_failures = cursor.fetchall()

            # Total downtime today
            total_dt_min = sum(
                (d['minutes'] or (
                    int((datetime.now(timezone.utc) -
                         (d['start_time'].replace(tzinfo=timezone.utc)
                          if d['start_time'].tzinfo is None else d['start_time'])
                         ).total_seconds() / 60)
                    if not d.get('end_time') else 0
                ))
                for d in downtime_today
            )

            total_loss_today = sum(float(d.get('estimated_loss') or 0) for d in downtime_today)

    finally:
        conn.close()

    return success({
        'down_equipment': down_equipment,
        'open_requests': open_requests,
        'overdue_requests': [
            r for r in open_requests
            if _is_overdue(r)
        ],
        'downtime_today': downtime_today,
        'mttr_minutes': mttr,
        'total_downtime_minutes_today': total_dt_min,
        'total_loss_today': total_loss_today,
        'equipment_stats': equipment_stats,
        'recurring_failures': recurring_failures,
    })


def _is_overdue(req):
    targets = get_sla_targets(req['priority_level'])
    reported_at = req['reported_at']
    if isinstance(reported_at, str):
        reported_at = datetime.fromisoformat(reported_at)
    if hasattr(reported_at, 'tzinfo') and reported_at.tzinfo is None:
        reported_at = reported_at.replace(tzinfo=timezone.utc)
    elapsed = math.floor((datetime.now(timezone.utc) - reported_at).total_seconds() / 60)
    if req['status'] in ('open', 'queued'):
        return elapsed > targets['response']
    return elapsed > targets['resolution']


# ============================================================
# MONTHLY DOWNTIME ANALYSIS (Real Database Data, Split Across Months)
# ============================================================

@app.route('/api/analytics/monthly-downtime', methods=['GET'])
@all_roles
def get_monthly_downtime_analysis():
    """
    Monthly Downtime Analysis:
    - Calculates month vs total downtime hours using real database records.
    - Handles ongoing breakdowns by calculating live elapsed time up to current moment.
    - Accurately splits cross-month breakdowns across calendar month boundaries.
    - Supports machine filtering (equipment_id) and custom date ranges (from/to dates).
    """
    equipment_id = request.args.get('equipment')
    from_date_str = request.args.get('from')
    to_date_str = request.args.get('to')
    months_limit = int(request.args.get('months', 6))

    now = datetime.now()

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            # Query downtime logs with equipment info
            query = """
                SELECT dl.id, dl.equipment_id, dl.start_time, dl.end_time,
                       dl.minutes, dl.planned, dl.estimated_loss, dl.status,
                       e.name AS equipment_name, e.code AS equipment_code,
                       e.cost_per_hour
                FROM downtime_logs dl
                JOIN equipment e ON e.id = dl.equipment_id
                WHERE 1=1
            """
            params = []
            if equipment_id:
                query += " AND dl.equipment_id = %s"
                params.append(equipment_id)

            cursor.execute(query, params)
            logs = cursor.fetchall()
    finally:
        conn.close()

    # Determine date range boundaries
    start_bound = None
    end_bound = None

    if from_date_str:
        try:
            start_bound = datetime.fromisoformat(from_date_str.replace('Z', ''))
        except Exception:
            pass

    if to_date_str:
        try:
            end_bound = datetime.fromisoformat(to_date_str.replace('Z', ''))
            if len(to_date_str) <= 10:  # Date only, include full day
                end_bound = end_bound.replace(hour=23, minute=59, second=59)
        except Exception:
            pass

    # If no custom range provided, default to past `months_limit` months up to current month
    if not start_bound:
        # e.g., for 6 months, go back 5 full months to 1st of that month
        y = now.year
        m = now.month - (months_limit - 1)
        while m < 1:
            m += 12
            y -= 1
        start_bound = datetime(y, m, 1, 0, 0, 0)

    if not end_bound:
        end_bound = now

    # Helper: generate list of month keys (YYYY-MM) in the range
    month_data = {}
    cur_y = start_bound.year
    cur_m = start_bound.month
    end_y = end_bound.year
    end_m = end_bound.month

    while (cur_y < end_y) or (cur_y == end_y and cur_m <= end_m):
        m_key = f"{cur_y}-{cur_m:02d}"
        m_dt = datetime(cur_y, cur_m, 1)
        month_data[m_key] = {
            'month_key': m_key,
            'year': cur_y,
            'month': cur_m,
            'month_label': m_dt.strftime('%B %Y'),
            'short_label': m_dt.strftime('%b \'%y'),
            'total_seconds': 0.0,
            'planned_seconds': 0.0,
            'unplanned_seconds': 0.0,
            'estimated_loss': 0.0,
            'event_ids': set(),
            'has_ongoing': False,
            'machine_breakdown': {},  # eq_code -> { seconds, name }
        }
        if cur_m == 12:
            cur_m = 1
            cur_y += 1
        else:
            cur_m += 1

    ongoing_count = 0
    total_distinct_events = set()

    for l in logs:
        s_dt = l['start_time']
        if hasattr(s_dt, 'tzinfo') and s_dt.tzinfo is not None:
            s_dt = s_dt.replace(tzinfo=None)

        is_ongoing = not l.get('end_time') or l.get('status') == 'open'
        if is_ongoing:
            e_dt = now
            ongoing_count += 1
        else:
            e_dt = l['end_time']
            if hasattr(e_dt, 'tzinfo') and e_dt.tzinfo is not None:
                e_dt = e_dt.replace(tzinfo=None)

        if not s_dt or not e_dt or e_dt < s_dt:
            continue

        # Check if overlaps with the overall range [start_bound, end_bound]
        if e_dt < start_bound or s_dt > end_bound:
            continue

        total_distinct_events.add(l['id'])
        total_event_seconds = max(1.0, (e_dt - s_dt).total_seconds())
        cost_per_hr = float(l.get('cost_per_hour') or 0.0)
        is_planned = bool(l.get('planned'))
        eq_code = l.get('equipment_code') or 'UNKNOWN'
        eq_name = l.get('equipment_name') or 'Machine'

        # Split event interval across calendar months
        cur = s_dt
        while cur < e_dt:
            m_key = f"{cur.year}-{cur.month:02d}"

            # Next month start boundary
            if cur.month == 12:
                next_m_start = datetime(cur.year + 1, 1, 1, 0, 0, 0)
            else:
                next_m_start = datetime(cur.year, cur.month + 1, 1, 0, 0, 0)

            segment_end = min(e_dt, next_m_start)
            segment_seconds = (segment_end - cur).total_seconds()

            if segment_seconds > 0 and m_key in month_data:
                m_entry = month_data[m_key]
                m_entry['total_seconds'] += segment_seconds
                if is_planned:
                    m_entry['planned_seconds'] += segment_seconds
                else:
                    m_entry['unplanned_seconds'] += segment_seconds

                m_entry['event_ids'].add(l['id'])
                if is_ongoing and e_dt >= segment_end:
                    m_entry['has_ongoing'] = True

                # Calculate proportional estimated loss for this segment
                segment_hrs = segment_seconds / 3600.0
                if l.get('estimated_loss') is not None and float(l['estimated_loss']) > 0:
                    portion = (segment_seconds / total_event_seconds) * float(l['estimated_loss'])
                else:
                    portion = segment_hrs * cost_per_hr
                m_entry['estimated_loss'] += portion

                # Machine contribution
                if eq_code not in m_entry['machine_breakdown']:
                    m_entry['machine_breakdown'][eq_code] = {'name': eq_name, 'seconds': 0.0}
                m_entry['machine_breakdown'][eq_code]['seconds'] += segment_seconds

            cur = next_m_start

    # Format monthly results
    result_months = []
    total_all_hours = 0.0

    for m_key in sorted(month_data.keys()):
        m_entry = month_data[m_key]
        total_hrs = round(m_entry['total_seconds'] / 3600.0, 2)
        planned_hrs = round(m_entry['planned_seconds'] / 3600.0, 2)
        unplanned_hrs = round(m_entry['unplanned_seconds'] / 3600.0, 2)
        total_all_hours += total_hrs

        # Find top contributing machine
        top_mach = None
        top_mach_hrs = 0.0
        for code, info in m_entry['machine_breakdown'].items():
            hrs = round(info['seconds'] / 3600.0, 1)
            if hrs > top_mach_hrs:
                top_mach_hrs = hrs
                top_mach = f"{info['name']} ({code})"

        result_months.append({
            'month_key': m_key,
            'year': m_entry['year'],
            'month': m_entry['month'],
            'month_label': m_entry['month_label'],
            'short_label': m_entry['short_label'],
            'total_hours': total_hrs,
            'total_minutes': round(m_entry['total_seconds'] / 60.0),
            'planned_hours': planned_hrs,
            'unplanned_hours': unplanned_hrs,
            'event_count': len(m_entry['event_ids']),
            'estimated_loss': round(m_entry['estimated_loss'], 2),
            'top_machine': top_mach,
            'top_machine_hours': top_mach_hrs,
            'has_ongoing': m_entry['has_ongoing'],
        })

    # Summary metrics
    active_months_count = len(result_months)
    avg_hours = round(total_all_hours / max(1, active_months_count), 2)

    peak_month = None
    max_hrs = -1.0
    for rm in result_months:
        if rm['total_hours'] > max_hrs:
            max_hrs = rm['total_hours']
            peak_month = rm

    return success({
        'months': result_months,
        'summary': {
            'total_downtime_hours': round(total_all_hours, 2),
            'average_downtime_hours': avg_hours,
            'peak_month_label': peak_month['month_label'] if peak_month else 'N/A',
            'peak_month_hours': peak_month['total_hours'] if peak_month else 0.0,
            'active_ongoing_count': ongoing_count,
            'total_events': len(total_distinct_events),
            'months_count': active_months_count,
        }
    })


# ============================================================
# NOTIFICATIONS (FR-35)
# ============================================================

@app.route('/api/notifications', methods=['GET'])
@all_roles
def get_notifications():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT n.*, mr.request_code FROM notifications n
                   LEFT JOIN maintenance_requests mr ON mr.id = n.request_id
                   WHERE n.user_id = %s ORDER BY n.created_at DESC LIMIT 50""",
                (g.user_id,)
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    unread = sum(1 for n in rows if not n.get('read'))
    return success(rows, unread_count=unread)


@app.route('/api/notifications/<notif_id>/read', methods=['PATCH'])
@all_roles
def mark_notification_read(notif_id):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE notifications SET `read` = TRUE WHERE id = %s AND user_id = %s",
                (notif_id, g.user_id)
            )
            conn.commit()
    finally:
        conn.close()
    return success({'message': 'Marked as read'})


@app.route('/api/notifications/read-all', methods=['PATCH'])
@all_roles
def mark_all_read():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE notifications SET `read` = TRUE WHERE user_id = %s",
                (g.user_id,)
            )
            conn.commit()
    finally:
        conn.close()
    return success({'message': 'All marked as read'})


# ============================================================
# ADMIN ROUTES (FR-4)
# ============================================================

@app.route('/api/admin/users', methods=['GET'])
@require_roles('administrator')
def list_users():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, email, full_name, role, phone, active, created_at FROM users ORDER BY full_name"
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return success(rows)


@app.route('/api/admin/users', methods=['POST'])
@require_roles('administrator')
def create_user():
    data = request.get_json()
    required = ['email', 'password', 'full_name', 'role']
    for f in required:
        if not data.get(f):
            return error(f'{f} is required')
    if len(data['password']) < 8:
        return error('Password must be at least 8 characters')

    pw_hash = bcrypt.hashpw(data['password'].encode(), bcrypt.gensalt()).decode()
    user_id = str(uuid.uuid4())
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, email, password_hash, full_name, role, phone) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, data['email'].lower(), pw_hash, data['full_name'], data['role'], data.get('phone'))
            )
            conn.commit()
            cursor.execute("SELECT id, email, full_name, role, phone, active FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
    except Exception as e:
        conn.rollback()
        if 'Duplicate entry' in str(e):
            return error('Email already exists')
        return error(str(e))
    finally:
        conn.close()
    return success(user, 201)


@app.route('/api/admin/users/<user_id>', methods=['PATCH'])
@require_roles('administrator')
def update_user(user_id):
    data = request.get_json()
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            fields = []
            values = []
            for f in ['full_name', 'role', 'phone', 'active']:
                if f in data:
                    fields.append(f'{f} = %s')
                    values.append(data[f])
            if data.get('password'):
                if len(data['password']) < 8:
                    return error('Password must be at least 8 characters')
                pw_hash = bcrypt.hashpw(data['password'].encode(), bcrypt.gensalt()).decode()
                fields.append('password_hash = %s')
                values.append(pw_hash)
            if fields:
                values.append(user_id)
                cursor.execute(
                    f"UPDATE users SET {', '.join(fields)}, updated_at = NOW() WHERE id = %s",
                    values
                )
            conn.commit()
            cursor.execute("SELECT id, email, full_name, role, phone, active FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success(user)


@app.route('/api/skills', methods=['GET'])
@all_roles
def list_skills():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM skills ORDER BY name")
            rows = cursor.fetchall()
    finally:
        conn.close()
    return success(rows)


@app.route('/api/priority-rules', methods=['GET'])
@require_roles('supervisor', 'administrator')
def get_priority_rules():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM priority_rules WHERE active = TRUE LIMIT 1")
            row = cursor.fetchone()
    finally:
        conn.close()
    return success(row)


@app.route('/api/priority-rules', methods=['PATCH'])
@require_roles('administrator')
def update_priority_rules():
    data = request.get_json()
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM priority_rules WHERE active = TRUE LIMIT 1")
            row = cursor.fetchone()
            if not row:
                return error('Priority rules not found')
            fields = []
            values = []
            updatable = [
                'weight_safety', 'weight_production', 'weight_criticality', 'weight_downtime', 'weight_workaround',
                'threshold_critical', 'threshold_high', 'threshold_medium', 'threshold_low',
                'weight_production_stopped', 'weight_safety_risk', 'weight_waiting_hour',
                'waiting_cap', 'weight_days_overdue', 'p1_threshold', 'p2_threshold', 'p3_threshold',
                'p1_response_minutes', 'p1_resolution_minutes', 'p2_response_minutes', 'p2_resolution_minutes',
                'p3_response_minutes', 'p3_resolution_minutes', 'p4_response_minutes', 'p4_resolution_minutes'
            ]
            for f in updatable:
                if f in data:
                    fields.append(f'{f} = %s')
                    values.append(float(data[f]) if 'weight_' in f else int(data[f]))
            if fields:
                values.append(row['id'])
                cursor.execute(
                    f"UPDATE priority_rules SET {', '.join(fields)}, updated_at = NOW() WHERE id = %s",
                    values
                )
            conn.commit()
            cursor.execute("SELECT * FROM priority_rules WHERE id = %s", (row['id'],))
            updated = cursor.fetchone()

        # Immediately recalculate all active requests under the updated priority rules
        from scheduler import recalculate_priorities
        recalculate_priorities()
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()
    return success(updated)


@app.route('/api/admin/run-scheduler', methods=['POST'])
@require_roles('administrator')
def run_scheduler_now():
    """FR-36 demo: Run the preventive scheduler on demand."""
    created = run_preventive_scheduler()
    recalculate_priorities()
    return success({'created': created, 'message': f'{created} preventive request(s) created'})


@app.route('/api/admin/create-sample-breakdown', methods=['POST'])
@all_roles
def create_sample_breakdown():
    """Creates a sample high-priority breakdown request for demo testing."""
    data = request.get_json() or {}
    sample_type = data.get('sample_type', 'safety_critical')  # 'safety_critical' or 'production_halt'

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, code, name, criticality FROM equipment WHERE active = TRUE ORDER BY RAND() LIMIT 1")
            eq = cursor.fetchone()
            if not eq:
                return error('No active equipment found')

            if sample_type == 'safety_critical':
                s_rating = 5
                p_rating = 4
                w_rating = 5
                safety_flag = True
                desc = f"Emergency safety hazard detected on {eq['name']}: high temperature & electrical spark risk!"
                prob_type = 'electrical'
            else:
                s_rating = 2
                p_rating = 5
                w_rating = 5
                safety_flag = False
                desc = f"Critical line stoppage on {eq['name']}: main drive shaft failure!"
                prob_type = 'mechanical'

            from priority_engine import calculate_smart_priority
            priority = calculate_smart_priority(
                safety_risk_rating=s_rating,
                production_impact_rating=p_rating,
                machine_criticality=eq['criticality'],
                downtime_minutes=45.0,
                workaround_rating=w_rating,
                safety_hazard_flag=safety_flag
            )

            req_id = str(uuid.uuid4())
            req_code = next_request_code(cursor)
            dt_start = datetime.now(timezone.utc) - timedelta(minutes=45)

            cursor.execute(
                """INSERT INTO maintenance_requests (
                     id, request_code, type, source, equipment_id,
                     problem_type, urgency, safety_risk_rating, production_impact_rating,
                     workaround_rating, safety_hazard_flag, safety_review_notes,
                     production_stopped, safety_risk, description, reported_by, reported_at,
                     actual_breakdown_time, status, priority_score, priority_level, score_breakdown
                   ) VALUES (%s, %s, 'breakdown', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, 'open', %s, %s, %s)""",
                (
                    req_id, req_code, g.user_role, eq['id'],
                    prob_type, 4, s_rating, p_rating, w_rating, safety_flag,
                    'Sample industrial risk assessment generated' if safety_flag else None,
                    True, safety_flag, desc, g.user_id,
                    dt_start.strftime('%Y-%m-%d %H:%M:%S'),
                    priority['score'], priority['level'], json.dumps(priority['breakdown'])
                )
            )

            cursor.execute("UPDATE equipment SET status = 'down', updated_at = NOW() WHERE id = %s", (eq['id'],))
            cursor.execute(
                "INSERT INTO downtime_logs (id, equipment_id, request_id, start_time, planned, status) VALUES (%s, %s, %s, %s, FALSE, 'open')",
                (str(uuid.uuid4()), eq['id'], req_id, dt_start.strftime('%Y-%m-%d %H:%M:%S'))
            )
            log_status_change(cursor, req_id, None, 'open', g.user_id, g.user_name, 'Sample breakdown created')
            conn.commit()

        # Try auto-assign
        assign_request(req_id, g.user_id)
        return success({'message': f'Sample request {req_code} created for {eq["name"]}', 'request_id': req_id})
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route('/api/health', methods=['GET'])
def health():
    try:
        conn = get_db()
        conn.ping()
        conn.close()
        return jsonify({'status': 'ok', 'db': 'connected'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'db': str(e)}), 500


# ============================================================
# STARTUP
# ============================================================

if __name__ == '__main__':
    start_scheduler()
    use_prod = '--prod' in sys.argv or '--production' in sys.argv or os.getenv('USE_WAITRESS', 'false').lower() == 'true'
    
    if use_prod:
        from waitress import serve
        print("Starting production WSGI server (Waitress) on http://0.0.0.0:5000...")
        serve(app, host='0.0.0.0', port=5000, threads=6)
    else:
        print("\n" + "=" * 60)
        print(" SMART MAINTENANCE TRACKER BACKEND READY")
        print(" Listening on: http://127.0.0.1:5000 (Local)")
        print(" Note: The warning below is standard Flask development output.")
        print(" For production WSGI without warnings, run: python app.py --prod")
        print("=" * 60 + "\n")
        app.run(
            host='0.0.0.0',
            port=5000,
            debug=os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
        )

