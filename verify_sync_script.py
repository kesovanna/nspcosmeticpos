import subprocess
import time
import urllib.request
import urllib.error
import http.cookiejar
import json
import os

cwd = os.path.abspath(os.path.dirname(__file__))
python_exe = os.path.join(cwd, '..', '.venv', 'Scripts', 'python.exe')
log_path = os.path.join(cwd, 'verify_sync_output.log')

proc = subprocess.Popen(
    [python_exe, 'main.py'],
    cwd=cwd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)

try:
    start = time.time()
    endpoint = 'http://127.0.0.1:5000/'
    while time.time() - start < 30:
        try:
            urllib.request.urlopen(endpoint, timeout=5)
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError('Server did not start within 30 seconds')

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [('Content-Type', 'application/json')]

    try:
        opener.open('http://127.0.0.1:5000/setup-admin', timeout=10)
    except Exception:
        pass

    # GET request to fetch CSRF token
    get_login_req = urllib.request.Request('http://127.0.0.1:5000/login')
    get_login_resp = opener.open(get_login_req, timeout=10)
    html = get_login_resp.read().decode('utf-8')
    
    import re
    csrf_token = ''
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', html)
    if match:
        csrf_token = match.group(1)
    else:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if match:
            csrf_token = match.group(1)

    login_data = json.dumps({'username': 'testadmin', 'password': 'test123456'}).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if csrf_token:
        headers['X-CSRFToken'] = csrf_token
        
    login_req = urllib.request.Request('http://127.0.0.1:5000/login', data=login_data, headers=headers)
    login_resp = opener.open(login_req, timeout=10)
    print('LOGIN', login_resp.getcode(), login_resp.read().decode('utf-8', errors='replace'))

    sync_req = urllib.request.Request('http://127.0.0.1:5000/api/sync', data=json.dumps({}).encode('utf-8'), headers={'Content-Type': 'application/json'})
    try:
        sync_resp = opener.open(sync_req, timeout=60)
        print('SYNC', sync_resp.getcode(), sync_resp.read().decode('utf-8', errors='replace'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print('SYNC HTTP ERROR', e.code, body)
    except Exception as e:
        print('SYNC ERROR', type(e).__name__, e)

    time.sleep(2)
    print('---SERVER OUTPUT---')
    if proc.stdout:
        stdout = proc.stdout.read()
        print(stdout)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
