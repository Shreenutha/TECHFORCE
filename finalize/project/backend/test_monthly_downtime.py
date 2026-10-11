import urllib.request
import json

def test():
    # Login as supervisor
    login_req = urllib.request.Request(
        'http://localhost:5000/api/auth/login', 
        data=json.dumps({'email': 'supervisor@demo.com', 'password': 'Sup@123'}).encode(), 
        headers={'Content-Type': 'application/json'}
    )
    token = json.loads(urllib.request.urlopen(login_req).read())['data']['token']
    auth_header = {'Authorization': 'Bearer ' + token}

    # 1. Test default 6 months
    req = urllib.request.Request('http://localhost:5000/api/analytics/monthly-downtime', headers=auth_header)
    res = json.loads(urllib.request.urlopen(req).read())['data']
    print('Test 1 (6 months): OK, count =', len(res['months']), 'total_hours =', res['summary']['total_downtime_hours'])

    # 2. Test 3 months
    req = urllib.request.Request('http://localhost:5000/api/analytics/monthly-downtime?months=3', headers=auth_header)
    res = json.loads(urllib.request.urlopen(req).read())['data']
    print('Test 2 (3 months): OK, count =', len(res['months']), 'total_hours =', res['summary']['total_downtime_hours'])

    # 3. Test equipment filter
    eq_req = urllib.request.Request('http://localhost:5000/api/equipment', headers=auth_header)
    eq_list = json.loads(urllib.request.urlopen(eq_req).read())['data']
    if eq_list:
        target_eq = eq_list[0]
        eq_id = target_eq['id']
        req = urllib.request.Request(f'http://localhost:5000/api/analytics/monthly-downtime?equipment={eq_id}', headers=auth_header)
        res = json.loads(urllib.request.urlopen(req).read())['data']
        print(f"Test 3 (Equipment {target_eq['code']}): OK, count =", len(res['months']), 'total_hours =', res['summary']['total_downtime_hours'])

    # 4. Test custom date range
    req = urllib.request.Request('http://localhost:5000/api/analytics/monthly-downtime?from=2026-07-01&to=2026-09-30', headers=auth_header)
    res = json.loads(urllib.request.urlopen(req).read())['data']
    print('Test 4 (Custom range Jul-Sep): OK, count =', len(res['months']), 'months =', [m['short_label'] for m in res['months']])

if __name__ == '__main__':
    test()
