#!/usr/bin/env python3
"""
HQ Tunnel Collector — 总部 WireGuard 隧道状态采集器
运行于总部 VyOS，周期采集 wg show all dump 并输出 status-tunnel.json

功能：
- 解析 wg show all dump 原始输出
- 计算每 peer 的实时速率（rx/tx Mbps）
- 判定在线/离线状态（握手超时阈值）
- 输出标准化 JSON 供前端读取
- 写入采集器心跳时间戳
"""

import json
import os
import subprocess
import time
import psutil
from pathlib import Path

# ==================== 配置 ====================
CONFIG_PATH = os.environ.get('WG_MONITOR_CONFIG', '/etc/wg-monitor/config.json')
OUTPUT_DIR = os.environ.get('WG_MONITOR_OUTPUT', '/var/www/monitor/data')
COLLECT_INTERVAL = int(os.environ.get('WG_MONITOR_INTERVAL', '5'))
HANDSHAKE_TIMEOUT = 180  # 秒，超过此值判定离线

# ==================== 全局状态 ====================
prev_stats = {}  # {peer_pubkey: {'rx': bytes, 'tx': bytes, 'time': timestamp}}


def load_config() -> dict:
    """加载 peer 映射配置"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[WARN] 配置加载失败: {e}, 使用空映射")
        return {"peer_map": {}}


def run_wg_dump() -> str:
    """执行 wg show all dump，获取原始输出"""
    try:
        result = subprocess.run(
            ['wg', 'show', 'all', 'dump'],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[ERROR] wg dump 执行失败: {e}")
        return ""


def parse_wg_dump(raw: str) -> list:
    """
    解析 wg show all dump 输出
    格式：
    接口行: interface private-key public-key listen-port fwmark
    Peer行: interface public-key preshared-key endpoint allowed-ips
            latest-handshake transfer-rx transfer-tx persistent-keepalive
    """
    peers = []
    for line in raw.strip().split('\n'):
        fields = line.split('\t')
        if len(fields) < 8:
            continue
        # Peer 行有 9 个字段
        if len(fields) == 9:
            peers.append({
                'interface': fields[0],
                'peer_pubkey': fields[1],
                'endpoint': fields[3] if fields[3] != '(none)' else None,
                'allowed_ips': fields[4],
                'last_handshake_ts': int(fields[5]) if fields[5] != '0' else 0,
                'rx_bytes': int(fields[6]),
                'tx_bytes': int(fields[7]),
            })
    return peers


def calc_rates(peer_pubkey: str, rx_bytes: int, tx_bytes: int) -> tuple:
    """计算实时速率 (rx_mbps, tx_mbps)"""
    global prev_stats
    now = time.time()

    if peer_pubkey in prev_stats:
        prev = prev_stats[peer_pubkey]
        dt = now - prev['time']
        if dt > 0:
            rx_mbps = round((rx_bytes - prev['rx']) * 8 / dt / 1_000_000, 2)
            tx_mbps = round((tx_bytes - prev['tx']) * 8 / dt / 1_000_000, 2)
            # 防止计数器重置导致负值
            rx_mbps = max(0.0, rx_mbps)
            tx_mbps = max(0.0, tx_mbps)
        else:
            rx_mbps, tx_mbps = 0.0, 0.0
    else:
        rx_mbps, tx_mbps = 0.0, 0.0

    prev_stats[peer_pubkey] = {'rx': rx_bytes, 'tx': tx_bytes, 'time': now}
    return rx_mbps, tx_mbps


def get_system_info() -> dict:
    """获取总部系统资源信息"""
    hostname = os.uname().nodename if hasattr(os, 'uname') else 'unknown'
    cpu = round(psutil.cpu_percent(interval=0), 1)
    mem = round(psutil.virtual_memory().percent, 1)
    return {
        'hostname': hostname,
        'cpu_percent': cpu,
        'memory_percent': mem,
    }


def collect_once(config: dict) -> dict:
    """执行一次完整采集"""
    raw = run_wg_dump()
    if not raw:
        return None

    parsed_peers = parse_wg_dump(raw)
    now = time.time()
    peer_map = config.get('peer_map', {})

    peers_out = []
    total_rx = 0.0
    total_tx = 0.0
    online_count = 0

    for p in parsed_peers:
        pubkey = p['peer_pubkey']
        rx_mbps, tx_mbps = calc_rates(pubkey, p['rx_bytes'], p['tx_bytes'])
        total_rx += rx_mbps
        total_tx += tx_mbps

        # 握手时间判定
        if p['last_handshake_ts'] > 0:
            handshake_ago = int(now - p['last_handshake_ts'])
        else:
            handshake_ago = 99999

        is_online = handshake_ago < HANDSHAKE_TIMEOUT
        if is_online:
            online_count += 1

        # 从配置获取别名和 branch_id
        mapping = peer_map.get(pubkey, {})
        name = mapping.get('name', pubkey[:8] + '...')
        branch_id = mapping.get('branch_id', '')

        peers_out.append({
            'interface': p['interface'],
            'peer': pubkey,
            'branch_id': branch_id,
            'name': name,
            'status': 'online' if is_online else 'offline',
            'rx_rate_mbps': rx_mbps,
            'tx_rate_mbps': tx_mbps,
            'last_handshake_seconds_ago': handshake_ago,
            'endpoint': p['endpoint'] or '',
        })

    sys_info = get_system_info()
    sys_info['tunnel_active'] = online_count
    sys_info['tunnel_total'] = len(parsed_peers)

    return {
        'updated_at': int(now),
        'collector_heartbeat': int(now),
        'system': sys_info,
        'totals': {
            'rx_mbps': round(total_rx, 2),
            'tx_mbps': round(total_tx, 2),
        },
        'peers': peers_out,
    }


def write_output(data: dict):
    """原子写入 JSON 文件"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, 'status-tunnel.json')
    tmp_path = output_path + '.tmp'

    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, output_path)


def main():
    """主循环"""
    print(f"[INFO] HQ Tunnel Collector 启动")
    print(f"[INFO] 配置文件: {CONFIG_PATH}")
    print(f"[INFO] 输出目录: {OUTPUT_DIR}")
    print(f"[INFO] 采集间隔: {COLLECT_INTERVAL}s")

    config = load_config()

    while True:
        try:
            data = collect_once(config)
            if data:
                write_output(data)
                active = data['system']['tunnel_active']
                total = data['system']['tunnel_total']
                print(f"[OK] 采集完成 - 隧道 {active}/{total} 在线, "
                      f"RX={data['totals']['rx_mbps']}Mbps TX={data['totals']['tx_mbps']}Mbps")
            else:
                print("[WARN] 本轮采集无数据")
        except Exception as e:
            print(f"[ERROR] 采集异常: {e}")

        time.sleep(COLLECT_INTERVAL)


if __name__ == '__main__':
    main()
