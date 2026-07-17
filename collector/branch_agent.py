#!/usr/bin/env python3
"""
Branch Agent — 分支资源上报代理
运行于各分支 VyOS，周期采集本机资源并上报总部聚合器

功能：
- 采集 CPU、内存、负载、关键接口流量
- HMAC-SHA256 签名防伪造
- 自动重试与错误容忍
"""

import hashlib
import hmac
import json
import os
import time
import urllib.request
import urllib.error
import psutil

# ==================== 配置 ====================
AGENT_CONFIG_PATH = os.environ.get('WG_AGENT_CONFIG', '/etc/wg-monitor/agent.conf')

# 默认配置（可被配置文件覆盖）
DEFAULT_CONFIG = {
    'branch_id': 'branch-default',
    'secret': '',
    'hq_endpoint': 'http://10.10.0.1:8080/monitor/api/report',
    'report_interval': 5,
    'interfaces': ['eth0', 'eth1', 'wg0'],  # 监控的接口列表
}

# ==================== 全局状态 ====================
prev_net = {}  # {iface: {'rx': bytes, 'tx': bytes, 'time': ts}}


def load_agent_config() -> dict:
    """加载分支 Agent 配置"""
    config = DEFAULT_CONFIG.copy()
    try:
        with open(AGENT_CONFIG_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#') and not line.startswith('['):
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key == 'report_interval':
                        config[key] = int(value)
                    elif key == 'interfaces':
                        config[key] = [x.strip() for x in value.split(',')]
                    else:
                        config[key] = value
    except FileNotFoundError:
        print(f"[WARN] 配置文件不存在: {AGENT_CONFIG_PATH}，使用默认配置")
    return config


def collect_system() -> dict:
    """采集系统资源"""
    cpu = round(psutil.cpu_percent(interval=0.5), 1)
    mem = round(psutil.virtual_memory().percent, 1)
    load_1, load_5, load_15 = os.getloadavg() if hasattr(os, 'getloadavg') else (0, 0, 0)
    return {
        'cpu_percent': cpu,
        'memory_percent': mem,
        'load_1m': round(load_1, 2),
        'load_5m': round(load_5, 2),
        'load_15m': round(load_15, 2),
    }


def collect_interfaces(iface_list: list) -> dict:
    """采集接口流量（计算实时速率）"""
    global prev_net
    now = time.time()
    net_io = psutil.net_io_counters(pernic=True)
    result = {}

    for iface in iface_list:
        if iface not in net_io:
            continue
        stats = net_io[iface]
        rx_bytes = stats.bytes_recv
        tx_bytes = stats.bytes_sent

        if iface in prev_net:
            prev = prev_net[iface]
            dt = now - prev['time']
            if dt > 0:
                rx_mbps = round(max(0, (rx_bytes - prev['rx'])) * 8 / dt / 1_000_000, 2)
                tx_mbps = round(max(0, (tx_bytes - prev['tx'])) * 8 / dt / 1_000_000, 2)
            else:
                rx_mbps, tx_mbps = 0.0, 0.0
        else:
            rx_mbps, tx_mbps = 0.0, 0.0

        prev_net[iface] = {'rx': rx_bytes, 'tx': tx_bytes, 'time': now}
        result[iface] = {'rx_mbps': rx_mbps, 'tx_mbps': tx_mbps}

    return result


def sign_payload(payload: dict, branch_id: str, secret: str) -> dict:
    """HMAC-SHA256 签名"""
    timestamp = str(int(time.time()))
    body = json.dumps(payload, sort_keys=True, separators=(',', ':'))

    sign_string = f"{branch_id}\n{timestamp}\n{body}"
    signature = hmac.new(
        secret.encode('utf-8'),
        sign_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return {
        'headers': {
            'X-Branch-ID': branch_id,
            'X-Timestamp': timestamp,
            'X-Signature': signature,
            'Content-Type': 'application/json',
        },
        'body': body,
    }


def send_report(endpoint: str, signed: dict) -> bool:
    """发送上报数据到总部聚合器"""
    try:
        req = urllib.request.Request(
            endpoint,
            data=signed['body'].encode('utf-8'),
            headers=signed['headers'],
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return True
            else:
                print(f"[WARN] 上报返回非200: {resp.status}")
                return False
    except urllib.error.URLError as e:
        print(f"[ERROR] 上报失败: {e}")
        return False
    except Exception as e:
        print(f"[ERROR] 上报异常: {e}")
        return False


def main():
    """主循环"""
    config = load_agent_config()
    branch_id = config['branch_id']
    secret = config['secret']
    endpoint = config['hq_endpoint']
    interval = config['report_interval']
    ifaces = config['interfaces']

    print(f"[INFO] Branch Agent 启动 - ID: {branch_id}")
    print(f"[INFO] 上报地址: {endpoint}")
    print(f"[INFO] 上报间隔: {interval}s")
    print(f"[INFO] 监控接口: {ifaces}")

    if not secret:
        print("[WARN] 未配置 secret，上报将无签名验证!")

    # 首次采集（预热速率计算）
    collect_interfaces(ifaces)
    time.sleep(1)

    consecutive_failures = 0

    while True:
        try:
            sys_data = collect_system()
            iface_data = collect_interfaces(ifaces)

            payload = {
                'branch_id': branch_id,
                'reported_at': int(time.time()),
                **sys_data,
                'interfaces': iface_data,
            }

            if secret:
                signed = sign_payload(payload, branch_id, secret)
            else:
                signed = {
                    'headers': {'Content-Type': 'application/json', 'X-Branch-ID': branch_id},
                    'body': json.dumps(payload, sort_keys=True, separators=(',', ':')),
                }

            success = send_report(endpoint, signed)
            if success:
                consecutive_failures = 0
                print(f"[OK] 上报成功 - CPU={sys_data['cpu_percent']}% MEM={sys_data['memory_percent']}%")
            else:
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    print(f"[ALERT] 连续 {consecutive_failures} 次上报失败!")

        except Exception as e:
            print(f"[ERROR] 采集/上报异常: {e}")
            consecutive_failures += 1

        time.sleep(interval)


if __name__ == '__main__':
    main()
