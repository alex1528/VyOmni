#!/usr/bin/env python3
"""
VyOmni Branch Agent — 分支轻量采集器（v2.0）
自注册 + 动态配置 + 远程升级 + 系统资源/网络接口采集
"""

import json
import time
import os
import sys

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_common import (
    load_config, register_node, report_data, apply_dynamic_config,
    check_and_upgrade, collect_system, get_local_ip, AGENT_VERSION
)


def collect_interfaces():
    """采集网络接口流量（仅匹配 eth* 和 wg* 开头的接口）"""
    interfaces = {}
    try:
        with open('/proc/net/dev') as f:
            lines = f.readlines()[2:]  # skip header
        for line in lines:
            parts = line.split()
            iface = parts[0].rstrip(':')
            # 仅采集 eth 和 wg 开头的接口
            if not (iface.startswith('eth') or iface.startswith('wg')):
                continue
            interfaces[iface] = {
                'rx_bytes': int(parts[1]),
                'tx_bytes': int(parts[9]),
            }
    except Exception:
        pass
    return interfaces


def collect_wg_peers():
    """采集本节点 WireGuard peers 完整信息（分支自身视角）"""
    import subprocess
    peers_info = []
    try:
        result = subprocess.run(
            ['wg', 'show', 'all', 'dump'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return peers_info

        for line in result.stdout.strip().split('\n'):
            fields = line.split('\t')
            # peer 行有 9 个字段
            if len(fields) >= 8 and fields[3] != '(none)':
                peers_info.append({
                    'interface': fields[0],
                    'peer_key': fields[1],
                    'endpoint': fields[3],
                    'allowed_ips': fields[4],
                    'latest_handshake': int(fields[5]) if fields[5] != '0' else 0,
                })
    except Exception:
        pass
    return peers_info


def main():
    print(f'[INFO] VyOmni Branch Agent v{AGENT_VERSION} starting...')

    # 加载配置
    config = load_config()
    print(f'[INFO] Server: {config["server_url"]}')

    # 自注册
    credentials = None
    while credentials is None:
        credentials = register_node(config, role='branch', capabilities=['system', 'interfaces'])
        if credentials is None:
            print('[WARN] Registration failed, retrying in 10s...', file=sys.stderr)
            time.sleep(10)

    # 主循环
    interval = credentials.get('report_interval', 10)
    print(f'[INFO] Agent active. node_id={credentials["node_id"]}, interval={interval}s')

    while True:
        try:
            # 根据 capabilities 决定采集项
            caps = credentials.get('capabilities', ['system', 'interfaces'])

            payload = {
                'node_id': credentials['node_id'],
                'role': 'branch',
                'hostname': credentials['hostname'],
                'branch_id': credentials['node_id'],  # 兼容旧版
                'timestamp': int(time.time()),
                'version': AGENT_VERSION,
                'ip': get_local_ip(),
            }

            if 'system' in caps:
                payload['system'] = collect_system()

            if 'interfaces' in caps:
                payload['interfaces'] = collect_interfaces()

            # 采集本节点 wg peers 信息（allowed_ips）
            wg_peers = collect_wg_peers()
            if wg_peers:
                payload['wg_peers'] = wg_peers

            # 上报
            response = report_data(config, credentials, payload)

            # 处理响应
            if response:
                status = 'OK'
                # 应用动态配置
                credentials = apply_dynamic_config(credentials, response)
                interval = credentials.get('report_interval', 10)

                # 检查升级
                if check_and_upgrade(config, credentials, response):
                    break  # 升级后退出，由 systemd 重启
            else:
                status = 'FAIL'

            sys_info = payload.get('system', {})
            print(f'[{time.strftime("%H:%M:%S")}] '
                  f'cpu={sys_info.get("cpu_percent", 0)}% '
                  f'mem={sys_info.get("memory_percent", 0)}% -> {status}')

        except Exception as e:
            print(f'[ERROR] {e}', file=sys.stderr)

        time.sleep(interval)


if __name__ == '__main__':
    main()
