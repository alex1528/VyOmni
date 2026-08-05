#!/usr/bin/env python3
"""
VyOmni Alerter — Server 端告警服务（节点级失联兜底）
================================================================
双层告警架构中的 Server 层。职责收敛为"节点整体失联"检测：
  - 采集器（HQ）心跳超时
  - 分支节点离线
  - 隧道 peer 握手超时（作为 HQ 本地告警的兜底）

线路级精确检测（特定出口 link/ping、单条隧道）由 HQ 本地 line_alert.py 负责。

状态机：正常→中断[告警]，中断→恢复[恢复通知]，防止重复告警并补充恢复通知。
"""

import json
import time
import os
import sys
import socket
import urllib.request

DATA_DIR = os.environ.get('DATA_DIR', '/data')
ALERT_CONFIG_PATH = os.environ.get('ALERT_CONFIG_PATH', '/app/alert.json')
CHECK_INTERVAL = 10  # 秒

# 状态机：{alert_key: {'status': 'up'/'down', 'fail_count': N}}
alert_state = {}


def load_alert_config():
    if os.path.exists(ALERT_CONFIG_PATH):
        try:
            with open(ALERT_CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {'enabled': False, 'channels': []}


def _read_status(filename):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def collect_conditions(config):
    """
    采集当前所有监控条件的状态。
    返回 list of dict: {key, ok, level, title, detail}
    ok=True 表示正常，ok=False 表示异常。
    """
    conditions = []
    now = int(time.time())

    # --- 隧道状态文件 ---
    tunnel = _read_status('status-tunnel.json')
    if tunnel:
        # 采集器心跳
        heartbeat = tunnel.get('collector_heartbeat', 0)
        hb_ok = not (heartbeat > 0 and now - heartbeat > 60)
        conditions.append({
            'key': 'hq_heartbeat',
            'ok': hb_ok,
            'level': 'warning',
            'title': '总部采集器心跳超时',
            'detail': f'最后心跳: {now - heartbeat}秒前' if heartbeat > 0 else '无心跳数据',
        })

        # 隧道 peer 握手（兜底，使用正确字段 last_handshake_seconds_ago）
        # 开关：monitors.tunnel_handshake=false 时不执行隧道告警
        monitors = config.get('monitors', {})
        tunnel_mon = monitors.get('tunnel_handshake', True)
        for peer in (tunnel.get('peers', []) if tunnel_mon else []):
            ago = peer.get('last_handshake_seconds_ago', 99999)
            name = peer.get('name') or peer.get('endpoint', 'unknown')
            peer_ok = ago < 180
            conditions.append({
                'key': f'tunnel:{peer.get("peer", name)}',
                'ok': peer_ok,
                'level': 'critical',
                'title': f'隧道离线: {name}',
                'detail': f'最后握手: {ago}秒前',
            })

    # --- 分支状态文件 ---
    branches = _read_status('status-branches.json')
    if branches:
        for branch in branches.get('branches', []):
            bid = branch.get('branch_id', '')
            hostname = branch.get('hostname', bid)
            online = branch.get('online', True)
            conditions.append({
                'key': f'branch:{bid}',
                'ok': online,
                'level': 'warning',
                'title': f'分支离线: {hostname}',
                'detail': f'最后上报: {now - branch.get("last_seen", 0)}秒前',
            })

            cpu = branch.get('system', {}).get('cpu_percent', 0)
            conditions.append({
                'key': f'branch_cpu:{bid}',
                'ok': cpu <= 90,
                'level': 'warning',
                'title': f'分支 CPU 过高: {hostname}',
                'detail': f'CPU: {cpu}%',
            })

    return conditions


def send_webhook(channel, title, detail, level):
    """发送单条告警到通知渠道
    支持 type: dingtalk / wecom / feishu / lark / telegram / generic
    """
    ctype = channel.get('type', 'dingtalk')
    url = channel.get('webhook_url', '')
    if not url:
        return False

    text = f'[VyOmni-Server] [{level.upper()}] {title}\n{detail}\n时间: {time.strftime("%Y-%m-%d %H:%M:%S")}'

    if ctype == 'dingtalk':
        payload = {'msgtype': 'text', 'text': {'content': text}}
    elif ctype == 'wecom':
        payload = {'msgtype': 'text', 'text': {'content': text}}
    elif ctype in ('feishu', 'lark'):
        payload = {'msg_type': 'text', 'content': {'text': text}}
    elif ctype == 'telegram':
        chat_id = channel.get('chat_id', '')
        if not chat_id:
            print('[WARN] telegram 缺少 chat_id', file=sys.stderr)
            return False
        payload = {'chat_id': chat_id, 'text': text}
    else:  # generic
        payload = {
            'source': 'vyomni-server-alerter',
            'level': level,
            'title': title,
            'detail': detail,
            'timestamp': int(time.time()),
        }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f'[WARN] Webhook({ctype}) send failed: {e}', file=sys.stderr)
        return False


def dispatch(config, title, detail, level):
    """向所有配置的通道发送"""
    for channel in config.get('channels', []):
        # 单渠道开关：enabled=false 则跳过（缺省 true 向后兼容）
        if channel.get('enabled', True) and channel.get('webhook_url'):
            send_webhook(channel, title, detail, level)


def process_conditions(config, conditions):
    """状态机处理：翻转时告警/恢复"""
    fail_threshold = config.get('fail_threshold', 2)

    for cond in conditions:
        key = cond['key']
        st = alert_state.setdefault(key, {'status': 'up', 'fail_count': 0})

        if cond['ok']:
            if st['status'] == 'down':
                # 恢复通知
                dispatch(config, cond['title'].replace('离线', '恢复').replace('超时', '恢复').replace('过高', '恢复正常'),
                         '已恢复正常', 'info')
                print(f'[{time.strftime("%H:%M:%S")}] ✅ 恢复: {cond["title"]}')
            st['status'] = 'up'
            st['fail_count'] = 0
        else:
            st['fail_count'] += 1
            if st['fail_count'] >= fail_threshold and st['status'] != 'down':
                st['status'] = 'down'
                dispatch(config, cond['title'], cond['detail'], cond['level'])
                print(f'[{time.strftime("%H:%M:%S")}] ⚠️  告警: {cond["title"]} — {cond["detail"]}')


def main():
    config = load_alert_config()
    print(f'[INFO] VyOmni Alerter (Server层) 启动. Enabled: {config.get("enabled", False)}')

    while True:
        try:
            config = load_alert_config()  # 热加载
            if config.get('enabled', False):
                conditions = collect_conditions(config)
                process_conditions(config, conditions)
        except Exception as e:
            print(f'[ERROR] {e}', file=sys.stderr)
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
