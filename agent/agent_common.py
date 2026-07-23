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
AGENT_VERSION = '2.1.3'
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
    """判断是否为私有网络地址（含 CGNAT 100.64.0.0/10）"""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        # RFC1918 + loopback + link-local + CGNAT
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except Exception:
        return True


def get_default_gateway_info():
    """
    获取默认网关接口名和该接口 IP
    返回: (interface_name, interface_ip) 或 (None, None)
    """
    try:
        # 方法1：解析 /proc/net/route
        with open('/proc/net/route') as f:
            lines = f.readlines()[1:]
        default_iface = None
        for line in lines:
            parts = line.strip().split()
            # Destination == 0.0.0.0 且 Flags 包含 UG
            if len(parts) >= 8 and parts[1] == '00000000' and int(parts[3], 16) & 0x2:
                default_iface = parts[0]
                break

        if not default_iface:
            # 方法2：ip route show default
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and 'dev' in result.stdout:
                import re
                m = re.search(r'dev\s+(\S+)', result.stdout)
                if m:
                    default_iface = m.group(1)

        if not default_iface:
            return None, None

        # 获取该接口的 IP
        result = subprocess.run(
            ['ip', '-4', '-o', 'addr', 'show', 'dev', default_iface],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            import re
            match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', result.stdout)
            if match:
                return default_iface, match.group(1)

    except Exception:
        pass
    return None, None


def get_public_ip():
    """
    探测出口公网 IP（通过多个外部服务，超时容忍）
    返回公网 IP 字符串或 None
    """
    import ipaddress

    services = [
        'http://ifconfig.io/ip',
        'http://ip.sb',
        'http://ipecho.net/plain',
        'http://api.ipify.org',
    ]

    for url in services:
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'curl/7.88.1',
                'Accept': 'text/plain',
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode('utf-8', errors='ignore').strip()
                # 有些服务返回额外内容，只取第一行
                ip = raw.split('\n')[0].strip()
                # 验证是合法公网 IP
                addr = ipaddress.ip_address(ip)
                if not addr.is_private:
                    return ip
        except Exception:
            continue
    return None


def get_local_ip():
    """
    获取节点 IP 地址（智能探测）

    逻辑：
    1. 获取默认网关出口接口及其 IP
    2. 如果接口 IP 是私有地址 → 探测出口公网 IP
    3. 返回公网 IP（优先）或私有接口 IP（fallback）
    """
    # 步骤1：获取默认网关接口 IP
    iface, gateway_ip = get_default_gateway_info()

    if not gateway_ip:
        # fallback: UDP socket 连接法（不实际发包，仅获取出口 IP）
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(3)
            s.connect(('223.5.5.5', 53))  # 阿里 DNS，国内可达
            gateway_ip = s.getsockname()[0]
            s.close()
        except Exception:
            gateway_ip = '0.0.0.0'

    # 步骤2：如果是私有地址，尝试探测出口公网 IP
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
    检测并执行远程升级（全自动多文件更新）
    流程：检测新版本 → 下载所有 Agent 文件 → SHA256 校验 → 备份 → 全量替换 → 重启
    返回: True 表示即将重启（调用方应 break 退出循环），False 继续运行
    """
    if not response:
        return False

    upgrade_info = response.get('upgrade_available')
    if not upgrade_info:
        return False

    new_version = upgrade_info.get('version', '')
    if not new_version or new_version == AGENT_VERSION:
        return False

    print(f'[UPGRADE] 发现新版本: {new_version} (当前: {AGENT_VERSION})')

    # 确定需要更新的文件列表
    agent_dir = os.path.dirname(os.path.abspath(__file__))
    # 根据当前运行的主脚本确定角色
    main_script = os.path.basename(sys.argv[0])
    files_to_update = ['agent_common.py', main_script]

    server_url = config['server_url'].rstrip('/')
    os.makedirs(UPGRADE_DIR, exist_ok=True)

    # 阶段1：下载所有文件到临时目录
    downloaded = {}
    print(f'[UPGRADE] 下载 {len(files_to_update)} 个文件...')
    for filename in files_to_update:
        download_url = f'{server_url}/api/deploy/files/{filename}'
        tmp_path = os.path.join(UPGRADE_DIR, filename + '.new')

        try:
            req = urllib.request.Request(download_url, headers={
                'X-Node-ID': credentials.get('node_id', ''),
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()

            # 基础验证：文件不能为空或太小（防止下载到错误页面）
            if len(data) < 100:
                print(f'[UPGRADE] ❌ {filename} 文件过小({len(data)}字节)，跳过升级', file=sys.stderr)
                _cleanup_upgrade_dir()
                return False

            with open(tmp_path, 'wb') as f:
                f.write(data)
            downloaded[filename] = {'path': tmp_path, 'data': data, 'size': len(data)}
            print(f'[UPGRADE]   ✓ {filename} ({len(data)} bytes)')

        except Exception as e:
            print(f'[UPGRADE] ❌ 下载 {filename} 失败: {e}', file=sys.stderr)
            _cleanup_upgrade_dir()
            return False

    # 阶段2：校验 SHA256（如果服务端提供了哈希）
    expected_hash = upgrade_info.get('sha256', '')
    if expected_hash and 'agent_common.py' in downloaded:
        actual_hash = hashlib.sha256(downloaded['agent_common.py']['data']).hexdigest()
        if actual_hash != expected_hash:
            print(f'[UPGRADE] ❌ SHA256 校验失败', file=sys.stderr)
            print(f'  期望: {expected_hash}', file=sys.stderr)
            print(f'  实际: {actual_hash}', file=sys.stderr)
            _cleanup_upgrade_dir()
            return False
        print(f'[UPGRADE]   ✓ SHA256 校验通过')

    # 阶段3：备份当前文件
    print(f'[UPGRADE] 备份当前版本...')
    backup_dir = os.path.join(UPGRADE_DIR, f'backup_{AGENT_VERSION}')
    os.makedirs(backup_dir, exist_ok=True)
    for filename in files_to_update:
        src = os.path.join(agent_dir, filename)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, filename))

    # 阶段4：替换文件
    print(f'[UPGRADE] 替换文件...')
    for filename, info in downloaded.items():
        target = os.path.join(agent_dir, filename)
        try:
            shutil.move(info['path'], target)
            os.chmod(target, 0o755)
            print(f'[UPGRADE]   ✓ {filename} 已更新')
        except Exception as e:
            print(f'[UPGRADE] ❌ 替换 {filename} 失败: {e}，执行回滚', file=sys.stderr)
            _rollback_upgrade(agent_dir, backup_dir, files_to_update)
            return False

    # 阶段5：重启服务
    print(f'[UPGRADE] ✅ 升级完成: {AGENT_VERSION} → {new_version}')
    print(f'[UPGRADE] 正在重启服务...')

    # 使用 os.execv 或 systemctl restart 重启
    # systemctl restart 会杀掉当前进程，所以这之后的代码不会执行
    try:
        subprocess.Popen(
            ['systemctl', 'restart', 'vyomni-agent'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # 如果 systemctl 不可用（非 systemd 环境），用 exec 重启
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            print(f'[UPGRADE] ⚠️ 自动重启失败: {e}，请手动重启服务', file=sys.stderr)

    return True


def _cleanup_upgrade_dir():
    """清理临时下载文件"""
    if os.path.exists(UPGRADE_DIR):
        for f in os.listdir(UPGRADE_DIR):
            if f.endswith('.new'):
                os.remove(os.path.join(UPGRADE_DIR, f))


def _rollback_upgrade(agent_dir, backup_dir, files):
    """升级失败时回滚"""
    print('[UPGRADE] 执行回滚...', file=sys.stderr)
    for filename in files:
        backup_file = os.path.join(backup_dir, filename)
        target = os.path.join(agent_dir, filename)
        if os.path.exists(backup_file):
            shutil.copy2(backup_file, target)
            print(f'[UPGRADE]   回滚: {filename}', file=sys.stderr)



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


