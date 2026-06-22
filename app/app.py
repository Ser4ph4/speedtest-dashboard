from flask import Flask, request, jsonify, render_template, redirect, url_for, make_response
from flask_jwt_extended import (
    JWTManager, create_access_token, verify_jwt_in_request,
    set_access_cookies, unset_jwt_cookies
)
from functools import wraps
import sqlite3
import os
import json
from datetime import datetime, timedelta

app = Flask(__name__)

app.config['JWT_SECRET_KEY']            = os.environ.get('JWT_SECRET', 'change-me-now')
app.config['JWT_ACCESS_TOKEN_EXPIRES']  = timedelta(hours=24)
app.config['JWT_TOKEN_LOCATION']        = ['cookies']
app.config['JWT_COOKIE_SECURE']         = False
app.config['JWT_COOKIE_CSRF_PROTECT']   = False
app.config['JWT_COOKIE_SAMESITE']       = 'Lax'

jwt = JWTManager(app)

DB_PATH    = os.environ.get('DB_PATH',    '/data/speedtest.db')
API_KEY    = os.environ.get('API_KEY',    '')
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'changeme')


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS results (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            host             TEXT    NOT NULL,
            timestamp        TEXT    NOT NULL,
            download_mbps    REAL,
            upload_mbps      REAL,
            ping_ms          REAL,
            jitter_ms        REAL,
            isp              TEXT,
            server_name      TEXT,
            server_location  TEXT,
            result_url       TEXT,
            raw_json         TEXT,
            created_at       TEXT    DEFAULT (datetime('now'))
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_host      ON results(host)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON results(timestamp)')
    conn.commit()
    conn.close()


# ── Auth decorators ───────────────────────────────────────────────────────────

def api_key_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key')
        if not key or key != API_KEY:
            return jsonify({'error': 'Invalid API key'}), 401
        return f(*args, **kwargs)
    return decorated

def web_auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
        except Exception:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Web routes ────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json() or {}
        if data.get('username') == ADMIN_USER and data.get('password') == ADMIN_PASS:
            token = create_access_token(identity=ADMIN_USER)
            resp  = make_response(jsonify({'success': True}))
            set_access_cookies(resp, token)
            return resp
        return jsonify({'error': 'Credenciais inválidas'}), 401
    return render_template('login.html')

@app.route('/logout')
def logout():
    resp = make_response(redirect(url_for('login')))
    unset_jwt_cookies(resp)
    return resp

@app.route('/dashboard')
@web_auth_required
def dashboard():
    return render_template('dashboard.html')


# ── Agent API ─────────────────────────────────────────────────────────────────

@app.route('/api/push', methods=['POST'])
@api_key_required
def push_result():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No JSON payload'}), 400

    host = data.get('host', '').strip()
    raw  = data.get('raw')          # full Ookla JSON string

    if not host or not raw:
        return jsonify({'error': 'Missing host or raw'}), 400

    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw

        # Ookla reports bandwidth in bytes/s → convert to Mbps
        dl   = parsed.get('download', {}).get('bandwidth', 0) / 125_000
        ul   = parsed.get('upload',   {}).get('bandwidth', 0) / 125_000
        ping = parsed.get('ping',     {}).get('latency',   0)
        jit  = parsed.get('ping',     {}).get('jitter',    0)
        isp  = parsed.get('isp', '')
        srv  = parsed.get('server', {})
        ts   = parsed.get('timestamp', datetime.utcnow().isoformat())

        conn = get_db()
        conn.execute('''
            INSERT INTO results
                (host, timestamp, download_mbps, upload_mbps, ping_ms, jitter_ms,
                 isp, server_name, server_location, result_url, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            host, ts,
            round(dl, 2), round(ul, 2), round(ping, 2), round(jit, 2),
            isp,
            srv.get('name', ''),
            srv.get('location', ''),
            parsed.get('result', {}).get('url', ''),
            json.dumps(parsed)
        ))
        conn.commit()
        conn.close()

        return jsonify({'ok': True, 'host': host, 'download_mbps': round(dl, 2)})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Dashboard API ─────────────────────────────────────────────────────────────

@app.route('/api/hosts')
@web_auth_required
def get_hosts():
    conn = get_db()
    rows = conn.execute('SELECT DISTINCT host FROM results ORDER BY host').fetchall()
    conn.close()
    return jsonify([r['host'] for r in rows])


@app.route('/api/stats')
@web_auth_required
def get_stats():
    host = request.args.get('host')
    days = int(request.args.get('days', 30))

    conn   = get_db()
    params = [f'-{days} days']
    where  = "created_at >= datetime('now', ?)"
    if host:
        where += ' AND host = ?'
        params.append(host)

    # Averages over window
    avg = conn.execute(
        f'SELECT AVG(download_mbps) avg_dl, AVG(upload_mbps) avg_ul, '
        f'AVG(ping_ms) avg_ping, AVG(jitter_ms) avg_jitter FROM results WHERE {where}',
        params
    ).fetchone()

    # Latest per host
    hosts_rows = conn.execute('SELECT DISTINCT host FROM results').fetchall()
    latest = []
    for h in hosts_rows:
        row = conn.execute(
            'SELECT host, download_mbps, upload_mbps, ping_ms, jitter_ms, timestamp, isp '
            'FROM results WHERE host = ? ORDER BY timestamp DESC LIMIT 1',
            (h['host'],)
        ).fetchone()
        if row:
            latest.append(dict(row))

    conn.close()
    return jsonify({'latest': latest, 'averages': dict(avg) if avg else {}})


@app.route('/api/results')
@web_auth_required
def get_results():
    host  = request.args.get('host')
    days  = int(request.args.get('days', 30))
    limit = int(request.args.get('limit', 500))

    conn   = get_db()
    params = [f'-{days} days']
    where  = "created_at >= datetime('now', ?)"
    if host:
        where += ' AND host = ?'
        params.append(host)
    params.append(limit)

    rows = conn.execute(
        f'SELECT id, host, timestamp, download_mbps, upload_mbps, ping_ms, jitter_ms, '
        f'isp, server_name, server_location, result_url '
        f'FROM results WHERE {where} ORDER BY timestamp ASC LIMIT ?',
        params
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/recent')
@web_auth_required
def get_recent():
    host  = request.args.get('host')
    limit = int(request.args.get('limit', 20))

    conn   = get_db()
    params = []
    where  = '1=1'
    if host:
        where = 'host = ?'
        params.append(host)
    params.append(limit)

    rows = conn.execute(
        f'SELECT id, host, timestamp, download_mbps, upload_mbps, ping_ms, jitter_ms, '
        f'server_name, server_location, result_url '
        f'FROM results WHERE {where} ORDER BY timestamp DESC LIMIT ?',
        params
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)