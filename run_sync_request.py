import urllib.request
import urllib.error
import http.cookiejar
import json

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

login_data = json.dumps({'username': 'admin', 'password': 'admin123'}).encode('utf-8')
login_req = urllib.request.Request('http://127.0.0.1:5000/login', data=login_data, headers={'Content-Type': 'application/json'})
login_resp = opener.open(login_req, timeout=10)
print('LOGIN', login_resp.getcode(), login_resp.read().decode('utf-8'))

sync_req = urllib.request.Request('http://127.0.0.1:5000/api/sync', data=b'{}', headers={'Content-Type': 'application/json'})
try:
    sync_resp = opener.open(sync_req, timeout=60)
    print('SYNC', sync_resp.getcode(), sync_resp.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    body = e.read().decode('utf-8', errors='replace')
    print('SYNC HTTP ERROR', e.code, body)
except Exception as e:
    print('SYNC ERROR', type(e).__name__, e)
