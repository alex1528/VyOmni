#!/usr/bin/env python3
"""
Alert Module — Webhook 告警推送
支持：钉钉机器人、企业微信机器人、通用 Webhook（JSON POST）

可独立运行（定时检测 JSON 文件触发告警），也可被 collector/aggregator 导入使用。
"""

import hashlib
import hmac
import json
import os
import time
import base64
import urllib.request
import urllib.error
from typing import Optional

# ==================== 配置 ====================
ALERT_CONFIG_PATH = os.environ.get('ALERT_CONFIG', '/etc/wg-monitor/alert.json')

# 默认配置
DEFAULT_ALERT_CONFIG = {
    'enabled': True,
    'cooldown_seconds': 300,        # 同类告警冷却时间（5分钟）
    'channels': {
        'dingtalk': {
            'enabled': False,
            'webhook_url': '',
            'secret': '',           # 钉钉签名密钥（可选）
        },
        'wecom': {
            'enabled': False,
            'webhook_url': '',
        },
        'generic': {
            'enabled': False,
            'webhook_url': '',
            'headers': {},          # 自定义 HTTP Headers
        }
    }
}

# ==================== 告警状态 ====================
alert_history = {}  # {alert_key: last_trigger_timestamp}


def load_alert_config() -> dict:
    """加载告警配置"""
    try:
        with open(ALERT_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
            # 合并默认值
            merged = DEFAULT_ALERT_CONFIG.copy()
            merged.update(config)
            return merged
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_ALERT_CONFIG


def should_send(alert_key: str, cooldown: int) -> bool:
    """告警去重/冷却判断"""
    now = time.time()
    last = alert_history.get(alert_key, 0)
    if now - last < cooldown:
        return False
    alert_history[alert_key] = now
    return True


# ==================== 钉钉推送 ====================
def _dingtalk_sign(secret: str) -> tuple:
    """钉钉加签"""
    timestamp = str(int(time.time() * 1000))
    sign_str = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code).decode())
    return timestamp, sign


def send_dingtalk(webhook_url: str, title: str, content: str,
                  secret: str = '', at_all: bool = False) -> bool:
    """
    发送钉钉机器人消息（Markdown 格式）
    """
    import urllib.parse

    url = webhook_url
    if secret:
        timestamp, sign = _dingtalk_sign(secret)
        url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"

    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'title': title,
            'text': content,
        },
        'at': {
            'isAtAll': at_all
        }
    }

    return _http_post(url, payload)


# ==================== 企业微信推送 ====================
def send_wecom(webhook_url: str, title: str, content: str) -> bool:
    """
    发送企业微信机器人消息（Markdown 格式）
    """
    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'content': f"## {title}\n{content}"
        }
    }
    return _http_post(webhook_url, payload)


# ==================== 通用 Webhook ====================
def send_generic(webhook_url: str, title: str, content: str,
                 level: str = 'warning', headers: dict = None) -> bool:
    """
    通用 Webhook JSON POST
    """
    payload = {
        'title': title,
        'content': content,
        'level': level,
        'timestamp': int(time.time()),
        'source': 'vyos-wg-monitor',
    }
    return _http_post(webhook_url, payload, extra_headers=headers)


# ==================== HTTP 工具 ====================
def _http_post(url: str, payload: dict, extra_headers: dict = None) -> bool:
    """发送 HTTP POST JSON 请求"""
    headers = {'Content-Type': 'application/json'}
    if extra_headers:
        headers.update(extra_headers)

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[ALERT-ERROR] Webhook 发送失败: {e}")
        return False


# ==================== 统一告警入口 ====================
def fire_alert(level: str, title: str, content: str,
               alert_key: Optional[str] = None, config: dict = None):
    """
    触发告警（统一入口）

    Args:
        level: 'critical' / 'warning' / 'info'
        title: 告警标题
        content: 告警详情（支持 Markdown）
        alert_key: 去重键（默认=title）
        config: 告警配置（默认从文件加载）
    """
    if config is None:
        config = load_alert_config()

    if not config.get('enabled', True):
        return

    key = alert_key or title
    cooldown = config.get('cooldown_seconds', 300)

    if not should_send(key, cooldown):
        print(f"[ALERT-COOLDOWN] 跳过: {title}")
        return

    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    full_content = f"**时间**: {timestamp}\n**级别**: {level}\n\n{content}"

    channels = config.get('channels', {})
    sent = False

    # 钉钉
    ding = channels.get('dingtalk', {})
    if ding.get('enabled') and ding.get('webhook_url'):
        ok = send_dingtalk(
            ding['webhook_url'], title, full_content,
            secret=ding.get('secret', ''),
            at_all=(level == 'critical')
        )
        if ok:
            sent = True
            print(f"[ALERT-DING] ✓ {title}")

    # 企业微信
    wc = channels.get('wecom', {})
    if wc.get('enabled') and wc.get('webhook_url'):
        ok = send_wecom(wc['webhook_url'], title, full_content)
        if ok:
            sent = True
            print(f"[ALERT-WECOM] ✓ {title}")

    # 通用 Webhook
    gen = channels.get('generic', {})
    if gen.get('enabled') and gen.get('webhook_url'):
        ok = send_generic(
            gen['webhook_url'], title, content,
            level=level, headers=gen.get('headers', {})
        )
        if ok:
            sent = True
            print(f"[ALERT-GENERIC] ✓ {title}")

    if not sent:
        print(f"[ALERT-LOCAL] {level}: {title} | {content}")


# ==================== 独立运行模式 ====================
def check_and_alert():
    """
    独立运行时的告警检测逻辑
    读取 status-tunnel.json + status-branches.json，触发告警
    """
    data_dir = os.environ.get('WG_MONITOR_OUTPUT', '/var/www/monitor/data')
    config = load_alert_config()
    now = time.time()

    # 读取隧道数据
    tunnel_path = os.path.join(data_dir, 'status-tunnel.json')
    try:
        with open(tunnel_path, 'r') as f:
            tunnel = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        fire_alert('critical', '采集器数据缺失',
                   f'无法读取 {tunnel_path}，采集器可能已停止',
                   config=config)
        return

    # 检查采集器心跳
    heartbeat = tunnel.get('collector_heartbeat', 0)
    if now - heartbeat > 30:
        fire_alert('critical', '采集器心跳丢失',
                   f'最后心跳: {int(now - heartbeat)}s 前\n采集器进程可能已崩溃',
                   config=config)

    # 检查隧道离线
    for peer in tunnel.get('peers', []):
        if peer.get('status') == 'offline':
            fire_alert('critical', f"隧道离线: {peer.get('name', peer.get('peer','?')[:8])}",
                       f"接口: {peer.get('interface')}\n"
                       f"最后握手: {peer.get('last_handshake_seconds_ago')}s 前\n"
                       f"Endpoint: {peer.get('endpoint', '-')}",
                       alert_key=f"tunnel_offline_{peer.get('peer','')}",
                       config=config)

    # 读取分支数据
    branch_path = os.path.join(data_dir, 'status-branches.json')
    try:
        with open(branch_path, 'r') as f:
            branches = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return

    for br in branches.get('branches', []):
        bid = br.get('branch_id', 'unknown')

        # 分支上报中断
        reported_at = br.get('reported_at', 0)
        if now - reported_at > 20:
            fire_alert('warning', f'分支上报中断: {bid}',
                       f'最后上报: {int(now - reported_at)}s 前',
                       alert_key=f"branch_stale_{bid}",
                       config=config)

        # CPU 高
        cpu = br.get('cpu_percent', 0)
        if cpu >= 85:
            fire_alert('warning', f'分支 CPU 告警: {bid}',
                       f'CPU: {cpu}%',
                       alert_key=f"branch_cpu_{bid}",
                       config=config)

        # 内存高
        mem = br.get('memory_percent', 0)
        if mem >= 90:
            fire_alert('warning', f'分支内存告警: {bid}',
                       f'内存: {mem}%',
                       alert_key=f"branch_mem_{bid}",
                       config=config)


def main():
    """独立运行模式：定时检测并推送告警"""
    interval = int(os.environ.get('ALERT_INTERVAL', '10'))
    print(f"[INFO] Alert Module 独立模式启动 (间隔 {interval}s)")
    print(f"[INFO] 配置: {ALERT_CONFIG_PATH}")

    while True:
        try:
            check_and_alert()
        except Exception as e:
            print(f"[ERROR] 告警检测异常: {e}")
        time.sleep(interval)


if __name__ == '__main__':
    main()
