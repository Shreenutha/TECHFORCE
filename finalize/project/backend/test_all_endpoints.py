import sys, traceback, json
sys.path.insert(0, '.')
from app import app

def test_api():
    with app.test_client() as c:
        print("--- Testing Logins ---")
        users = [
            ('admin@demo.com', 'Admin@123', 'admin'),
            ('supervisor@demo.com', 'Sup@123', 'supervisor'),
            ('operator@demo.com', 'Op@123', 'operator'),
            ('tech1@demo.com', 'Tech@123', 'technician'),
        ]
        tokens = {}
        for email, pw, role in users:
            resp = c.post('/api/auth/login', json={'email': email, 'password': pw})
            data = resp.get_json()
            if resp.status_code == 200:
                print(f"[OK] Login {role} ({email}): token received")
                tokens[role] = data['data']['token']
            else:
                print(f"[FAIL] Login {role} ({email}): {resp.status_code} {data}")

        sup_token = tokens.get('supervisor')
        op_token = tokens.get('operator')

        print("\n--- Testing Endpoints with Supervisor Token ---")
        endpoints = [
            ('/api/technicians', 'GET'),
            ('/api/equipment', 'GET'),
            ('/api/schedules', 'GET'),
            ('/api/requests', 'GET'),
            ('/api/downtime', 'GET'),
            ('/api/analytics/summary', 'GET'),
            ('/api/notifications', 'GET'),
        ]
        for url, method in endpoints:
            resp = c.open(url, method=method, headers={'Authorization': f'Bearer {sup_token}'})
            data = resp.get_json()
            status = resp.status_code
            if status == 200:
                print(f"[OK] {method} {url}: 200 OK (count={len(data.get('data', [])) if isinstance(data.get('data'), list) else 'dict'})")
            else:
                print(f"[FAIL] {method} {url}: {status} {data}")

        print("\n--- Testing /api/technicians with Operator Token ---")
        resp = c.get('/api/technicians', headers={'Authorization': f'Bearer {op_token}'})
        data = resp.get_json()
        print(f"[{'OK' if resp.status_code == 200 else 'FAIL'}] GET /api/technicians (as operator): {resp.status_code}")

        print("\n--- Testing Request Details and Recommendation ---")
        # Fetch first request ID
        reqs_resp = c.get('/api/requests', headers={'Authorization': f'Bearer {sup_token}'})
        reqs = reqs_resp.get_json().get('data', [])
        if reqs:
            first_req_id = reqs[0]['id']
            # Test GET /api/requests/<id>
            r_resp = c.get(f'/api/requests/{first_req_id}', headers={'Authorization': f'Bearer {sup_token}'})
            print(f"[{'OK' if r_resp.status_code == 200 else 'FAIL'}] GET /api/requests/{first_req_id}: {r_resp.status_code}")
            # Test GET /api/requests/<id>/recommendation
            rec_resp = c.get(f'/api/requests/{first_req_id}/recommendation', headers={'Authorization': f'Bearer {sup_token}'})
            print(f"[{'OK' if rec_resp.status_code == 200 else 'FAIL'}] GET /api/requests/{first_req_id}/recommendation: {rec_resp.status_code}")

if __name__ == '__main__':
    test_api()
