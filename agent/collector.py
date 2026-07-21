#!/usr/bin/env python3
"""
VyOmni HQ Agent — 总部轻量采集器
部署在总部 VyOS 上，采集 WireGuard 隧道状态 + 系统资源
定期 HTTP POST 到监控平台 Aggregator
"""

import json
import time
import hashlib
import hmac
import subprocess
import urllib.request
import os
import sys

# === 配置 ===
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.conf')

def load_config():
    """加载配置文件"""
    config = {
        'server_url': 'http://192.168.1.100:9100',
        'report_interval': 5,
        'hmac_key': 'change-me-in-production',
        'hostname': 'HQ-VyOS',
        'role': 'hq',
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    config[k.strip()] = v.strip()
    return config

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
                # peer line: interface, public_key, preshared_key, endpoint,
                #            allowed_ips, latest_handshake, rx, tx
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

def collect_system():
    """采集系统资源（不依赖 psutil）"""
    cpu_percent = 0.0
    mem_percent = 0.0
    
    # CPU: /proc/stat
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        fields = line.split()
        idle = int(fields[4])
        total = sum(int(x) for x in fields[1:])
        # 简单快照（非差值），后续优化
        cpu_percent = round((1 - idle / total) * 100, 1) if total > 0 else 0
    except:
        pass
    
    # Memory: /proc/meminfo
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(':')] = int(parts[1])
        total = info.get('MemTotal', 1)
        avail = info.get('MemAvailable', 0)
        mem_percent = round((1 - avail / total) * 100, 1)
    except:
        pass
    
    return {'cpu_percent': cpu_percent, 'memory_percent': mem_percent}

def sign_payload(payload, key):
    """HMAC-SHA256 签名"""
    body = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    ts = str(int(time.time()))
    sig = hmac.new(key.encode(), (ts + body).encode(), hashlib.sha256).hexdigest()
    return body, ts, sig

def report(config, payload):
    """上报数据到聚合器"""
    body, ts, sig = sign_payload(payload, config['hmac_key'])
    url = config['server_url'].rstrip('/') + '/report'
    
    req = urllib.request.Request(
        url,
        data=body.encode(),
        headers={
            'Content-Type': 'application/json',
            'X-Timestamp': ts,
            'X-Signature': sig,
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f'[WARN] Report failed: {e}', file=sys.stderr)
        return False

def main():
    config = load_config()
    interval = int(config.get('report_interval', 5))
    print(f'[INFO] VyOmni HQ Agent started. Server: {config["server_url"]}, Interval: {interval}s')
    
    while True:
        try:
            peers = collect_wg_dump()
            system = collect_system()
            
            payload = {
                'role': 'hq',
                'hostname': config['hostname'],
                'timestamp': int(time.time()),
                'system': system,
                'peers': peers,
            }
            
            ok = report(config, payload)
            status = 'OK' if ok else 'FAIL'
            print(f'[{time.strftime("%H:%M:%S")}] peers={len(peers)} cpu={system["cpu_percent"]}% mem={system["memory_percent"]}% -> {status}')
        except Exception as e:
            print(f'[ERROR] {e}', file=sys.stderr)
        
        time.sleep(interval)

if __name__ == '__main__':
    main()
