"""
Smart Downtime-Aware Priority Engine

Implements the transparent, rule-based 5-factor priority algorithm:
1. Safety Risk (30%)
2. Production Impact (25%)
3. Machine Criticality (20%)
4. Downtime Urgency (15%)
5. Lack of Workaround (10%)

Maximum Score: 100.
Score Formula:
    Score = (Safety / 5 * 30) + (Production / 5 * 25) + (Criticality / 5 * 20) +
            (Urgency / 5 * 15) + (LackOfWorkaround / 5 * 10)

Thresholds (Configurable):
    80 - 100: Critical
    60 - 79:  High
    35 - 59:  Medium
    20 - 34:  Low

Downtime Urgency Rating based on elapsed machine downtime from saved timestamps:
    < 30 min:               Rating 1
    30 to < 60 min:         Rating 2
    60 to 120 min:          Rating 3
    > 120 to 240 min:       Rating 4
    > 240 min:              Rating 5
"""
import math
from datetime import datetime, timezone


def get_downtime_urgency_rating(downtime_minutes: float) -> int:
    """
    Map elapsed machine downtime minutes into urgency rating (1-5).
    """
    if downtime_minutes is None or downtime_minutes < 0:
        return 1
    if downtime_minutes < 30:
        return 1
    elif downtime_minutes < 60:
        return 2
    elif downtime_minutes <= 120:
        return 3
    elif downtime_minutes <= 240:
        return 4
    else:
        return 5


def calculate_downtime_minutes(start_timestamp, end_timestamp=None) -> float:
    """
    Calculate downtime duration in minutes from saved timestamps.
    Handles datetime objects or ISO format strings.
    """
    if not start_timestamp:
        return 0.0

    if isinstance(start_timestamp, str):
        try:
            start_timestamp = datetime.fromisoformat(start_timestamp.replace('Z', '+00:00'))
        except Exception:
            return 0.0

    if start_timestamp.tzinfo is None:
        start_timestamp = start_timestamp.replace(tzinfo=timezone.utc)

    if end_timestamp:
        if isinstance(end_timestamp, str):
            try:
                end_timestamp = datetime.fromisoformat(end_timestamp.replace('Z', '+00:00'))
            except Exception:
                end_timestamp = datetime.now(timezone.utc)
        if end_timestamp.tzinfo is None:
            end_timestamp = end_timestamp.replace(tzinfo=timezone.utc)
        current_time = end_timestamp
    else:
        current_time = datetime.now(timezone.utc)

    diff = current_time - start_timestamp
    return max(0.0, diff.total_seconds() / 60.0)


def calculate_smart_priority(
    safety_risk_rating: int = 1,
    production_impact_rating: int = 1,
    machine_criticality: int = 1,
    downtime_minutes: float = 0.0,
    workaround_rating: int = None,
    safety_hazard_flag: bool = False,
    urgency_rating: int = None,
    rules: dict = None
) -> dict:
    """
    Percentage-based Maintenance Priority Algorithm.
    Calculates total priority score out of 100% based on:
    1. Safety Risk (30%)
    2. Production Impact (25%)
    3. Machine Criticality (20%)
    4. Urgency (15% if 5-factor, or 25% if 4-factor)
    5. Lack of Workaround (10% if 5-factor mode)

    Normalizes each factor to a percentage (0-100%), multiplies by weight,
    and classifies requests into:
    - Critical (80-100%)
    - High (60-79%)
    - Medium (35-59%)
    - Low (0-34%)

    Safety-critical faults (safety rating >= 4 or safety_hazard_flag) trigger
    immediate mandatory safety escalation.
    """
    # Clamp ratings to 1..5 scale
    s = max(1, min(5, int(safety_risk_rating or 1)))
    p = max(1, min(5, int(production_impact_rating or 1)))
    c = max(1, min(5, int(machine_criticality or 1)))
    downtime_minutes = float(downtime_minutes if downtime_minutes is not None else 0.0)

    # Determine urgency rating from reported urgency and elapsed downtime
    downtime_rating = get_downtime_urgency_rating(downtime_minutes)
    if urgency_rating is not None:
        u = max(1, min(5, int(urgency_rating)))
        u = max(u, downtime_rating)
    else:
        u = downtime_rating
    d = u  # alias for backwards compatibility

    # Check if 5-factor (with workaround) or 4-factor mode
    has_workaround = workaround_rating is not None
    w = max(1, min(5, int(workaround_rating))) if has_workaround else 1

    # Configurable weights (sum = 100%)
    w_safety = float(rules.get('weight_safety', 30.0) if rules else 30.0)
    w_production = float(rules.get('weight_production', 25.0) if rules else 25.0)
    w_criticality = float(rules.get('weight_criticality', 20.0) if rules else 20.0)

    if has_workaround:
        w_urgency = float(rules.get('weight_downtime', 15.0) if rules else 15.0)
        w_workaround = float(rules.get('weight_workaround', 10.0) if rules else 10.0)
    else:
        w_urgency = float(rules.get('weight_downtime', 25.0) if rules else 25.0)
        w_workaround = 0.0

    # Configurable thresholds
    t_critical = int(rules.get('threshold_critical', 80) if rules else 80)
    t_high = int(rules.get('threshold_high', 60) if rules else 60)
    t_medium = int(rules.get('threshold_medium', 35) if rules else 35)
    t_low = int(rules.get('threshold_low', 20) if rules else 20)

    # Normalize each factor to a percentage (0 - 100%)
    pct_safety = round((s / 5.0) * 100.0, 1)
    pct_production = round((p / 5.0) * 100.0, 1)
    pct_criticality = round((c / 5.0) * 100.0, 1)
    pct_urgency = round((u / 5.0) * 100.0, 1)
    pct_workaround = round((w / 5.0) * 100.0, 1) if has_workaround else 0.0

    # Weighted point contributions out of 100%
    pts_safety = round((pct_safety / 100.0) * w_safety, 2)
    pts_production = round((pct_production / 100.0) * w_production, 2)
    pts_criticality = round((pct_criticality / 100.0) * w_criticality, 2)
    pts_urgency = round((pct_urgency / 100.0) * w_urgency, 2)
    pts_downtime = pts_urgency  # alias
    pts_workaround = round((pct_workaround / 100.0) * w_workaround, 2) if has_workaround else 0.0

    total_score = round(pts_safety + pts_production + pts_criticality + pts_urgency + pts_workaround, 1)
    total_score = min(100.0, max(0.0, total_score))

    # Priority level classification
    if total_score >= t_critical:
        level = 'Critical'
    elif total_score >= t_high:
        level = 'High'
    elif total_score >= t_medium:
        level = 'Medium'
    else:
        level = 'Low'

    # Safety Escalation Detection:
    # Trigger if safety rating >= 4 OR safety hazard explicitly flagged
    is_safety_escalated = bool(safety_hazard_flag or s >= 4)

    # Generate transparent explainable reasons
    reasons = [
        f"Total Priority Score: {total_score}% out of 100% (Classified as {level} Priority)."
    ]

    if s >= 4:
        reasons.append(f"Severe safety hazard reported (Safety Rating {s}/5: {pct_safety}% normalized, contributing {pts_safety}/{w_safety} pts). Immediate safety precautions required.")
    elif s == 3:
        reasons.append(f"Moderate safety concern present (Safety Rating {s}/5: {pct_safety}% normalized, contributing {pts_safety}/{w_safety} pts).")
    else:
        reasons.append(f"Standard industrial safety risk (Safety Rating {s}/5: {pct_safety}% normalized, contributing {pts_safety}/{w_safety} pts).")

    if p >= 4:
        reasons.append(f"High production impact affecting throughput (Production Impact {p}/5: {pct_production}% normalized, contributing {pts_production}/{w_production} pts).")
    elif p == 3:
        reasons.append(f"Moderate production slowdown (Production Impact {p}/5: {pct_production}% normalized, contributing {pts_production}/{w_production} pts).")
    else:
        reasons.append(f"Minor production impact (Production Impact {p}/5: {pct_production}% normalized, contributing {pts_production}/{w_production} pts).")

    if c >= 4:
        reasons.append(f"Critical production machinery (Machine Criticality {c}/5: {pct_criticality}% normalized, contributing {pts_criticality}/{w_criticality} pts).")
    elif c == 3:
        reasons.append(f"Standard production line machine (Machine Criticality {c}/5: {pct_criticality}% normalized, contributing {pts_criticality}/{w_criticality} pts).")
    else:
        reasons.append(f"Non-critical auxiliary equipment (Machine Criticality {c}/5: {pct_criticality}% normalized, contributing {pts_criticality}/{w_criticality} pts).")

    if u >= 4:
        reasons.append(f"Extended downtime of {int(downtime_minutes)} minutes (Urgency Rating {u}/5: {pct_urgency}% normalized, contributing {pts_urgency}/{w_urgency} pts).")
    elif u >= 2:
        reasons.append(f"Machine downtime reached {int(downtime_minutes)} minutes (Urgency Rating {u}/5: {pct_urgency}% normalized, contributing {pts_urgency}/{w_urgency} pts).")
    else:
        reasons.append(f"Recent breakdown (< 30 min downtime: Urgency Rating {u}/5, contributing {pts_urgency}/{w_urgency} pts).")

    if has_workaround:
        if w >= 4:
            reasons.append(f"No effective workaround or alternative machine available (Lack of Workaround {w}/5: {pct_workaround}% normalized, contributing {pts_workaround}/{w_workaround} pts).")
        elif w == 3:
            reasons.append(f"Partial or inefficient workaround available (Rating {w}/5: {pct_workaround}% normalized, contributing {pts_workaround}/{w_workaround} pts).")
        else:
            reasons.append(f"Feasible bypass/backup machine currently available (Rating {w}/5: contributing {pts_workaround}/{w_workaround} pts).")

    if is_safety_escalated:
        safety_status_message = "MANDATORY SAFETY ESCALATION: Machine must undergo formal industrial safety assessment before work begins."
    else:
        safety_status_message = "Standard industrial maintenance safety precautions apply."

    breakdown = {
        'total_score': total_score,
        'total_percentage': total_score,
        'level': level,
        'is_safety_escalated': is_safety_escalated,
        'factors': {
            'safety_risk': {
                'rating': s,
                'max_rating': 5,
                'percentage': pct_safety,
                'weight': w_safety,
                'points': pts_safety,
                'description': 'Personal or environmental safety hazard severity'
            },
            'production_impact': {
                'rating': p,
                'max_rating': 5,
                'percentage': pct_production,
                'weight': w_production,
                'points': pts_production,
                'description': 'Direct disruption to factory production line'
            },
            'machine_criticality': {
                'rating': c,
                'max_rating': 5,
                'percentage': pct_criticality,
                'weight': w_criticality,
                'points': pts_criticality,
                'description': 'Equipment importance in the manufacturing flow'
            },
            'urgency': {
                'rating': u,
                'max_rating': 5,
                'percentage': pct_urgency,
                'weight': w_urgency,
                'points': pts_urgency,
                'downtime_minutes': round(downtime_minutes, 1),
                'description': 'Urgency escalation as equipment stays non-operational'
            },
            'downtime_urgency': {
                'rating': u,
                'max_rating': 5,
                'percentage': pct_urgency,
                'weight': w_urgency,
                'points': pts_urgency,
                'downtime_minutes': round(downtime_minutes, 1),
                'description': 'Urgency escalation as equipment stays non-operational'
            },
        },
        'reasons': reasons,
        'safety_warning': safety_status_message
    }

    if has_workaround:
        breakdown['factors']['lack_of_workaround'] = {
            'rating': w,
            'max_rating': 5,
            'percentage': pct_workaround,
            'weight': w_workaround,
            'points': pts_workaround,
            'description': 'Absence of secondary machines or production buffers'
        }

    # Recommended action advice based on score & safety
    if is_safety_escalated:
        recommended_action = "Trigger Safety Escalation & Dispatch Senior Qualified Specialist"
    elif level == 'Critical':
        recommended_action = "Immediate priority assignment & line supervisor alert"
    elif level == 'High':
        recommended_action = "Assign qualified technician within 60 minutes"
    elif level == 'Medium':
        recommended_action = "Schedule technician during current shift"
    else:
        recommended_action = "Queue for next routine maintenance window"

    return {
        'score': int(round(total_score)),
        'score_exact': total_score,
        'level': level,
        'priority_level': level,
        'is_safety_escalated': is_safety_escalated,
        'recommended_action': recommended_action,
        'breakdown': breakdown,
        'reasons': reasons,
        'safety_warning': safety_status_message
    }


def sort_requests_by_priority(requests_list):
    """
    Sorts maintenance requests according to documented tie-breaking rules:
    1. Required safety escalation
    2. Higher priority score
    3. Greater production impact
    4. Longer downtime
    5. Earlier request time
    """
    def sort_key(r):
        is_safety = bool(
            r.get('is_safety_escalated') or
            r.get('safety_hazard_flag') or
            (r.get('safety_risk_rating', 1) >= 4) or
            r.get('safety_risk')
        )
        score = float(r.get('priority_score', 0))
        prod_impact = int(r.get('production_impact_rating', 1) or 1)
        downtime_min = float(r.get('downtime_minutes', 0) or 0)
        
        rep = r.get('reported_at')
        if isinstance(rep, str):
            try:
                rep_ts = datetime.fromisoformat(rep.replace('Z', '+00:00')).timestamp()
            except Exception:
                rep_ts = 0.0
        elif isinstance(rep, datetime):
            rep_ts = rep.timestamp()
        else:
            rep_ts = 0.0

        # High values first for first 4 criteria; smaller timestamp (earlier time) first for 5th
        return (
            1 if is_safety else 0,
            score,
            prod_impact,
            downtime_min,
            -rep_ts
        )

    return sorted(requests_list, key=sort_key, reverse=True)


def calculate_priority_score(criticality, urgency, production_stopped, safety_risk,
                               waiting_hours=0, days_overdue=0, rules=None,
                               safety_risk_rating=None, production_impact_rating=None,
                               workaround_rating=None, actual_downtime_minutes=None,
                               safety_hazard_flag=False):
    """
    Unified entry point for calculating priority.
    Seamlessly supports both the new 5-factor smart downtime algorithm
    and provides backwards-compatible conversion from legacy parameters.
    """
    # If 5-factor explicit ratings are provided, use the new smart algorithm
    if safety_risk_rating is not None or production_impact_rating is not None or workaround_rating is not None:
        s_rate = int(safety_risk_rating if safety_risk_rating is not None else (4 if safety_risk else 1))
        p_rate = int(production_impact_rating if production_impact_rating is not None else (4 if production_stopped else 2))
        w_rate = int(workaround_rating if workaround_rating is not None else 3)
        c_rate = int(criticality or 3)
        dt_min = float(actual_downtime_minutes if actual_downtime_minutes is not None else (waiting_hours * 60))

        return calculate_smart_priority(
            safety_risk_rating=s_rate,
            production_impact_rating=p_rate,
            machine_criticality=c_rate,
            downtime_minutes=dt_min,
            workaround_rating=w_rate,
            safety_hazard_flag=safety_hazard_flag or bool(safety_risk),
            rules=rules
        )

    # Backwards compatibility fallback if only legacy arguments are supplied
    s_rate = 5 if safety_risk else 1
    p_rate = 5 if production_stopped else (urgency if urgency else 2)
    w_rate = 4 if production_stopped else 2
    c_rate = int(criticality or 3)
    dt_min = float(waiting_hours * 60)

    res = calculate_smart_priority(
        safety_risk_rating=s_rate,
        production_impact_rating=p_rate,
        machine_criticality=c_rate,
        downtime_minutes=dt_min,
        workaround_rating=w_rate,
        safety_hazard_flag=bool(safety_risk),
        rules=rules
    )

    # Map level to P1..P4 for legacy consumers if needed
    level_map = {'Critical': 'P1', 'High': 'P2', 'Medium': 'P3', 'Low': 'P4'}
    res['legacy_level'] = level_map.get(res['level'], 'P4')
    return res


def get_waiting_hours(reported_at):
    """Get number of hours a request has been waiting."""
    if not reported_at:
        return 0
    if isinstance(reported_at, str):
        try:
            reported_at = datetime.fromisoformat(reported_at.replace('Z', '+00:00'))
        except Exception:
            return 0
    now = datetime.now(timezone.utc)
    if reported_at.tzinfo is None:
        reported_at = reported_at.replace(tzinfo=timezone.utc)
    diff = now - reported_at
    return math.floor(max(0, diff.total_seconds()) / 3600)


def get_days_overdue(next_due_date):
    """Get number of days a preventive task is overdue."""
    if not next_due_date:
        return 0
    if isinstance(next_due_date, str):
        try:
            next_due_date = datetime.fromisoformat(next_due_date).date()
        except Exception:
            return 0
    today = datetime.now(timezone.utc).date()
    if hasattr(next_due_date, 'date'):
        next_due_date = next_due_date.date()
    diff = (today - next_due_date).days
    return max(0, diff)


SLA_TARGETS = {
    'Critical': {'response': 15, 'resolution': 240},
    'High': {'response': 60, 'resolution': 480},
    'Medium': {'response': 480, 'resolution': 1440},
    'Low': {'response': 2880, 'resolution': 4320},
    'P1': {'response': 15, 'resolution': 240},
    'P2': {'response': 60, 'resolution': 480},
    'P3': {'response': 480, 'resolution': 1440},
    'P4': {'response': 2880, 'resolution': 4320},
}


def get_sla_targets(level, rules=None):
    """Get SLA response and resolution targets in minutes."""
    normalized_level = level
    if level == 'P1': normalized_level = 'Critical'
    elif level == 'P2': normalized_level = 'High'
    elif level == 'P3': normalized_level = 'Medium'
    elif level == 'P4': normalized_level = 'Low'

    if rules:
        return {
            'Critical': {'response': rules.get('p1_response_minutes', 15), 'resolution': rules.get('p1_resolution_minutes', 240)},
            'High': {'response': rules.get('p2_response_minutes', 60), 'resolution': rules.get('p2_resolution_minutes', 480)},
            'Medium': {'response': rules.get('p3_response_minutes', 480), 'resolution': rules.get('p3_resolution_minutes', 1440)},
            'Low': {'response': rules.get('p4_response_minutes', 2880), 'resolution': rules.get('p4_resolution_minutes', 4320)},
        }.get(normalized_level, SLA_TARGETS.get(level, {'response': 2880, 'resolution': 4320}))

    return SLA_TARGETS.get(level, SLA_TARGETS.get(normalized_level, {'response': 2880, 'resolution': 4320}))
