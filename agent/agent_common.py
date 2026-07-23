#!/usr/bin/env python3
"""
VyOmni Agent Common — 公共逻辑模块
提供：节点自注册、HMAC签名上报、动态配置应用、远程升级
兼容一次性 tk_ Token 格式
"""

import json
import time
import hashlib
import hmac
import os
import sys
import uuid
import subprocess
import urllib.request
import urllib.error
import socket
import shutil

# === 常量 ===
AGENT_VERSION = '2.1.0'
CREDENTIAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.credentials.json')
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.conf')
UPGRADE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.upgrades')


# === 配置加载 ===
def load_config():
    """加载极简配置文件（server_url + register_token）"""
    config = {
        'server_url': 'http://192.168.1.100:8080',
        'register_token': 'vyomni-2025',
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    config[k.strip()] = v.strip()
    return config


# === 节点 ID 生成 ===
def get_node_id():
    """基于 hostname + MAC hash 生成唯一 node_id"""
    hostname = socket.gethostname()
    # 获取 MAC 地址
    mac = uuid.getnode()
    mac_str = ':'.join(f'{(mac >> i) & 0xff:02x}' for i in range(0, 48, 8))
    raw = f'{hostname}-{mac_str}'
    node_hash = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f'{hostname}-{node_hash}'


# === 角色自动检测 ===
def detect_role():
    """检测本机角色：有 wg 接口 → hq，无 → branch"""
    try:
        result = subprocess.run(
            ['ip', 'link', 'show', 'type', 'wireguard'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return 'hq'
    except Exception:
        # 备选方案：检查 wg 命令
        try:
            result = subprocess.run(
                ['wg', 'show', 'interfaces'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return 'hq'
        except Exception:
            pass
    return 'branch'


# === 获取本机 IP ===
def is_private_ip(ip):
    """判断是否为私有网络地址"""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private
    except Exception:
        return True


def get_gateway_interface_ip():
    """获取与默认网关一致的接口 IP"""
    try:
        # Linux: 解析 /proc/net/route 获取默认网关对应接口
        with open('/proc/net/route') as f:
            lines = f.readlines()[1:]
        default_iface = None
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 3 and parts[1] == '00000000':  # 默认路由
                default_iface = parts[0]
                break

        if not default_iface:
            return None

        # 获取该接口的 IP（通过 ip addr 或 /proc）
        result = subprocess.run(
            ['ip', '-4', 'addr', 'show', 'dev', default_iface],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            import re
            match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)
    except Exception:
        pass
    return None


def get_public_ip():
    """探测出口公网 IP（通过外部服务）"""
    # 多个备选探测服务
    services = [
        'http://ifconfig.me/ip',
        'http://ip.sb',
        'http://ipecho.net/plain',
        'http://checkip.amazonaws.com',
    ]
    for url in services:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                ip = resp.read().decode().strip()
                # 验证是合法 IP
                import ipaddress
                ipaddress.ip_address(ip)
                return ip
        except Exception:
            continue
    return None


def get_local_ip():
    """
    获取节点 IP 地址（智能探测）
    优先级：
    1. 获取默认网关接口 IP
    2. 如果是私有地址 → 探测出口公网 IP
    3. 返回格式：公网IP（如有）或 私有IP
    """
    # 步骤1：获取默认网关接口 IP
    gateway_ip = get_gateway_interface_ip()

    if not gateway_ip:
        # fallback: socket 连接法
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            gateway_ip = s.getsockname()[0]
            s.close()
        except Exception:
            gateway_ip = '0.0.0.0'

    # 步骤2：如果是私有地址，探测公网 IP
    if is_private_ip(gateway_ip):
        public_ip = get_public_ip()
        if public_ip:
            return public_ip

    return gateway_ip


# === 凭证管理 ===
def load_credentials():
    """加载本地保存的注册凭证"""
    if os.path.exists(CREDENTIAL_FILE):
        try:
            with open(CREDENTIAL_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def save_credentials(creds):
    """保存注册凭证到本地"""
    with open(CREDENTIAL_FILE, 'w') as f:
        json.dump(creds, f, indent=2)
    os.chmod(CREDENTIAL_FILE, 0o600)


# === 节点注册 ===
def register_node(config, role=None, capabilities=None):
    """
    首次启动注册节点
    支持传统固定 Token 和一次性 tk_ Token
    返回: credentials dict 或 None
    """
    # 检查是否已注册
    creds = load_credentials()
    if creds and creds.get('node_id') and creds.get('hmac_key'):
        print(f'[INFO] Already registered as {creds["node_id"]} (status: {creds.get("status", "unknown")})')
        return creds

    # 准备注册数据
    node_id = get_node_id()
    hostname = socket.gethostname()

    # 检测 register_token 的类型来决定角色
    register_token = config['register_token']

    # 角色优先由调用方指定，fallback 到 detect_role
    if role is None:
        role = detect_role()

    if capabilities is None:
        capabilities = ['system']
        if role == 'hq':
            capabilities.append('wireguard')
        else:
            capabilities.append('interfaces')

    payload = {
        'node_id': node_id,
        'role': role,
        'hostname': hostname,
        'capabilities': capabilities,
        'version': AGENT_VERSION,
        'ip': get_local_ip(),
        'register_token': register_token,
    }

    url = config['server_url'].rstrip('/') + '/register'
    body = json.dumps(payload, separators=(',', ':'))

    req = urllib.request.Request(
        url,
        data=body.encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                result = json.loads(resp.read().decode())
                creds = {
                    'node_id': node_id,
                    'role': role,
                    'hostname': hostname,
                    'hmac_key': result.get('hmac_key', ''),
                    'report_interval': result.get('report_interval', 10),
                    'status': result.get('status', 'pending'),
                    'registered_at': int(time.time()),
                    'token_type': 'onetime' if register_token.startswith('tk_') else 'static',
                }
                save_credentials(creds)

                # 一次性 Token 注册成功后，Token 已由服务端作废
                # 后续通信使用专属 hmac_key
                if register_token.startswith('tk_'):
                    print(f'[INFO] Registered with one-time token: node_id={node_id}, role={role}, status={creds["status"]}')
                    print(f'[INFO] Token consumed. Future communication uses HMAC key.')
                else:
                    print(f'[INFO] Registered successfully: node_id={node_id}, role={role}, status={creds["status"]}')

                return creds
            else:
                print(f'[ERROR] Registration failed: HTTP {resp.status}', file=sys.stderr)
                return None
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ''
        print(f'[ERROR] Registration failed: HTTP {e.code} - {body_text}', file=sys.stderr)
        return None
    except Exception as e:
        print(f'[ERROR] Registration failed: {e}', file=sys.stderr)
        return None


# === HMAC 签名 ===
def sign_payload(payload, key):
    """HMAC-SHA256 签名"""
    body = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    ts = str(int(time.time()))
    sig = hmac.new(key.encode(), (ts + body).encode(), hashlib.sha256).hexdigest()
    return body, ts, sig


# === 数据上报 ===
def report_data(config, credentials, payload):
    """
    上报数据到聚合器
    返回: 响应 dict（含动态配置+升级通知）或 None
    """
    body, ts, sig = sign_payload(payload, credentials['hmac_key'])
    url = config['server_url'].rstrip('/') + '/report'

    req = urllib.request.Request(
        url,
        data=body.encode(),
        headers={
            'Content-Type': 'application/json',
            'X-Timestamp': ts,
            'X-Signature': sig,
            'X-Node-ID': credentials['node_id'],
        },
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                resp_body = resp.read().decode()
                try:
                    return json.loads(resp_body)
                except json.JSONDecodeError:
                    return {'status': 'ok'}
            return None
    except Exception as e:
        print(f'[WARN] Report failed: {e}', file=sys.stderr)
        return None


# === 动态配置应用 ===
def apply_dynamic_config(credentials, response):
    """
    从 report 响应中提取并应用动态配置
    返回更新后的 credentials
    """
    if not response:
        return credentials

    config_update = response.get('config_update')
    if not config_update:
        return credentials

    changed = False

    if 'report_interval' in config_update:
        new_interval = int(config_update['report_interval'])
        if new_interval != credentials.get('report_interval'):
            credentials['report_interval'] = new_interval
            print(f'[CONFIG] report_interval updated to {new_interval}s')
            changed = True

    if 'capabilities' in config_update:
        credentials['capabilities'] = config_update['capabilities']
        print(f'[CONFIG] capabilities updated: {config_update["capabilities"]}')
        changed = True

    if 'custom_labels' in config_update:
        credentials['custom_labels'] = config_update['custom_labels']
        print(f'[CONFIG] custom_labels updated: {config_update["custom_labels"]}')
        changed = True

    if 'status' in config_update:
        credentials['status'] = config_update['status']
        print(f'[CONFIG] status updated to {config_update["status"]}')
        changed = True

    if changed:
        save_credentials(credentials)

    return credentials


# === 远程升级 ===
def check_and_upgrade(config, credentials, response):
    """
    检测并执行远程升级
    返回: True 表示需要重启, False 继续运行
    """
    if not response:
        return False

    upgrade_info = response.get('upgrade_available')
    if not upgrade_info:
        return False

    new_version = upgrade_info.get('version', '')
    sha256_expected = upgrade_info.get('sha256', '')

    if not new_version or new_version == AGENT_VERSION:
        return False

    print(f'[UPGRADE] New version available: {new_version} (current: {AGENT_VERSION})')

    # 下载新版本
    try:
        download_url = config['server_url'].rstrip('/') + '/api/upgrade/latest'
        os.makedirs(UPGRADE_DIR, exist_ok=True)
        tmp_path = os.path.join(UPGRADE_DIR, f'agent_{new_version}.py.tmp')

        req = urllib.request.Request(
            download_url,
            headers={'X-Node-ID': credentials['node_id']},
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()

        with open(tmp_path, 'wb') as f:
            f.write(data)

        # 校验 SHA256
        actual_hash = hashlib.sha256(data).hexdigest()
        if sha256_expected and actual_hash != sha256_expected:
            print(f'[UPGRADE] SHA256 mismatch! Expected: {sha256_expected}, Got: {actual_hash}', file=sys.stderr)
            os.remove(tmp_path)
            return False

        # 确定当前脚本路径
        current_script = os.path.abspath(sys.argv[0])
        bak_path = current_script + '.bak'

        # 备份当前版本
        if os.path.exists(current_script):
            shutil.copy2(current_script, bak_path)
            print(f'[UPGRADE] Backed up current version to {bak_path}')

        # 替换
        shutil.move(tmp_path, current_script)
        os.chmod(current_script, 0o755)
        print(f'[UPGRADE] Upgraded to version {new_version}')

        # 重启 systemd 服务
        service_name = 'vyomni-agent'
        try:
            subprocess.run(['systemctl', 'restart', service_name], timeout=10)
            print(f'[UPGRADE] Service {service_name} restarted')
        except Exception as e:
            print(f'[UPGRADE] Could not restart service: {e}. Manual restart required.', file=sys.stderr)

        return True

    except Exception as e:
        print(f'[UPGRADE] Upgrade failed: {e}', file=sys.stderr)
        # 回滚
        try:
            current_script = os.path.abspath(sys.argv[0])
            bak_path = current_script + '.bak'
            if os.path.exists(bak_path):
                shutil.move(bak_path, current_script)
                print('[UPGRADE] Rolled back to previous version')
        except Exception as rb_err:
            print(f'[UPGRADE] Rollback failed: {rb_err}', file=sys.stderr)
        return False


# === 系统采集（公共） ===
def collect_system():
    """采集系统资源（CPU/内存/负载）— CPU 使用差值法计算瞬时值"""
    global _prev_cpu_idle, _prev_cpu_total

    cpu_percent = 0.0
    mem_percent = 0.0
    load_1m = 0.0
    load_5m = 0.0
    load_15m = 0.0

    # CPU: /proc/stat 差值法
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        fields = line.split()
        idle = int(fields[4])
        total = sum(int(x) for x in fields[1:])

        if _prev_cpu_total > 0:
            d_idle = idle - _prev_cpu_idle
            d_total = total - _prev_cpu_total
            if d_total > 0:
                cpu_percent = round((1 - d_idle / d_total) * 100, 1)
                cpu_percent = max(0.0, min(100.0, cpu_percent))

        _prev_cpu_idle = idle
        _prev_cpu_total = total
    except Exception:
        pass

    # Memory: /proc/meminfo
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(':')] = int(parts[1])
        total_mem = info.get('MemTotal', 1)
        avail = info.get('MemAvailable', 0)
        mem_percent = round((1 - avail / total_mem) * 100, 1)
    except Exception:
        pass

    # Load average
    try:
        with open('/proc/loadavg') as f:
            parts = f.read().split()
            load_1m = float(parts[0])
            load_5m = float(parts[1])
            load_15m = float(parts[2])
    except Exception:
        pass

    return {
        'cpu_percent': cpu_percent,
        'memory_percent': mem_percent,
        'load_1m': load_1m,
        'load_5m': load_5m,
        'load_15m': load_15m,
    }


