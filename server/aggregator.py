#!/usr/bin/env python3
"""
VyOmni Aggregator — 集中式数据聚合器（v2.0）
节点注册 + HMAC验证 + 动态配置下发 + 远程升级管理 + 节点审核
"""

import json
import time
import hashlib
import hmac
import os
import sys
import secrets
import shutil
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Lock
from urllib.parse import urlparse, parse_qs

# === 配置 ===
DATA_DIR = os.environ.get('DATA_DIR', '/data')
LISTEN_PORT = int(os.environ.get('LISTEN_PORT', '9100'))
REGISTER_TOKEN = os.environ.get('REGISTER_TOKEN', 'vyomni-2025')
TIME_WINDOW = 120  # 签名有效窗口（秒）
DEFAULT_REPORT_INTERVAL = 10

# === 数据文件路径 ===
NODES_FILE = os.path.join(DATA_DIR, 'nodes.json')
UPGRADE_DIR = os.path.join(DATA_DIR, 'upgrades')

# === 状态存储 ===
state_lock = Lock()
nodes = {}  # node_id -> node_info
hq_state = {}  # 最近一次总部上报
branch_states = {}  # branch_id -> 最近一次分支上报
upgrade_info = None  # 当前可用升级信息


# === 持久化 ===
def load_nodes():
    """从 JSON 文件加载节点数据"""
    global nodes
    if os.path.exists(NODES_FILE):
        try:
            with open(NODES_FILE) as f:
                nodes = json.load(f)
        except (json.JSONDecodeError, IOError):
            nodes = {}


def save_nodes():
    """保存节点数据到 JSON 文件"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(NODES_FILE, 'w') as f:
        json.dump(nodes, f, indent=2, ensure_ascii=False)


def load_upgrade_info():
    """加载升级信息"""
    global upgrade_info
    info_path = os.path.join(UPGRADE_DIR, 'latest.json')
    if os.path.exists(info_path):
        try:
            with open(info_path) as f:
                upgrade_info = json.load(f)
        except (json.JSONDecodeError, IOError):
            upgrade_info = None


# === HMAC 验证 ===
def verify_signature(body_bytes, ts_str, sig_str, hmac_key):
    """验证 HMAC-SHA256 签名"""
    try:
        ts = int(ts_str)
        if abs(time.time() - ts) > TIME_WINDOW:
            return False
        expected = hmac.new(
            hmac_key.encode(),
            (ts_str + body_bytes.decode()).encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, sig_str)
    except Exception:
        return False


# === 状态文件写入（兼容旧版前端） ===
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

    # status-branches.json — 仅包含 approved 节点
    branches = []
    for bid, bstate in branch_states.items():
        # 检查节点是否已审核通过
        node_id = bstate.get('node_id', bid)
        node_info = nodes.get(node_id, {})
        if node_info.get('status') != 'approved':
            continue
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


# === 构建 report 响应（含动态配置+升级通知） ===
def build_report_response(node_id):
    """构建上报响应，包含配置更新和升级通知"""
    response = {'status': 'ok'}

    node_info = nodes.get(node_id)
    if not node_info:
        return response

    # 动态配置下发
    pending_config = node_info.get('pending_config')
    if pending_config:
        response['config_update'] = pending_config
        # 清除待下发配置
        node_info.pop('pending_config', None)
        save_nodes()

    # 升级通知
    if upgrade_info:
        node_version = node_info.get('version', '0.0.0')
        if upgrade_info.get('version') and upgrade_info['version'] != node_version:
            response['upgrade_available'] = {
                'version': upgrade_info['version'],
                'sha256': upgrade_info.get('sha256', ''),
            }

    return response


# === HTTP Handler ===
class ApiHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length) if length > 0 else b''

    def do_OPTIONS(self):
        """CORS preflight"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Timestamp, X-Signature, X-Node-ID')
        self.end_headers()

    def do_POST(self):
        path = self.path.split('?')[0]

        if path == '/register':
            self.handle_register()
        elif path == '/report':
            self.handle_report()
        elif path.startswith('/api/nodes/') and path.endswith('/approve'):
            node_id = path.split('/')[3]
            self.handle_approve(node_id)
        elif path.startswith('/api/nodes/') and path.endswith('/reject'):
            node_id = path.split('/')[3]
            self.handle_reject(node_id)
        elif path.startswith('/api/nodes/') and path.endswith('/config'):
            node_id = path.split('/')[3]
            self.handle_set_config(node_id)
        elif path == '/api/upgrade':
            self.handle_upload_upgrade()
        else:
            self.send_json(404, {'error': 'not found'})

    def do_GET(self):
        path = self.path.split('?')[0]

        if path == '/health':
            self.handle_health()
        elif path == '/api/nodes':
            self.handle_list_nodes()
        elif path == '/api/upgrade/latest':
            self.handle_download_upgrade()
        else:
            self.send_json(404, {'error': 'not found'})

    def do_DELETE(self):
        path = self.path.split('?')[0]
        if path.startswith('/api/nodes/'):
            node_id = path.split('/')[3]
            self.handle_delete_node(node_id)
        else:
            self.send_json(404, {'error': 'not found'})

    # --- Registration ---
    def handle_register(self):
        body = self.read_body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'invalid JSON'})
            return

        # 验证注册令牌
        token = payload.get('register_token', '')
        if token != REGISTER_TOKEN:
            self.send_json(403, {'error': 'invalid register_token'})
            return

        node_id = payload.get('node_id', '')
        if not node_id:
            self.send_json(400, {'error': 'node_id required'})
            return

        with state_lock:
            # 检查是否已注册
            if node_id in nodes:
                existing = nodes[node_id]
                self.send_json(200, {
                    'hmac_key': existing['hmac_key'],
                    'report_interval': existing.get('report_interval', DEFAULT_REPORT_INTERVAL),
                    'status': existing.get('status', 'pending'),
                })
                return

            # 生成专属 HMAC key
            hmac_key = secrets.token_hex(32)

            node_info = {
                'node_id': node_id,
                'role': payload.get('role', 'unknown'),
                'hostname': payload.get('hostname', ''),
                'capabilities': payload.get('capabilities', []),
                'version': payload.get('version', ''),
                'ip': payload.get('ip', ''),
                'hmac_key': hmac_key,
                'status': 'pending',  # 待审核
                'report_interval': DEFAULT_REPORT_INTERVAL,
                'registered_at': int(time.time()),
                'last_seen': 0,
                'custom_labels': {},
            }

            nodes[node_id] = node_info
            save_nodes()

        print(f'[REGISTER] New node: {node_id} (role={payload.get("role")}, ip={payload.get("ip")})')

        self.send_json(200, {
            'hmac_key': hmac_key,
            'report_interval': DEFAULT_REPORT_INTERVAL,
            'status': 'pending',
        })

    # --- Report ---
    def handle_report(self):
        body = self.read_body()
        ts = self.headers.get('X-Timestamp', '')
        sig = self.headers.get('X-Signature', '')
        node_id = self.headers.get('X-Node-ID', '')

        # 查找节点的 HMAC key
        with state_lock:
            node_info = nodes.get(node_id)

        if not node_info:
            # 兼容旧版（无 X-Node-ID 的情况），尝试从 payload 获取
            try:
                payload = json.loads(body)
                node_id = payload.get('node_id', '')
                node_info = nodes.get(node_id) if node_id else None
            except json.JSONDecodeError:
                self.send_json(400, {'error': 'invalid JSON'})
                return

        if not node_info:
            self.send_json(403, {'error': 'unknown node, please register first'})
            return

        hmac_key = node_info['hmac_key']

        if not verify_signature(body, ts, sig, hmac_key):
            self.send_json(403, {'error': 'invalid signature'})
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'invalid JSON'})
            return

        role = payload.get('role', '')

        with state_lock:
            # 更新节点最后见时间
            node_info['last_seen'] = int(time.time())
            if payload.get('version'):
                node_info['version'] = payload['version']

            if role == 'hq':
                hq_state.update(payload)
                print(f'[{time.strftime("%H:%M:%S")}] HQ report: {len(payload.get("peers", []))} peers')
            elif role == 'branch':
                bid = payload.get('branch_id', payload.get('node_id', 'unknown'))
                branch_states[bid] = payload
                print(f'[{time.strftime("%H:%M:%S")}] Branch report: {bid}')
            else:
                self.send_json(400, {'error': 'unknown role'})
                return

            save_nodes()
            write_status_files()

            # 构建响应
            response = build_report_response(node_id)

        self.send_json(200, response)

    # --- Node Management ---
    def handle_list_nodes(self):
        with state_lock:
            node_list = []
            for nid, info in nodes.items():
                node_list.append({
                    'node_id': info['node_id'],
                    'role': info.get('role', ''),
                    'hostname': info.get('hostname', ''),
                    'ip': info.get('ip', ''),
                    'status': info.get('status', 'pending'),
                    'version': info.get('version', ''),
                    'capabilities': info.get('capabilities', []),
                    'report_interval': info.get('report_interval', DEFAULT_REPORT_INTERVAL),
                    'custom_labels': info.get('custom_labels', {}),
                    'registered_at': info.get('registered_at', 0),
                    'last_seen': info.get('last_seen', 0),
                })
        self.send_json(200, {'nodes': node_list})

    def handle_approve(self, node_id):
        with state_lock:
            if node_id not in nodes:
                self.send_json(404, {'error': 'node not found'})
                return
            nodes[node_id]['status'] = 'approved'
            nodes[node_id].setdefault('pending_config', {})['status'] = 'approved'
            save_nodes()
        print(f'[APPROVE] Node approved: {node_id}')
        self.send_json(200, {'status': 'approved', 'node_id': node_id})

    def handle_reject(self, node_id):
        with state_lock:
            if node_id not in nodes:
                self.send_json(404, {'error': 'node not found'})
                return
            nodes[node_id]['status'] = 'rejected'
            nodes[node_id].setdefault('pending_config', {})['status'] = 'rejected'
            save_nodes()
        print(f'[REJECT] Node rejected: {node_id}')
        self.send_json(200, {'status': 'rejected', 'node_id': node_id})

    def handle_delete_node(self, node_id):
        with state_lock:
            if node_id not in nodes:
                self.send_json(404, {'error': 'node not found'})
                return
            del nodes[node_id]
            # 清理 branch_states
            branch_states.pop(node_id, None)
            save_nodes()
            write_status_files()
        print(f'[DELETE] Node deleted: {node_id}')
        self.send_json(200, {'status': 'deleted', 'node_id': node_id})

    def handle_set_config(self, node_id):
        """下发动态配置到指定节点"""
        body = self.read_body()
        try:
            config_data = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'invalid JSON'})
            return

        with state_lock:
            if node_id not in nodes:
                self.send_json(404, {'error': 'node not found'})
                return

            # 合并到 pending_config
            pending = nodes[node_id].get('pending_config', {})
            if 'report_interval' in config_data:
                pending['report_interval'] = int(config_data['report_interval'])
                nodes[node_id]['report_interval'] = int(config_data['report_interval'])
            if 'capabilities' in config_data:
                pending['capabilities'] = config_data['capabilities']
                nodes[node_id]['capabilities'] = config_data['capabilities']
            if 'custom_labels' in config_data:
                pending['custom_labels'] = config_data['custom_labels']
                nodes[node_id]['custom_labels'] = config_data['custom_labels']

            nodes[node_id]['pending_config'] = pending
            save_nodes()

        print(f'[CONFIG] Config queued for {node_id}: {config_data}')
        self.send_json(200, {'status': 'config_queued', 'node_id': node_id})

    # --- Upgrade Management ---
    def handle_upload_upgrade(self):
        """上传新版 Agent 脚本"""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self.send_json(400, {'error': 'empty body'})
            return

        body = self.rfile.read(content_length)

        # 从 header 获取版本号
        version = self.headers.get('X-Agent-Version', '')
        if not version:
            self.send_json(400, {'error': 'X-Agent-Version header required'})
            return

        os.makedirs(UPGRADE_DIR, exist_ok=True)

        # 保存文件
        file_path = os.path.join(UPGRADE_DIR, f'agent_{version}.py')
        with open(file_path, 'wb') as f:
            f.write(body)

        # 计算 SHA256
        sha256 = hashlib.sha256(body).hexdigest()

        # 更新升级信息
        global upgrade_info
        upgrade_info = {
            'version': version,
            'sha256': sha256,
            'file_path': file_path,
            'uploaded_at': int(time.time()),
            'size': len(body),
        }

        info_path = os.path.join(UPGRADE_DIR, 'latest.json')
        with open(info_path, 'w') as f:
            json.dump(upgrade_info, f, indent=2)

        print(f'[UPGRADE] New version uploaded: v{version} ({len(body)} bytes, sha256={sha256[:16]}...)')
        self.send_json(200, {
            'status': 'uploaded',
            'version': version,
            'sha256': sha256,
            'size': len(body),
        })

    def handle_download_upgrade(self):
        """Agent 下载新版本"""
        if not upgrade_info or not upgrade_info.get('file_path'):
            self.send_json(404, {'error': 'no upgrade available'})
            return

        file_path = upgrade_info['file_path']
        if not os.path.exists(file_path):
            self.send_json(404, {'error': 'upgrade file missing'})
            return

        with open(file_path, 'rb') as f:
            data = f.read()

        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Agent-Version', upgrade_info.get('version', ''))
        self.send_header('X-SHA256', upgrade_info.get('sha256', ''))
        self.end_headers()
        self.wfile.write(data)

    # --- Health ---
    def handle_health(self):
        self.send_json(200, {
            'status': 'healthy',
            'hq_last_seen': hq_state.get('timestamp', 0),
            'branches': len(branch_states),
            'total_nodes': len(nodes),
            'approved_nodes': sum(1 for n in nodes.values() if n.get('status') == 'approved'),
            'pending_nodes': sum(1 for n in nodes.values() if n.get('status') == 'pending'),
        })

    def log_message(self, format, *args):
        pass  # 静默HTTP日志


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # 加载持久化数据
    load_nodes()
    load_upgrade_info()

    print(f'[INFO] VyOmni Aggregator v2.0 starting...')
    print(f'[INFO] Listening on :{LISTEN_PORT}')
    print(f'[INFO] Data dir: {DATA_DIR}')
    print(f'[INFO] Loaded {len(nodes)} registered nodes')

    server = HTTPServer(('0.0.0.0', LISTEN_PORT), ApiHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[INFO] Shutting down.')
        server.shutdown()


if __name__ == '__main__':
    main()
