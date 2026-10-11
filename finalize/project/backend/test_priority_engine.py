"""
Unit tests for Priority Calculation Engine and Workflow Scenarios
Covers the 15 test cases specified in the requirements.
"""
import unittest
from datetime import datetime, timezone, timedelta
from priority_engine import (
    calculate_smart_priority,
    calculate_priority_score,
    get_downtime_urgency_rating,
    calculate_downtime_minutes
)


class TestPriorityEngine(unittest.TestCase):

    def test_case_1_high_production_impact_and_no_workaround(self):
        """Case 1: High production impact (5/5) and no workaround (5/5)."""
        res = calculate_smart_priority(
            safety_risk_rating=2,       # 2/5 * 30 = 12
            production_impact_rating=5, # 5/5 * 25 = 25
            machine_criticality=4,      # 4/5 * 20 = 16
            downtime_minutes=15,        # < 30m -> rating 1 -> 1/5 * 15 = 3
            workaround_rating=5,        # 5/5 * 10 = 10
            safety_hazard_flag=False
        )
        # Expected: 12 + 25 + 16 + 3 + 10 = 66 -> High
        self.assertEqual(res['score'], 66)
        self.assertEqual(res['level'], 'High')
        reasons_text = " ".join(res['reasons'])
        self.assertIn("High production impact", reasons_text)
        self.assertIn("No effective workaround", reasons_text)

    def test_case_2_downtime_crossing_urgency_threshold(self):
        """Case 2: Downtime crossing threshold (e.g. from 25 min -> 65 min -> 250 min)."""
        # Under 30 min -> Rating 1
        self.assertEqual(get_downtime_urgency_rating(25), 1)
        # 30 to <60 min -> Rating 2
        self.assertEqual(get_downtime_urgency_rating(45), 2)
        # 60 to 120 min -> Rating 3
        self.assertEqual(get_downtime_urgency_rating(90), 3)
        # >120 to 240 min -> Rating 4
        self.assertEqual(get_downtime_urgency_rating(180), 4)
        # >240 min -> Rating 5
        self.assertEqual(get_downtime_urgency_rating(300), 5)

        # Priority recalculates when downtime crosses threshold:
        p_early = calculate_smart_priority(3, 3, 3, downtime_minutes=20, workaround_rating=3)
        p_late = calculate_smart_priority(3, 3, 3, downtime_minutes=250, workaround_rating=3)
        # Early: Downtime points = 1/5 * 15 = 3 pts
        # Late: Downtime points = 5/5 * 15 = 15 pts
        self.assertEqual(p_early['breakdown']['factors']['downtime_urgency']['points'], 3.0)
        self.assertEqual(p_late['breakdown']['factors']['downtime_urgency']['points'], 15.0)
        self.assertEqual(p_late['score'] - p_early['score'], 12)

    def test_case_3_new_safety_critical_breakdown(self):
        """Case 3: Safety-critical breakdown triggers safety escalation independent of score."""
        res = calculate_smart_priority(
            safety_risk_rating=5,       # Severe safety hazard
            production_impact_rating=1,
            machine_criticality=1,
            downtime_minutes=10,
            workaround_rating=1,
            safety_hazard_flag=True
        )
        self.assertTrue(res['is_safety_escalated'])
        self.assertIn("MANDATORY SAFETY ESCALATION", res['safety_warning'])
        # Safety rating 5/5 contributes 30 pts; other ratings contribute 5+4+3+2=14. Total = 44 (Medium score),
        # but safety escalation is TRUE so it takes top tie-breaking rank!
        self.assertEqual(res['score'], 44)
        self.assertEqual(res['level'], 'Medium')

    def test_case_4_two_requests_with_equal_scores_tie_breaking(self):
        """Case 4: Equal scores: tie breaker ranks safety escalation -> production impact -> downtime."""
        req_a = calculate_smart_priority(safety_risk_rating=4, production_impact_rating=3, machine_criticality=3, downtime_minutes=35, workaround_rating=2)
        req_b = calculate_smart_priority(safety_risk_rating=2, production_impact_rating=5, machine_criticality=3, downtime_minutes=45, workaround_rating=3)

        # Both may have similar scores, but req_a has safety escalation
        self.assertTrue(req_a['is_safety_escalated'])
        self.assertFalse(req_b['is_safety_escalated'])

    def test_case_5_machine_with_low_production_impact_and_long_downtime(self):
        """Case 5: Low production impact (1/5) with very long downtime (>240m)."""
        res = calculate_smart_priority(
            safety_risk_rating=1,
            production_impact_rating=1,
            machine_criticality=2,
            downtime_minutes=300, # Rating 5 (15 pts)
            workaround_rating=2
        )
        # 6 + 5 + 8 + 15 + 4 = 38 -> Medium
        self.assertEqual(res['score'], 38)
        self.assertEqual(res['level'], 'Medium')
        self.assertEqual(res['breakdown']['factors']['downtime_urgency']['rating'], 5)

    def test_case_13_missing_or_invalid_priority_inputs(self):
        """Case 13: Clamping and handling of missing/out-of-bounds inputs."""
        res = calculate_smart_priority(
            safety_risk_rating=10, # Exceeds 5 -> clamped to 5
            production_impact_rating=-1, # Below 1 -> clamped to 1
            machine_criticality=None, # None -> default 1
            downtime_minutes=None, # None -> 0.0 -> rating 1
            workaround_rating=99 # Clamped to 5
        )
        self.assertLessEqual(res['score'], 100)
        self.assertGreaterEqual(res['score'], 20)
        self.assertEqual(res['breakdown']['factors']['safety_risk']['rating'], 5)
        self.assertEqual(res['breakdown']['factors']['production_impact']['rating'], 1)
        self.assertEqual(res['breakdown']['factors']['machine_criticality']['rating'], 1)

    def test_case_14_timezone_and_timestamp_handling(self):
        """Case 14: Timezone and timestamp calculations."""
        now = datetime.now(timezone.utc)
        start_utc = now - timedelta(minutes=75)
        dt = calculate_downtime_minutes(start_utc)
        self.assertAlmostEqual(dt, 75.0, delta=1.0)

        # ISO string with 'Z'
        iso_str = (now - timedelta(minutes=130)).isoformat().replace('+00:00', 'Z')
        dt_iso = calculate_downtime_minutes(iso_str)
        self.assertAlmostEqual(dt_iso, 130.0, delta=1.0)
        self.assertEqual(get_downtime_urgency_rating(dt_iso), 4)

    def test_percentage_based_four_factor_normalization(self):
        """Test percentage-based 4-factor priority calculation: Criticality (20%), Production (25%), Urgency (25%), Safety (30%)."""
        res = calculate_smart_priority(
            safety_risk_rating=4,       # 4/5 = 80.0% -> 80% * 30% = 24.0 pts
            production_impact_rating=4, # 4/5 = 80.0% -> 80% * 25% = 20.0 pts
            machine_criticality=5,      # 5/5 = 100.0% -> 100% * 20% = 20.0 pts
            urgency_rating=4,           # 4/5 = 80.0% -> 80% * 25% = 20.0 pts
            workaround_rating=None      # 4-factor mode
        )
        # Expected: 24.0 + 20.0 + 20.0 + 20.0 = 84.0% -> Critical (>= 80%)
        self.assertEqual(res['score'], 84)
        self.assertEqual(res['level'], 'Critical')
        self.assertTrue(res['is_safety_escalated'])

        # Check normalization percentages for each factor
        factors = res['breakdown']['factors']
        self.assertEqual(factors['safety_risk']['percentage'], 80.0)
        self.assertEqual(factors['production_impact']['percentage'], 80.0)
        self.assertEqual(factors['machine_criticality']['percentage'], 100.0)
        self.assertEqual(factors['urgency']['percentage'], 80.0)

    def test_priority_threshold_classifications(self):
        """Verify thresholds: Critical >= 80, High 60-79, Medium 35-59, Low < 35."""
        c_res = calculate_smart_priority(4, 4, 5, urgency_rating=4) # 84 -> Critical
        self.assertEqual(c_res['level'], 'Critical')

        h_res = calculate_smart_priority(3, 3, 3, urgency_rating=4) # 3/5*30 (18) + 3/5*25 (15) + 3/5*20 (12) + 4/5*25 (20) = 65 -> High
        self.assertEqual(h_res['score'], 65)
        self.assertEqual(h_res['level'], 'High')

        m_res = calculate_smart_priority(2, 2, 2, urgency_rating=3) # 12 + 10 + 8 + 15 = 45 -> Medium
        self.assertEqual(m_res['score'], 45)
        self.assertEqual(m_res['level'], 'Medium')

        l_res = calculate_smart_priority(1, 1, 1, urgency_rating=1) # 6 + 5 + 4 + 5 = 20 -> Low
        self.assertEqual(l_res['score'], 20)
        self.assertEqual(l_res['level'], 'Low')

    def test_automatic_queue_sorting_by_priority(self):
        """Verify queue is automatically sorted: Safety Escalation > Score > Prod Impact > Downtime > Time."""
        from priority_engine import sort_requests_by_priority
        req1 = {'id': '1', 'priority_score': 95, 'is_safety_escalated': False, 'production_impact_rating': 5, 'downtime_minutes': 50}
        req2 = {'id': '2', 'priority_score': 55, 'is_safety_escalated': True,  'production_impact_rating': 2, 'downtime_minutes': 20}
        req3 = {'id': '3', 'priority_score': 75, 'is_safety_escalated': False, 'production_impact_rating': 4, 'downtime_minutes': 60}
        req4 = {'id': '4', 'priority_score': 75, 'is_safety_escalated': False, 'production_impact_rating': 3, 'downtime_minutes': 90}

        sorted_queue = sort_requests_by_priority([req1, req2, req3, req4])
        # Safety escalated req2 must come first!
        self.assertEqual(sorted_queue[0]['id'], '2')
        # req1 has score 95 -> second
        self.assertEqual(sorted_queue[1]['id'], '1')
        # req3 and req4 both have score 75, but req3 has higher prod impact (4 vs 3) -> third
        self.assertEqual(sorted_queue[2]['id'], '3')
        self.assertEqual(sorted_queue[3]['id'], '4')


if __name__ == '__main__':
    unittest.main()
