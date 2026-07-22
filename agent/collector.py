#!/usr/bin/env python3
"""
VyOmni HQ Agent — 总部轻量采集器（v2.0）
自注册 + 动态配置 + 远程升级 + WireGuard 隧道状态采集
"""

import json
import time
import subprocess
import os
import sys

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_common import (
    load_config, register_node, report_data, apply_dynamic_config,
    check_and_upgrade, collect_system, AGENT_VERSION
)


def collect_wg_dump():
    """采集 wg show all dump"""
    try:
        result = subprocess.run(
            ['wg', 'show', 'all', 'dump'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return []

        peers = []
        lines = result.stdout.strip().split('\n')
        for line in lines:
            fields = line.split('\t')
            if len(fields) >= 8 and fields[3] != '(none)':
                peers.append({
                    'interface': fields[0],
                    'public_key': fields[1][:12] + '...',
                    'endpoint': fields[3],
                    'allowed_ips': fields[4],
                    'latest_handshake': int(fields[5]) if fields[5] != '0' else 0,
                    'transfer_rx': int(fields[6]),
                    'transfer_tx': int(fields[7]),
                    'keepalive': int(fields[8]) if len(fields) > 8 and fields[8] != 'off' else 0,
                })
        return peers
    except Exception as e:
        print(f'[ERROR] wg dump failed: {e}', file=sys.stderr)
        return []


def main():
    print(f'[INFO] VyOmni HQ Agent v{AGENT_VERSION} starting...')

    # 加载配置
    config = load_config()
    print(f'[INFO] Server: {config["server_url"]}')

    # 自注册
    credentials = None
    while credentials is None:
        credentials = register_node(config, capabilities=['system', 'wireguard'])
        if credentials is None:
            print('[WARN] Registration failed, retrying in 10s...', file=sys.stderr)
            time.sleep(10)

    # 主循环
    interval = credentials.get('report_interval', 5)
    print(f'[INFO] Agent active. node_id={credentials["node_id"]}, interval={interval}s')

    while True:
        try:
            # 根据 capabilities 决定采集项
            caps = credentials.get('capabilities', ['system', 'wireguard'])

            payload = {
                'node_id': credentials['node_id'],
                'role': 'hq',
                'hostname': credentials['hostname'],
                'timestamp': int(time.time()),
                'version': AGENT_VERSION,
            }

            if 'system' in caps:
                payload['system'] = collect_system()

            if 'wireguard' in caps:
                payload['peers'] = collect_wg_dump()

            # 上报
            response = report_data(config, credentials, payload)

            # 处理响应
            if response:
                status = 'OK'
                # 应用动态配置
                credentials = apply_dynamic_config(credentials, response)
                interval = credentials.get('report_interval', 5)

                # 检查升级
                if check_and_upgrade(config, credentials, response):
                    break  # 升级后退出，由 systemd 重启
            else:
                status = 'FAIL'

            sys_info = payload.get('system', {})
            peer_count = len(payload.get('peers', []))
            print(f'[{time.strftime("%H:%M:%S")}] peers={peer_count} '
                  f'cpu={sys_info.get("cpu_percent", 0)}% '
                  f'mem={sys_info.get("memory_percent", 0)}% -> {status}')

        except Exception as e:
            print(f'[ERROR] {e}', file=sys.stderr)

        time.sleep(interval)


if __name__ == '__main__':
    main()
