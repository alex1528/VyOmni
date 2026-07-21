#!/usr/bin/env python3
"""
VyOmni Branch Agent — 分支轻量采集器
部署在各分支 VyOS 上，采集系统资源（CPU/内存/负载/接口流量）
定期 HTTP POST 到监控平台 Aggregator
"""

import json
import time
import hashlib
import hmac
import urllib.request
import os
import sys

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.conf')

def load_config():
    config = {
        'server_url': 'http://192.168.1.100:9100',
        'report_interval': 10,
        'hmac_key': 'change-me-in-production',
        'hostname': 'Branch-Unknown',
        'role': 'branch',
        'branch_id': 'unknown',
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    config[k.strip()] = v.strip()
    return config

def collect_system():
    """采集系统资源"""
    cpu_percent = 0.0
    mem_percent = 0.0
    load_1m = 0.0
    
    # CPU
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        fields = line.split()
        idle = int(fields[4])
        total = sum(int(x) for x in fields[1:])
        cpu_percent = round((1 - idle / total) * 100, 1) if total > 0 else 0
    except:
        pass
    
    # Memory
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
    
    # Load average
    try:
        with open('/proc/loadavg') as f:
            load_1m = float(f.read().split()[0])
    except:
        pass
    
    return {
        'cpu_percent': cpu_percent,
        'memory_percent': mem_percent,
        'load_1m': load_1m,
    }

def collect_interfaces():
    """采集网络接口流量"""
    interfaces = {}
    try:
        with open('/proc/net/dev') as f:
            lines = f.readlines()[2:]  # skip header
        for line in lines:
            parts = line.split()
            iface = parts[0].rstrip(':')
            if iface in ('lo',):
                continue
            interfaces[iface] = {
                'rx_bytes': int(parts[1]),
                'tx_bytes': int(parts[9]),
            }
    except:
        pass
    return interfaces

def sign_payload(payload, key):
    body = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    ts = str(int(time.time()))
    sig = hmac.new(key.encode(), (ts + body).encode(), hashlib.sha256).hexdigest()
    return body, ts, sig

def report(config, payload):
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
    interval = int(config.get('report_interval', 10))
    print(f'[INFO] VyOmni Branch Agent started. ID: {config["branch_id"]}, Server: {config["server_url"]}')
    
    while True:
        try:
            system = collect_system()
            interfaces = collect_interfaces()
            
            payload = {
                'role': 'branch',
                'hostname': config['hostname'],
                'branch_id': config['branch_id'],
                'timestamp': int(time.time()),
                'system': system,
                'interfaces': interfaces,
            }
            
            ok = report(config, payload)
            status = 'OK' if ok else 'FAIL'
            print(f'[{time.strftime("%H:%M:%S")}] cpu={system["cpu_percent"]}% mem={system["memory_percent"]}% -> {status}')
        except Exception as e:
            print(f'[ERROR] {e}', file=sys.stderr)
        
        time.sleep(interval)

if __name__ == '__main__':
    main()
