#!/usr/bin/env python3
"""
VyOmni Alerter — 告警服务
监控状态文件变化，触发条件时推送告警到钉钉/企微/Webhook
"""

import json
import time
import os
import sys
import urllib.request

DATA_DIR = os.environ.get('DATA_DIR', '/data')
ALERT_CONFIG_PATH = os.environ.get('ALERT_CONFIG_PATH', '/app/alert.json')
CHECK_INTERVAL = 10  # 秒

def load_alert_config():
    if os.path.exists(ALERT_CONFIG_PATH):
        with open(ALERT_CONFIG_PATH) as f:
            return json.load(f)
    return {'enabled': False, 'channels': []}

def check_tunnel_alerts(config):
    """检查隧道告警条件"""
    alerts = []
    status_file = os.path.join(DATA_DIR, 'status-tunnel.json')
    
    if not os.path.exists(status_file):
        return alerts
    
    try:
        with open(status_file) as f:
            data = json.load(f)
    except:
        return alerts
    
    now = int(time.time())
    
    # 告警：采集器心跳超时
    heartbeat = data.get('collector_heartbeat', 0)
    if heartbeat > 0 and now - heartbeat > 60:
        alerts.append({
            'level': 'warning',
            'title': '总部采集器心跳超时',
            'detail': f'最后心跳: {now - heartbeat}秒前',
        })
    
    # 告警：peer 握手超时
    for peer in data.get('peers', []):
        handshake = peer.get('latest_handshake', 0)
        if handshake > 0 and now - handshake > 180:
            alerts.append({
                'level': 'critical',
                'title': f'隧道离线: {peer.get("endpoint", "unknown")}',
                'detail': f'最后握手: {now - handshake}秒前',
            })
    
    return alerts

def check_branch_alerts(config):
    """检查分支告警条件"""
    alerts = []
    status_file = os.path.join(DATA_DIR, 'status-branches.json')
    
    if not os.path.exists(status_file):
        return alerts
    
    try:
        with open(status_file) as f:
            data = json.load(f)
    except:
        return alerts
    
    now = int(time.time())
    
    for branch in data.get('branches', []):
        if not branch.get('online', True):
            alerts.append({
                'level': 'warning',
                'title': f'分支离线: {branch.get("hostname", branch.get("branch_id"))}',
                'detail': f'最后上报: {now - branch.get("last_seen", 0)}秒前',
            })
        
        cpu = branch.get('system', {}).get('cpu_percent', 0)
        if cpu > 90:
            alerts.append({
                'level': 'warning',
                'title': f'分支 CPU 过高: {branch.get("hostname")}',
                'detail': f'CPU: {cpu}%',
            })
    
    return alerts

def send_webhook(url, alerts):
    """发送告警到 Webhook"""
    text = '\n'.join([f'[{a["level"].upper()}] {a["title"]} - {a["detail"]}' for a in alerts])
    payload = json.dumps({'msgtype': 'text', 'text': {'content': f'[VyOmni] 告警\n{text}'}})
    
    req = urllib.request.Request(
        url,
        data=payload.encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f'[WARN] Webhook send failed: {e}', file=sys.stderr)
        return False

def main():
    config = load_alert_config()
    print(f'[INFO] VyOmni Alerter started. Enabled: {config.get("enabled", False)}')
    
    last_alerts = set()  # 去重：同一告警 30min 内不重复发送
    
    while True:
        try:
            if config.get('enabled', False):
                alerts = check_tunnel_alerts(config) + check_branch_alerts(config)
                
                # 去重
                new_alerts = []
                for a in alerts:
                    key = f'{a["level"]}:{a["title"]}'
                    if key not in last_alerts:
                        new_alerts.append(a)
                        last_alerts.add(key)
                
                if new_alerts:
                    for channel in config.get('channels', []):
                        url = channel.get('webhook_url', '')
                        if url:
                            send_webhook(url, new_alerts)
                            print(f'[{time.strftime("%H:%M:%S")}] Sent {len(new_alerts)} alerts')
            
            # 每30分钟重置去重缓存
            if int(time.time()) % 1800 < CHECK_INTERVAL:
                last_alerts.clear()
                
        except Exception as e:
            print(f'[ERROR] {e}', file=sys.stderr)
        
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
