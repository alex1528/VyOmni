#!/usr/bin/env python3
"""
VyOmni Aggregator — 集中式数据聚合器
部署在 Linux 监控服务器，接收各节点 Agent 上报并生成状态 JSON
"""

import json
import time
import hashlib
import hmac
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Lock

# === 配置 ===
DATA_DIR = os.environ.get('DATA_DIR', '/data')
CONFIG_PATH = os.environ.get('CONFIG_PATH', '/app/config.json')
SECRETS_PATH = os.environ.get('SECRETS_PATH', '/app/secrets.json')
LISTEN_PORT = int(os.environ.get('LISTEN_PORT', '9100'))
HMAC_KEY = 'change-me-in-production'
TIME_WINDOW = 60  # 签名有效窗口（秒）

# === 状态存储 ===
state_lock = Lock()
hq_state = {}  # 最近一次总部上报
branch_states = {}  # branch_id -> 最近一次分支上报

def load_secrets():
    global HMAC_KEY
    if os.path.exists(SECRETS_PATH):
        with open(SECRETS_PATH) as f:
            secrets = json.load(f)
            HMAC_KEY = secrets.get('hmac_key', HMAC_KEY)

def verify_signature(body_bytes, ts_str, sig_str):
    """验证 HMAC-SHA256 签名"""
    try:
        ts = int(ts_str)
        if abs(time.time() - ts) > TIME_WINDOW:
            return False
        expected = hmac.new(
            HMAC_KEY.encode(),
            (ts_str + body_bytes.decode()).encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, sig_str)
    except:
        return False

def write_status_files():
    """将聚合状态写入 JSON 文件供 Nginx 静态服务"""
    os.makedirs(DATA_DIR, exist_ok=True)
    
    now = int(time.time())
    
    # status-tunnel.json
    tunnel_data = {
        'updated_at': now,
        'collector_heartbeat': hq_state.get('timestamp', 0),
        'system': hq_state.get('system', {}),
        'peers': hq_state.get('peers', []),
        'totals': {'rx_mbps': 0, 'tx_mbps': 0},
    }
    tunnel_data['system']['hostname'] = hq_state.get('hostname', 'Unknown')
    tunnel_data['system']['tunnel_active'] = sum(
        1 for p in tunnel_data['peers']
        if now - p.get('latest_handshake', 0) < 180
    )
    tunnel_data['system']['tunnel_total'] = len(tunnel_data['peers'])
    
    with open(os.path.join(DATA_DIR, 'status-tunnel.json'), 'w') as f:
        json.dump(tunnel_data, f, indent=2)
    
    # status-branches.json
    branches = []
    for bid, bstate in branch_states.items():
        branches.append({
            'branch_id': bid,
            'hostname': bstate.get('hostname', bid),
            'last_seen': bstate.get('timestamp', 0),
            'online': now - bstate.get('timestamp', 0) < 60,
            'system': bstate.get('system', {}),
            'interfaces': bstate.get('interfaces', {}),
        })
    
    branch_data = {
        'updated_at': now,
        'branches': branches,
    }
    
    with open(os.path.join(DATA_DIR, 'status-branches.json'), 'w') as f:
        json.dump(branch_data, f, indent=2)

class ReportHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/report':
            self.send_error(404)
            return
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        ts = self.headers.get('X-Timestamp', '')
        sig = self.headers.get('X-Signature', '')
        
        if not verify_signature(body, ts, sig):
            self.send_error(403, 'Invalid signature')
            return
        
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, 'Invalid JSON')
            return
        
        role = payload.get('role', '')
        
        with state_lock:
            if role == 'hq':
                hq_state.update(payload)
                print(f'[{time.strftime("%H:%M:%S")}] HQ report: {len(payload.get("peers", []))} peers')
            elif role == 'branch':
                bid = payload.get('branch_id', 'unknown')
                branch_states[bid] = payload
                print(f'[{time.strftime("%H:%M:%S")}] Branch report: {bid}')
            else:
                self.send_error(400, 'Unknown role')
                return
            
            write_status_files()
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'status': 'healthy',
                'hq_last_seen': hq_state.get('timestamp', 0),
                'branches': len(branch_states),
            }).encode())
        else:
            self.send_error(404)
    
    def log_message(self, format, *args):
        pass  # 静默HTTP日志

def main():
    load_secrets()
    os.makedirs(DATA_DIR, exist_ok=True)
    
    server = HTTPServer(('0.0.0.0', LISTEN_PORT), ReportHandler)
    print(f'[INFO] VyOmni Aggregator listening on :{LISTEN_PORT}')
    print(f'[INFO] Data dir: {DATA_DIR}')
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[INFO] Shutting down.')
        server.shutdown()

if __name__ == '__main__':
    main()
