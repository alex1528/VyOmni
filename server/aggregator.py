#!/usr/bin/env python3
"""
VyOmni Aggregator — 集中式数据聚合器（v2.1）
节点注册 + HMAC验证 + 动态配置下发 + 远程升级管理 + 节点审核
+ 一次性Token生成 + curl|bash 一键部署端点
"""

import json
import urllib.request
import time
import hashlib
import hmac
import os
import sys
import secrets
import shutil
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Lock
from urllib.parse import urlparse, parse_qs

# === 配置 ===
DATA_DIR = os.environ.get('DATA_DIR', '/data')
LISTEN_PORT = int(os.environ.get('LISTEN_PORT', '9100'))
REGISTER_TOKEN = os.environ.get('REGISTER_TOKEN', 'vyomni-2025')
TIME_WINDOW = 120  # 签名有效窗口（秒）
DEFAULT_REPORT_INTERVAL = 10
AGENT_FILES_DIR = os.environ.get('AGENT_FILES_DIR', '/app/agent')



# === IP 地理定位 ===
def query_ip_geolocation(ip):
    """通过 ip-api.com 查询 IP 地理位置（免费，无需key）"""
    if not ip or ip in ('0.0.0.0', '127.0.0.1', ''):
        return None
    try:
        url = f'http://ip-api.com/json/{ip}?fields=status,lat,lon,city,regionName,country'
        req = urllib.request.Request(url, headers={'User-Agent': 'VyOmni/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        if data.get('status') == 'success':
            return {
                'lat': data.get('lat', 0),
                'lng': data.get('lon', 0),
                'city': data.get('city', ''),
                'region': data.get('regionName', ''),
                'country': data.get('country', ''),
            }
    except Exception as e:
        print(f'[GEO] IP定位查询失败 {ip}: {e}')
    return None

# === 数据文件路径 ===
NODES_FILE = os.path.join(DATA_DIR, 'nodes.json')
TOKENS_FILE = os.path.join(DATA_DIR, 'tokens.json')
UPGRADE_DIR = os.path.join(DATA_DIR, 'upgrades')

# === 状态存储 ===
state_lock = Lock()
nodes = {}  # node_id -> node_info
hq_state = {}  # 最近一次总部上报
prev_peer_transfer = {}  # {peer_key: {'rx': bytes, 'tx': bytes, 'time': ts}}
peer_aliases = {}  # {peer_public_key: display_name}
PEER_ALIASES_FILE = os.path.join(DATA_DIR, 'peer_aliases.json')

def load_peer_aliases():
    global peer_aliases
    if os.path.exists(PEER_ALIASES_FILE):
        try:
            with open(PEER_ALIASES_FILE) as f:
                peer_aliases = json.load(f)
        except (json.JSONDecodeError, IOError):
            peer_aliases = {}

def save_peer_aliases():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PEER_ALIASES_FILE, 'w') as f:
        json.dump(peer_aliases, f, indent=2, ensure_ascii=False)

prev_branch_interfaces = {}  # {branch_id: {iface: {'rx': bytes, 'tx': bytes}}}
prev_report_time = 0
branch_states = {}  # branch_id -> 最近一次分支上报
upgrade_info = None  # 当前可用升级信息
deploy_tokens = {}  # token_str -> token_info


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


def load_tokens():
    """从 JSON 文件加载部署 Token 数据"""
    global deploy_tokens
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE) as f:
                deploy_tokens = json.load(f)
        except (json.JSONDecodeError, IOError):
            deploy_tokens = {}


def save_tokens():
    """保存部署 Token 数据到 JSON 文件"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TOKENS_FILE, 'w') as f:
        json.dump(deploy_tokens, f, indent=2, ensure_ascii=False)


def load_upgrade_info():
    """
    加载升级信息
    优先从 /app/agent/agent_common.py 自动读取 AGENT_VERSION
    这样 git pull + docker rebuild 后自动成为全网升级目标
    """
    global upgrade_info

    # 方式1：自动从 Agent 文件读取最新版本
    agent_common_path = os.path.join(AGENT_FILES_DIR, 'agent_common.py')
    if os.path.exists(agent_common_path):
        try:
            with open(agent_common_path, 'r') as f:
                content = f.read()
            # 解析 AGENT_VERSION = 'x.x.x'
            import re as _re
            match = _re.search(r"AGENT_VERSION\s*=\s*['\"](.+?)['\"]", content)
            if match:
                latest_version = match.group(1)
                # 计算 agent_common.py 的 SHA256
                with open(agent_common_path, 'rb') as f:
                    sha256 = hashlib.sha256(f.read()).hexdigest()
                upgrade_info = {
                    'version': latest_version,
                    'sha256': sha256,
                    'source': 'auto',
                }
                print(f'[UPGRADE] 自动检测最新 Agent 版本: {latest_version}')
                return
        except Exception as e:
            print(f'[WARN] 自动读取 Agent 版本失败: {e}')

    # 方式2：从手动上传的 latest.json 读取（兼容旧方式）
    info_path = os.path.join(DATA_DIR, '.upgrades', 'latest.json')
    if os.path.exists(info_path):
        try:
            with open(info_path) as f:
                upgrade_info = json.load(f)
        except (json.JSONDecodeError, IOError):
            upgrade_info = None


# === Token 管理 ===
def generate_deploy_token(name, role):
    """生成一次性部署 Token（tk_ + 12位hex，24小时有效）"""
    token_str = 'tk_' + secrets.token_hex(6)
    now = int(time.time())
    expires_at = now + 86400  # 24小时

    token_info = {
        'token': token_str,
        'name': name,
        'role': role,
        'status': 'unused',  # unused / used / expired
        'created_at': now,
        'expires_at': expires_at,
        'used_at': None,
        'used_by_node': None,
    }

    deploy_tokens[token_str] = token_info
    save_tokens()
    return token_info


def validate_deploy_token(token_str):
    """验证部署 Token，返回 token_info 或 None"""
    token_info = deploy_tokens.get(token_str)
    if not token_info:
        return None
    if token_info['status'] != 'unused':
        return None
    if int(time.time()) > token_info['expires_at']:
        token_info['status'] = 'expired'
        save_tokens()
        return None
    return token_info


def consume_deploy_token(token_str, node_id):
    """消费（作废）一次性 Token"""
    token_info = deploy_tokens.get(token_str)
    if token_info:
        token_info['status'] = 'used'
        token_info['used_at'] = int(time.time())
        token_info['used_by_node'] = node_id
        save_tokens()


def cleanup_expired_tokens():
    """清理过期 Token"""
    now = int(time.time())
    changed = False
    for tk, info in deploy_tokens.items():
        if info['status'] == 'unused' and now > info['expires_at']:
            info['status'] = 'expired'
            changed = True
    if changed:
        save_tokens()


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
def _get_branch_endpoint(bstate):
    """获取分支节点连接的对端 Endpoint（分支自身 wg dump 中的 peer endpoint）"""
    wg_peers = bstate.get('wg_peers', [])
    if wg_peers:
        # 取第一个有 endpoint 的 peer（通常分支只有一个 peer = 总部）
        for wp in wg_peers:
            ep = wp.get('endpoint', '')
            if ep:
                return ep
    return ''


def _get_branch_allowed_ips(bstate, peer_endpoint_map, node_info):
    """
    获取分支节点的 Allowed IPs（仅使用分支自身上报数据）
    来源：分支自己 wg show all dump 中看到的 peers 的 allowed_ips
    """
    wg_peers = bstate.get('wg_peers', [])
    if wg_peers:
        all_allowed = []
        for wp in wg_peers:
            ips = wp.get('allowed_ips', '')
            if ips:
                all_allowed.append(ips)
        if all_allowed:
            return '; '.join(all_allowed)
    return ''


def _resolve_branch_display_name(node_info, peer_endpoint_map):
    """
    获取分支节点的显示别名（独立于隧道 peer_aliases）
    分支卡片别名 = node 自身的 display_name（通过 /api/nodes/{id}/rename 设置）
    与隧道卡片的 peer_aliases 完全独立，互不干扰
    """
    return node_info.get('display_name', '')


def write_status_files():
    """将聚合状态写入 JSON 文件供 Nginx 静态服务"""
    os.makedirs(DATA_DIR, exist_ok=True)
    now = int(time.time())

    # status-tunnel.json
    global prev_peer_transfer, prev_report_time

    raw_peers = hq_state.get('peers', [])
    report_time = hq_state.get('timestamp', 0)
    # 仅当 hq 有新数据时（timestamp 变化）才计算速率
    dt = (report_time - prev_report_time) if (prev_report_time > 0 and report_time > prev_report_time) else 0

    enriched_peers = []
    active_count = 0
    total_rx_rate = 0.0
    total_tx_rate = 0.0

    for p in raw_peers:
        handshake_ts = p.get('latest_handshake', 0)
        handshake_ago = (now - handshake_ts) if handshake_ts > 0 else 99999
        is_online = handshake_ago < 180
        if is_online:
            active_count += 1

        # 实时速率计算：(本次累计字节 - 上次累计字节) / 时间差
        transfer_rx = p.get('transfer_rx', 0)
        transfer_tx = p.get('transfer_tx', 0)
        peer_key = p.get('public_key', '') or p.get('endpoint', '')

        rx_rate = 0.0
        tx_rate = 0.0
        if peer_key and peer_key in prev_peer_transfer and dt > 0:
            prev = prev_peer_transfer[peer_key]
            delta_rx = transfer_rx - prev['rx']
            delta_tx = transfer_tx - prev['tx']
            # 防止计数器重置导致负值
            if delta_rx >= 0 and delta_tx >= 0:
                rx_rate = round(delta_rx * 8 / dt / 1_000_000, 3)  # Mbps
                tx_rate = round(delta_tx * 8 / dt / 1_000_000, 3)

        # 更新历史记录
        if peer_key:
            prev_peer_transfer[peer_key] = {'rx': transfer_rx, 'tx': transfer_tx}

        total_rx_rate += rx_rate
        total_tx_rate += tx_rate

        enriched_peers.append({
            'interface': p.get('interface', ''),
            'peer': peer_key,
            'name': peer_aliases.get(peer_key, peer_key[:16] + '...' if len(peer_key) > 16 else (peer_key or 'unknown')),
            'branch_id': '',
            'status': 'online' if is_online else 'offline',
            'endpoint': p.get('endpoint', ''),
            'allowed_ips': p.get('allowed_ips', ''),
            'last_handshake_seconds_ago': handshake_ago,
            'rx_rate_mbps': rx_rate,
            'tx_rate_mbps': tx_rate,
            'transfer_rx': transfer_rx,
            'transfer_tx': transfer_tx,
        })

    # 仅当有新 hq 数据时才更新时间基准
    if report_time > prev_report_time:
        prev_report_time = report_time

    tunnel_data = {
        'updated_at': now,
        'collector_heartbeat': hq_state.get('timestamp', 0),
        'system': hq_state.get('system', {}),
        'peers': enriched_peers,
        'totals': {'rx_mbps': round(total_rx_rate, 2), 'tx_mbps': round(total_tx_rate, 2)},
    }
    tunnel_data['system']['hostname'] = hq_state.get('hostname', 'Unknown')
    tunnel_data['system']['tunnel_active'] = active_count
    tunnel_data['system']['tunnel_total'] = len(raw_peers)

    with open(os.path.join(DATA_DIR, 'status-tunnel.json'), 'w') as f:
        json.dump(tunnel_data, f, indent=2)

    # status-branches.json — 仅包含 approved 节点
    global prev_branch_interfaces

    # 构建 IP→peer信息 映射（从 HQ peers 数据中提取）
    # key=endpoint_ip, value={'peer_key': 完整公钥, 'allowed_ips': 字符串}
    peer_endpoint_map = {}
    for p in enriched_peers:
        ep = p.get('endpoint', '')
        if ':' in ep:
            ep_ip = ep.rsplit(':', 1)[0]  # 去掉端口
        else:
            ep_ip = ep
        if ep_ip:
            peer_endpoint_map[ep_ip] = {
                'peer_key': p.get('peer', ''),
                'allowed_ips': p.get('allowed_ips', ''),
            }

    branches = []
    for bid, bstate in branch_states.items():
        node_id = bstate.get('node_id', bid)
        node_info = nodes.get(node_id, {}) if nodes else {}
        if node_info.get('status') != 'approved':
            continue

        sys_data = bstate.get('system', {})
        report_ts = bstate.get('timestamp', 0)
        raw_ifaces = bstate.get('interfaces', {})

        # 接口速率差值计算
        enriched_ifaces = {}
        prev_ifaces = prev_branch_interfaces.get(bid, {})
        dt_branch = (now - report_ts) if report_ts > 0 else 5  # 近似上报间隔

        for iface, idata in raw_ifaces.items():
            rx_bytes = idata.get('rx_bytes', 0)
            tx_bytes = idata.get('tx_bytes', 0)
            rx_rate = 0.0
            tx_rate = 0.0

            if iface in prev_ifaces:
                prev = prev_ifaces[iface]
                # 使用上报间隔作为时间差（branch 固定间隔上报）
                dt = max(dt_branch, 1)
                delta_rx = rx_bytes - prev.get('rx', 0)
                delta_tx = tx_bytes - prev.get('tx', 0)
                if delta_rx >= 0 and delta_tx >= 0 and dt > 0:
                    rx_rate = round(delta_rx * 8 / dt / 1_000_000, 3)
                    tx_rate = round(delta_tx * 8 / dt / 1_000_000, 3)

            enriched_ifaces[iface] = {
                'rx_mbps': rx_rate,
                'tx_mbps': tx_rate,
                'rx_bytes': rx_bytes,
                'tx_bytes': tx_bytes,
            }

        # 更新历史
        prev_branch_interfaces[bid] = {
            iface: {'rx': idata.get('rx_bytes', 0), 'tx': idata.get('tx_bytes', 0)}
            for iface, idata in raw_ifaces.items()
        }

        branches.append({
            'branch_id': bid,
            'hostname': bstate.get('hostname', bid),
            'display_name': _resolve_branch_display_name(node_info, peer_endpoint_map) if node_info else '',
            'reported_at': report_ts,
            'last_seen': report_ts,
            'online': now - report_ts < 60,
            'stale': now - report_ts > 30,
            # 平铺 system 字段（前端直接读 br.cpu_percent 等）
            'cpu_percent': sys_data.get('cpu_percent', 0),
            'memory_percent': sys_data.get('memory_percent', 0),
            'load_1m': sys_data.get('load_1m', 0),
            'load_5m': sys_data.get('load_5m', 0),
            'load_15m': sys_data.get('load_15m', 0),
            # 接口速率（含 rx_mbps/tx_mbps）
            'interfaces': enriched_ifaces,
            # 保留完整 system 以备前端其他用途
            'system': sys_data,
            # IP 地理位置
            'geo': node_info.get('geo', None),
            # 从分支自身 wg_peers 获取 endpoint 和 allowed_ips
            'endpoint': _get_branch_endpoint(bstate),
            'allowed_ips': _get_branch_allowed_ips(bstate, peer_endpoint_map, node_info),
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
        node_info.pop('pending_config', None)
        save_nodes()

    # 升级通知
    if upgrade_info:
        node_version = node_info.get('version', '0.0.0')
        if upgrade_info.get('version') and upgrade_info['version'] != node_version:
            response['upgrade_available'] = {
                'version': upgrade_info['version'],
                'sha256': upgrade_info.get('sha256', ''),
                'files': ['agent_common.py', 'collector.py', 'branch_agent.py'],
            }

    return response


# === 生成部署脚本 ===
def generate_deploy_script(token_str, token_info, server_url):
    """生成 curl|bash 一键部署脚本"""
    role = token_info['role']
    name = token_info['name']

    # 根据角色决定下载哪个 agent 脚本
    if role == 'hq':
        agent_script = 'collector.py'
    else:
        agent_script = 'branch_agent.py'

    lines = [
        '#!/bin/bash',
        '# ============================================',
        '# VyOmni Agent 一键部署脚本',
        '# 节点名称: ' + name,
        '# 角色: ' + role,
        '# Token: ' + token_str,
        '# ============================================',
        '',
        'set -e',
        '',
        'INSTALL_DIR="/opt/vyomni-agent"',
        'SERVER_URL="' + server_url + '"',
        'TOKEN="' + token_str + '"',
        'ROLE="' + role + '"',
        'AGENT_SCRIPT="' + agent_script + '"',
        '',
        'echo "=========================================="',
        'echo " VyOmni Agent 自动部署"',
        'echo " 节点: ' + name + '"',
        'echo " 角色: ' + role + '"',
        'echo "=========================================="',
        'echo ""',
        '',
        '# 1. 创建安装目录',
        'echo "[1/5] 创建安装目录..."',
        'mkdir -p "$INSTALL_DIR"',
        'cd "$INSTALL_DIR"',
        '',
        '# 2. 下载 Agent 文件',
        'echo "[2/5] 下载 Agent 文件..."',
        'curl -sL "${SERVER_URL}/api/deploy/files/agent_common.py" -o agent_common.py',
        'curl -sL "${SERVER_URL}/api/deploy/files/${AGENT_SCRIPT}" -o "${AGENT_SCRIPT}"',
        'chmod +x agent_common.py "${AGENT_SCRIPT}"',
        'echo "  已下载: agent_common.py, ${AGENT_SCRIPT}"',
        '',
        '# 3. 写入配置文件',
        'echo "[3/5] 写入配置文件..."',
        'cat > config.conf << CONF_EOF',
        '# VyOmni Agent 配置',
        'server_url = ' + server_url,
        'register_token = ' + token_str,
        'CONF_EOF',
        'echo "  配置文件: $INSTALL_DIR/config.conf"',
        '',
        '# 4. 创建 systemd 服务',
        'echo "[4/5] 创建 systemd 服务..."',
        'cat > /etc/systemd/system/vyomni-agent.service << SVC_EOF',
        '[Unit]',
        'Description=VyOmni Agent (' + name + ')',
        'After=network-online.target',
        'Wants=network-online.target',
        '',
        '[Service]',
        'Type=simple',
        'WorkingDirectory=/opt/vyomni-agent',
        'ExecStart=/usr/bin/python3 /opt/vyomni-agent/' + agent_script,
        'MemoryMax=20M',
        'Restart=always',
        'RestartSec=10',
        'StandardOutput=journal',
        'StandardError=journal',
        '',
        '[Install]',
        'WantedBy=multi-user.target',
        'SVC_EOF',
        '',
        'systemctl daemon-reload',
        '',
        '# 5. 启动服务',
        'echo "[5/5] 启动服务..."',
        'systemctl enable vyomni-agent',
        'systemctl start vyomni-agent',
        '',
        'echo ""',
        'echo "=========================================="',
        'echo " 部署完成！"',
        'echo " 安装目录: $INSTALL_DIR"',
        'echo " 查看日志: journalctl -u vyomni-agent -f"',
        'echo "=========================================="',
    ]

    return chr(10).join(lines) + chr(10)


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

    def send_text(self, status, text, content_type='text/plain'):
        body = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
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
        elif path == '/api/peers/alias':
            self.handle_set_peer_alias()
        elif path.startswith('/api/nodes/') and path.endswith('/rename'):
            node_id = path.split('/')[3]
            self.handle_rename_node(node_id)
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
        elif path == '/api/tokens/generate':
            self.handle_generate_token()
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
        elif path == '/api/tokens':
            self.handle_list_tokens()
        elif path == '/api/tunnel':
            self.handle_get_tunnel()
        elif path == '/api/branches':
            self.handle_get_branches()
        elif path == '/api/config':
            self.handle_get_config()
        elif re.match(r'^/api/deploy/tk_[0-9a-f]{12}$', path):
            token_str = path.split('/')[-1]
            self.handle_deploy_script(token_str)
        elif path.startswith('/api/deploy/files/'):
            filename = path.split('/')[-1]
            self.handle_deploy_file(filename)
        else:
            self.send_json(404, {'error': 'not found'})

    def do_DELETE(self):
        path = self.path.split('?')[0]
        if path.startswith('/api/nodes/'):
            node_id = path.split('/')[3]
            self.handle_delete_node(node_id)
        elif path.startswith('/api/tokens/'):
            token_id = path.split('/')[3]
            self.handle_delete_token(token_id)
        else:
            self.send_json(404, {'error': 'not found'})

    # --- Token 生成 ---
    def handle_delete_token(self, token_id):
        """DELETE /api/tokens/{token} — 删除 Token（所有状态均可删除）"""
        with state_lock:
            load_tokens()  # 刷新全局 deploy_tokens
            if token_id not in deploy_tokens:
                self.send_json(404, {'error': 'Token not found'})
                return
            del deploy_tokens[token_id]
            save_tokens()
        self.send_json(200, {'status': 'deleted', 'token': token_id})

    def handle_generate_token(self):
        """POST /api/tokens/generate — 生成一次性部署 Token"""
        body = self.read_body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'invalid JSON'})
            return

        name = payload.get('name', '').strip()
        role = payload.get('role', 'branch').strip()

        if not name:
            self.send_json(400, {'error': 'name is required'})
            return

        if role not in ('hq', 'branch'):
            self.send_json(400, {'error': 'role must be hq or branch'})
            return

        with state_lock:
            token_info = generate_deploy_token(name, role)

        # 构建部署命令
        host = self.headers.get('Host', 'localhost:' + str(LISTEN_PORT))
        scheme = 'http'
        server_url = scheme + '://' + host
        deploy_cmd = 'curl -sL ' + server_url + '/api/deploy/' + token_info['token'] + ' | bash'

        print('[TOKEN] Generated: ' + token_info['token'] + ' for "' + name + '" (role=' + role + ')')

        self.send_json(200, {
            'token': token_info['token'],
            'name': name,
            'role': role,
            'expires_at': token_info['expires_at'],
            'deploy_cmd': deploy_cmd,
            'config_content': 'server_url = ' + server_url + '\nregister_token = ' + token_info['token'],
        })

    # --- Token 列表 ---
    def handle_list_tokens(self):
        """GET /api/tokens — 列出所有部署 Token"""
        with state_lock:
            cleanup_expired_tokens()
            token_list = []
            for tk, info in deploy_tokens.items():
                token_list.append({
                    'token': info['token'],
                    'name': info['name'],
                    'role': info['role'],
                    'status': info['status'],
                    'created_at': info['created_at'],
                    'expires_at': info['expires_at'],
                    'used_at': info.get('used_at'),
                    'used_by_node': info.get('used_by_node'),
                })
        self.send_json(200, {'tokens': token_list})

    # --- 部署脚本端点 ---
    def handle_get_tunnel(self):
        """返回隧道状态数据（从 /data/status-tunnel.json 读取，实时应用别名）"""
        data_dir = os.environ.get('DATA_DIR', '/data')
        filepath = os.path.join(data_dir, 'status-tunnel.json')
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                data = json.load(f)
            # 实时应用 peer_aliases（确保重启后首次请求也有别名）
            load_peer_aliases()
            if peer_aliases and 'peers' in data:
                for p in data['peers']:
                    peer_key = p.get('peer', '')
                    if peer_key and peer_key in peer_aliases:
                        p['name'] = peer_aliases[peer_key]
                        p['display_name'] = peer_aliases[peer_key]
            self.send_json(200, data)
        else:
            self.send_json(200, {
                'updated_at': 0,
                'collector_heartbeat': 0,
                'system': {'hostname': 'waiting...', 'cpu_percent': 0, 'memory_percent': 0, 'tunnel_active': 0, 'tunnel_total': 0},
                'totals': {'rx_mbps': 0, 'tx_mbps': 0},
                'peers': []
            })

    def handle_get_branches(self):
        """返回分支状态数据（从 /data/status-branches.json 读取）"""
        data_dir = os.environ.get('DATA_DIR', '/data')
        filepath = os.path.join(data_dir, 'status-branches.json')
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                data = json.load(f)
            self.send_json(200, data)
        else:
            self.send_json(200, {'updated_at': 0, 'branches': []})

    def handle_get_config(self):
        """返回平台配置（供前端读取）"""
        config_path = os.environ.get('CONFIG_PATH', '/app/config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
        else:
            config = {}

        # 合并节点信息的 geo_locations
        load_nodes()  # 刷新全局 nodes
        geo = config.get('geo_locations', {})

        result = {
            'geo_locations': geo,
            'node_defaults': config.get('node_defaults', {}),
            'nodes_count': len(nodes) if nodes else 0,
        }
        self.send_json(200, result)


    def handle_deploy_script(self, token_str):
        """GET /api/deploy/{token} — 返回部署 bash 脚本"""
        with state_lock:
            token_info = validate_deploy_token(token_str)

        if not token_info:
            error_script = '#!/bin/bash\necho "ERROR: Token 无效、已使用或已过期"\nexit 1\n'
            self.send_text(403, error_script.replace('\\n', '\n'), 'text/x-shellscript')
            return

        # 推断 server_url
        host = self.headers.get('Host', 'localhost:' + str(LISTEN_PORT))
        server_url = 'http://' + host

        script = generate_deploy_script(token_str, token_info, server_url)
        self.send_text(200, script, 'text/x-shellscript')

    # --- Agent 文件下载 ---
    def handle_deploy_file(self, filename):
        """GET /api/deploy/files/{filename} — 提供 Agent 文件下载"""
        # 白名单文件
        allowed_files = {'agent_common.py', 'collector.py', 'branch_agent.py'}
        if filename not in allowed_files:
            self.send_json(404, {'error': 'file not found: ' + filename})
            return

        # 查找文件路径
        file_path = os.path.join(AGENT_FILES_DIR, filename)
        if not os.path.exists(file_path):
            # 尝试相对路径（../agent/）
            alt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agent', filename)
            if os.path.exists(alt_path):
                file_path = alt_path
            else:
                self.send_json(404, {'error': 'file not available: ' + filename})
                return

        with open(file_path, 'rb') as f:
            data = f.read()

        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    # --- Registration ---
    def handle_register(self):
        body = self.read_body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'invalid JSON'})
            return

        # 验证注册令牌（兼容一次性 tk_ Token）
        token = payload.get('register_token', '')

        with state_lock:
            # 检查是否为一次性部署 Token（tk_ 前缀）
            if token.startswith('tk_'):
                token_info = validate_deploy_token(token)
                if not token_info:
                    self.send_json(403, {'error': 'invalid or expired deploy token'})
                    return
            else:
                # 传统固定 Token
                if token != REGISTER_TOKEN:
                    self.send_json(403, {'error': 'invalid register_token'})
                    return

            node_id = payload.get('node_id', '')
            if not node_id:
                self.send_json(400, {'error': 'node_id required'})
                return

            # 检查是否已注册
            if node_id in nodes:
                existing = nodes[node_id]
                if token.startswith('tk_'):
                    consume_deploy_token(token, node_id)
                self.send_json(200, {
                    'hmac_key': existing['hmac_key'],
                    'report_interval': existing.get('report_interval', DEFAULT_REPORT_INTERVAL),
                    'status': existing.get('status', 'pending'),
                })
                return

            # 生成专属 HMAC key
            hmac_key = secrets.token_hex(32)

            # 如果使用一次性 Token，自动审核通过
            auto_approve = token.startswith('tk_')
            initial_status = 'approved' if auto_approve else 'pending'

            node_info = {
                'node_id': node_id,
                'role': payload.get('role', 'unknown'),
                'hostname': payload.get('hostname', ''),
                'capabilities': payload.get('capabilities', []),
                'version': payload.get('version', ''),
                'ip': payload.get('ip', ''),
                'hmac_key': hmac_key,
                'status': initial_status,
                'report_interval': DEFAULT_REPORT_INTERVAL,
                'registered_at': int(time.time()),
                'last_seen': 0,
                'custom_labels': {},
            }

            # 如果有 Token 的 name，也作为 label
            if token.startswith('tk_'):
                tk_info = deploy_tokens.get(token, {})
                node_info['custom_labels']['deploy_name'] = tk_info.get('name', '')
                if not node_info['hostname']:
                    node_info['hostname'] = tk_info.get('name', '')

            nodes[node_id] = node_info
            save_nodes()

            # 消费一次性 Token
            if token.startswith('tk_'):
                consume_deploy_token(token, node_id)

        print('[REGISTER] New node: ' + node_id + ' (role=' + payload.get('role', '') + ', status=' + initial_status + ')')

        self.send_json(200, {
            'hmac_key': hmac_key,
            'report_interval': DEFAULT_REPORT_INTERVAL,
            'status': initial_status,
        })

    # --- Report ---
    def handle_report(self):
        body = self.read_body()
        ts = self.headers.get('X-Timestamp', '')
        sig = self.headers.get('X-Signature', '')
        node_id = self.headers.get('X-Node-ID', '')

        with state_lock:
            node_info = nodes.get(node_id)

        if not node_info:
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
            node_info['last_seen'] = int(time.time())
            if payload.get('version'):
                node_info['version'] = payload['version']
            if payload.get('ip'):
                new_ip = payload['ip']
                old_ip = node_info.get('ip', '')
                node_info['ip'] = new_ip
                # IP 变化时查询地理位置（缓存：同 IP 不重复查）
                if new_ip and new_ip != node_info.get('geo_ip', ''):
                    geo = query_ip_geolocation(new_ip)
                    if geo:
                        node_info['geo'] = geo
                        node_info['geo_ip'] = new_ip
                        print(f'[GEO] {node_info.get("hostname","")}: {new_ip} -> {geo.get("city","")},{geo.get("region","")}'  )

            if role == 'hq':
                hq_state.update(payload)
            elif role == 'branch':
                bid = payload.get('branch_id', payload.get('node_id', 'unknown'))
                branch_states[bid] = payload
            else:
                self.send_json(400, {'error': 'unknown role'})
                return

            save_nodes()
            write_status_files()
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
                    'display_name': info.get('display_name', ''),
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
        self.send_json(200, {'status': 'approved', 'node_id': node_id})

    def handle_reject(self, node_id):
        with state_lock:
            if node_id not in nodes:
                self.send_json(404, {'error': 'node not found'})
                return
            nodes[node_id]['status'] = 'rejected'
            nodes[node_id].setdefault('pending_config', {})['status'] = 'rejected'
            save_nodes()
        self.send_json(200, {'status': 'rejected', 'node_id': node_id})

    def handle_set_peer_alias(self):
        """POST /api/peers/alias — 设置 peer 公钥的显示别名"""
        body = self.read_body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'invalid JSON'})
            return

        peer_key = payload.get('peer_key', '').strip()
        alias = payload.get('alias', '').strip()
        if not peer_key or not alias:
            self.send_json(400, {'error': 'peer_key and alias are required'})
            return

        load_peer_aliases()
        peer_aliases[peer_key] = alias
        save_peer_aliases()
        # 立即刷新 status 文件（前端下次轮询即可获取新别名）
        with state_lock:
            write_status_files()
        print(f'[PEER] Alias set: {peer_key[:16]}... → "{alias}"')
        self.send_json(200, {'status': 'ok', 'peer_key': peer_key, 'alias': alias})

    def handle_get_peer_aliases(self):
        """GET /api/peers/aliases — 获取所有 peer 别名"""
        load_peer_aliases()
        self.send_json(200, {'aliases': peer_aliases})

    def handle_rename_node(self, node_id):
        """POST /api/nodes/{id}/rename — 设置节点自定义显示名称"""
        body = self.read_body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'invalid JSON'})
            return

        display_name = payload.get('display_name', '').strip()
        if not display_name:
            self.send_json(400, {'error': 'display_name is required'})
            return

        with state_lock:
            load_nodes()
            if node_id not in nodes:
                self.send_json(404, {'error': 'node not found'})
                return
            nodes[node_id]['display_name'] = display_name
            save_nodes()

        print(f'[NODE] Renamed {node_id} → "{display_name}"')
        self.send_json(200, {'status': 'ok', 'node_id': node_id, 'display_name': display_name})

    def handle_delete_node(self, node_id):
        with state_lock:
            if node_id not in nodes:
                self.send_json(404, {'error': 'node not found'})
                return
            del nodes[node_id]
            branch_states.pop(node_id, None)
            save_nodes()
            write_status_files()
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

        self.send_json(200, {'status': 'config_queued', 'node_id': node_id})

    # --- Upgrade Management ---
    def handle_upload_upgrade(self):
        """上传新版 Agent 脚本"""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self.send_json(400, {'error': 'empty body'})
            return

        body = self.rfile.read(content_length)
        version = self.headers.get('X-Agent-Version', '')
        if not version:
            self.send_json(400, {'error': 'X-Agent-Version header required'})
            return

        os.makedirs(UPGRADE_DIR, exist_ok=True)
        file_path = os.path.join(UPGRADE_DIR, 'agent_' + version + '.py')
        with open(file_path, 'wb') as f:
            f.write(body)

        sha256 = hashlib.sha256(body).hexdigest()

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
            'active_tokens': sum(1 for t in deploy_tokens.values() if t.get('status') == 'unused'),
        })

    def log_message(self, format, *args):
        pass  # 静默HTTP日志


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # 加载持久化数据
    load_nodes()
    load_tokens()
    load_upgrade_info()

    print('[INFO] VyOmni Aggregator v2.1 starting...')
    print('[INFO] Listening on :' + str(LISTEN_PORT))
    print('[INFO] Data dir: ' + DATA_DIR)
    print('[INFO] Loaded ' + str(len(nodes)) + ' nodes, ' + str(len(deploy_tokens)) + ' tokens')

    server = HTTPServer(('0.0.0.0', LISTEN_PORT), ApiHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[INFO] Shutting down.')
        server.shutdown()


if __name__ == '__main__':
    main()
