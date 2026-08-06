#!/usr/bin/env python3
"""
VyOmni Line Alert — HQ 本地线路中断告警（独立轻量脚本）
================================================================
职责：在 HQ 节点本地检测"特定出口/线路中断"，本地直发 webhook（不经 Server），
      并可选回传 Server 供看板展示。与 vyomni-agent 解耦，独立 systemd 服务。

检测维度：
  1. 物理出口 link 状态：/sys/class/net/{iface}/operstate
  2. 物理出口连通性：ping（支持绑定接口名 -I {iface} 或 绑定源IP -I {src_ip}）
  3. wg 隧道握手：wg show all dump 解析各 peer latest_handshake

状态机（每条出口/线路独立）：
  正常 --连续 fail_threshold 次失败--> 中断[告警]
  中断 --1 次成功--> 恢复[恢复通知]

纯 Python stdlib，无第三方依赖。
"""

import json
import os
import sys
import time
import socket
import subprocess
import urllib.request

CONFIG_PATH = os.environ.get('LINE_ALERT_CONFIG', '/opt/vyomni-agent/line_alert.conf')
STATE_PATH = os.environ.get('LINE_ALERT_STATE', '/opt/vyomni-agent/line_alert_state.json')


# ==================== 配置 / 状态持久化 ====================
def load_config():
    """加载告警配置"""
    if not os.path.exists(CONFIG_PATH):
        print(f'[ERROR] 配置文件不存在: {CONFIG_PATH}', file=sys.stderr)
        return None
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f'[ERROR] 配置加载失败: {e}', file=sys.stderr)
        return None


def load_state():
    """加载告警状态（重启不误报）"""
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_state(state):
    try:
        with open(STATE_PATH, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except IOError:
        pass


# ==================== 检测：物理出口 ====================
# 真实 ping 二进制（绕过 VyOS op-mode 包装器，后者不认 -I 短参数）
PING_BIN = '/bin/ping' if os.path.exists('/bin/ping') else 'ping'
FPING_BIN = '/usr/bin/fping' if os.path.exists('/usr/bin/fping') else None

# 检测日志开关（由 run_checks 从 config.log_checks 更新，默认开启）
LOG_CHECKS = True


def _clog(msg):
    """检测日志输出（受 LOG_CHECKS 开关控制）"""
    if LOG_CHECKS:
        print(msg, file=sys.stderr)


def get_iface_ipv4(iface):
    """读取接口的首个 IPv4 地址（用于 bind_mode=auto 自动绑源IP）。
    优先 /sys 无法拿 IP，用 ip -o -4 addr show 解析。返回 IP 字符串或 None。
    """
    try:
        result = subprocess.run(
            ['ip', '-o', '-4', 'addr', 'show', 'dev', iface],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        # 输出形如: "3: eth1    inet 112.82.212.174/24 brd ... scope global eth1"
        for line in result.stdout.strip().split('\n'):
            parts = line.split()
            if 'inet' in parts:
                idx = parts.index('inet')
                if idx + 1 < len(parts):
                    return parts[idx + 1].split('/')[0]
    except Exception:
        pass
    return None


def check_link_state(iface):
    """读取网卡 link 状态。返回 True=up, False=down/不存在"""
    path = f'/sys/class/net/{iface}/operstate'
    try:
        with open(path) as f:
            state = f.read().strip()
        return state == 'up'
    except IOError:
        return False


def check_ping(target, bind_iface=None, bind_src_ip=None, count=2, timeout=2):
    """
    ping 探测。绑定优先级：源IP > 接口名。
    ⚠️ VyOS 多出口/策略路由环境：-I 接口名 只绑出接口不触发 PBR，
       回程选路错误导致假丢包；-I 源IP 命中 PBR 正确选路。故优先绑源IP。
    使用真实 /bin/ping（绕过 VyOS op-mode 包装器，后者不认 -I）。
    优先 fping（若可用），超时控制更精准。返回 True=通, False=不通。
    返回 (ok: bool, detail: dict)，detail 含 cmd/耗时/退出码/输出摘要。
    """
    # 绑定源优先级：源IP 优先（VyOS PBR 环境唯一可靠方式）
    bind = bind_src_ip or bind_iface

    # 优先 fping：-S 绑源地址，-I 绑接口
    if FPING_BIN:
        cmd = [FPING_BIN, '-c', str(count), '-t', str(timeout * 1000), '-q']
        if bind_src_ip:
            cmd += ['-S', bind_src_ip]
        elif bind_iface:
            cmd += ['-I', bind_iface]
        cmd.append(target)
    else:
        # 真实 ping：-I 接受 源IP 或 接口名
        cmd = [PING_BIN, '-c', str(count), '-W', str(timeout), '-n']
        if bind:
            cmd += ['-I', bind]
        cmd.append(target)

    detail = {
        'cmd': ' '.join(cmd),
        'tool': 'fping' if FPING_BIN else 'ping',
        'bind': bind_src_ip or bind_iface or '(default route)',
        'bind_type': 'src_ip' if bind_src_ip else ('iface' if bind_iface else 'none'),
    }
    t0 = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=count * timeout + 5)
        detail['elapsed_ms'] = int((time.time() - t0) * 1000)
        detail['rc'] = result.returncode
        err = (result.stderr or b'').decode('utf-8', 'ignore').strip()
        out = (result.stdout or b'').decode('utf-8', 'ignore').strip()
        detail['output'] = (err or out).replace('\n', ' ')[:200]
        return result.returncode == 0, detail
    except subprocess.TimeoutExpired:
        detail['elapsed_ms'] = int((time.time() - t0) * 1000)
        detail['rc'] = -1
        detail['output'] = 'timeout'
        return False, detail
    except Exception as e:
        detail['elapsed_ms'] = int((time.time() - t0) * 1000)
        detail['rc'] = -2
        detail['output'] = f'exception: {e}'
        return False, detail


def check_export(exp):
    """
    综合判定单条物理出口。返回 (ok: bool, reason: str)
    link 优先：link down 直接判失败，跳过 ping；link up 再看 ping（若配置了 target）。
    同时打印详细检测日志（link/ping 命令、绑定方式、耗时、结果）。
    """
    iface = exp.get('interface', '')
    name = exp.get('name', iface)
    # 1. link 状态
    link_up = check_link_state(iface)
    if not link_up:
        _clog(f'[CHECK] 出口"{name}" iface={iface} link=DOWN → 异常')
        return False, f'{iface} link down'
    # 2. ping 连通性（可选）
    target = exp.get('ping_target', '')
    if target:
        # bind_mode: auto(自动取接口IP绑源,默认,推荐) | src_ip(手填源IP) |
        #            interface(绑接口名,VyOS多出口PBR环境会假故障,不推荐) | none(不绑)
        # ⚠️ VyOS 实测：-I 接口名回程选路错误→假丢包；-I 源IP 命中PBR→正确
        bind_mode = exp.get('bind_mode', 'auto')
        bind_iface = None
        bind_src_ip = None
        if bind_mode == 'auto':
            # 自动获取接口 IPv4 作为源地址（免手工填 bind_src_ip）
            bind_src_ip = get_iface_ipv4(iface)
            if not bind_src_ip:
                # 取不到IP（接口无地址）→ 视为异常
                _clog(f'[CHECK] 出口"{name}" iface={iface} link=UP bind_mode=auto → 无可用IPv4地址 → 异常')
                return False, f'{iface} 无可用 IPv4 地址'
        elif bind_mode == 'src_ip':
            bind_src_ip = exp.get('bind_src_ip', '')
            if not bind_src_ip:
                # 未手填源IP → 自动获取
                bind_src_ip = get_iface_ipv4(iface)
        elif bind_mode == 'interface':
            bind_iface = iface
        # bind_mode == 'none' → 两者都不绑，走默认路由
        ok, d = check_ping(target, bind_iface=bind_iface, bind_src_ip=bind_src_ip)
        status = 'OK' if ok else 'FAIL'
        _clog(f'[CHECK] 出口"{name}" iface={iface} link=UP target={target} '
              f'bind={d.get("bind")}({d.get("bind_type")}) tool={d.get("tool")} '
              f'rc={d.get("rc")} {d.get("elapsed_ms")}ms → {status}'
              + (f' | {d.get("output")}' if not ok and d.get("output") else ''))
        if not ok:
            return False, f'{iface} ping {target} 不通'
    else:
        _clog(f'[CHECK] 出口"{name}" iface={iface} link=UP (未配ping_target) → 正常')
    return True, 'ok'


# ==================== 检测：wg 隧道 ====================
def get_wg_handshakes():
    """
    解析 wg show all dump，返回 {peer_key: latest_handshake_ts}
    dump peer 行字段: interface pubkey psk endpoint allowed-ips latest-handshake rx tx keepalive
    """
    handshakes = {}
    try:
        result = subprocess.run(
            ['wg', 'show', 'all', 'dump'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return handshakes
        for line in result.stdout.strip().split('\n'):
            fields = line.split('\t')
            # wg show all dump 两类行：
            #   接口行(5字段): iface privkey pubkey listenport fwmark
            #   peer 行(9字段): iface pubkey psk endpoint allowedips lasths rx tx keepalive
            # 按字段数区分：>=8 字段才是 peer 行
            if len(fields) < 8:
                continue
            try:
                peer_key = fields[1]
                handshake_ts = int(fields[5])
                handshakes[peer_key] = handshake_ts
            except (ValueError, IndexError):
                continue
    except Exception:
        pass
    return handshakes


def check_tunnel(peer_key, handshake_ts, timeout):
    """单条隧道判定。返回 (ok, reason)"""
    now = int(time.time())
    if handshake_ts <= 0:
        return False, '从未握手'
    age = now - handshake_ts
    if age > timeout:
        return False, f'最后握手 {age}s 前'
    return True, 'ok'


# ==================== 告警发送 ====================
def send_webhook(webhook, title, detail, level):
    """发送单条 webhook（支持 dingtalk/wecom/feishu/lark/telegram/generic）"""
    wtype = webhook.get('type', 'generic')
    url = webhook.get('url', '')
    if not url:
        return False

    hostname = socket.gethostname()
    text = f'[VyOmni-HQ告警] {hostname}\n[{level.upper()}] {title}\n{detail}\n时间: {time.strftime("%Y-%m-%d %H:%M:%S")}'

    headers = {'Content-Type': 'application/json'}
    if wtype == 'dingtalk':
        payload = {'msgtype': 'text', 'text': {'content': text}}
    elif wtype == 'wecom':
        payload = {'msgtype': 'text', 'text': {'content': text}}
    elif wtype in ('feishu', 'lark'):
        # 飞书/Lark 自定义机器人，文本消息格式
        payload = {'msg_type': 'text', 'content': {'text': text}}
    elif wtype == 'telegram':
        # Telegram Bot：url 为 https://api.telegram.org/bot<TOKEN>/sendMessage
        # chat_id 从 webhook.chat_id 读取
        chat_id = webhook.get('chat_id', '')
        if not chat_id:
            print('[WARN] telegram 缺少 chat_id', file=sys.stderr)
            return False
        payload = {'chat_id': chat_id, 'text': text}
    else:  # generic
        payload = {
            'source': 'vyomni-hq-line-alert',
            'hostname': hostname,
            'level': level,
            'title': title,
            'detail': detail,
            'timestamp': int(time.time()),
        }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=headers,
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f'[WARN] webhook({wtype}) 发送失败: {e}', file=sys.stderr)
        return False


def report_to_server(config, event):
    """回传告警事件到 Server 供看板展示（可选）"""
    server_report = config.get('server_report', {})
    if not server_report.get('enabled', False):
        return
    url = server_report.get('url', '')
    if not url:
        return
    try:
        payload = json.dumps(event).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f'[WARN] 回传 Server 失败: {e}', file=sys.stderr)


def dispatch_alert(config, target_id, name, kind, is_down, reason):
    """
    发送告警/恢复通知到所有 webhook + 可选回传 Server。
    kind: 'export' | 'tunnel'
    """
    level = 'critical' if is_down else 'info'
    if is_down:
        title = f'线路中断: {name}'
        detail = f'类型: {kind} | 原因: {reason}'
    else:
        title = f'线路恢复: {name}'
        detail = f'类型: {kind} | 已恢复正常'

    for webhook in config.get('webhooks', []):
        # 单渠道开关：enabled=false 则跳过（缺省 true 向后兼容）
        if webhook.get('enabled', True):
            send_webhook(webhook, title, detail, level)

    report_to_server(config, {
        'source': 'vyomni-hq-line-alert',
        'hostname': socket.gethostname(),
        'target_id': target_id,
        'name': name,
        'kind': kind,
        'level': level,
        'event': 'down' if is_down else 'recover',
        'title': title,
        'detail': detail,
        'timestamp': int(time.time()),
    })

    print(f'[{time.strftime("%H:%M:%S")}] {"⚠️ " if is_down else "✅ "}{title} — {detail}')


# ==================== 状态机 ====================
def update_state_machine(state, target_id, name, kind, ok, reason, fail_threshold, config):
    """
    更新单个目标的状态机并在状态翻转时告警。
    state[target_id] = {'status': 'up'/'down', 'fail_count': N}
    """
    st = state.setdefault(target_id, {'status': 'up', 'fail_count': 0})

    if ok:
        # 成功：若之前是 down，则恢复通知
        if st['status'] == 'down':
            dispatch_alert(config, target_id, name, kind, is_down=False, reason=reason)
        st['status'] = 'up'
        st['fail_count'] = 0
    else:
        # 失败：累加，达到阈值且当前非 down 才告警
        st['fail_count'] += 1
        if st['fail_count'] >= fail_threshold and st['status'] != 'down':
            st['status'] = 'down'
            dispatch_alert(config, target_id, name, kind, is_down=True, reason=reason)


# ==================== 主循环 ====================
def run_checks(config, state):
    # 更新检测日志开关（config.log_checks，默认 true）
    global LOG_CHECKS
    LOG_CHECKS = config.get('log_checks', True)
    # --- 物理出口 ---
    for exp in config.get('exports', []):
        if not exp.get('enabled', True):
            continue
        name = exp.get('name', exp.get('interface', 'unknown'))
        tid = 'export:' + exp.get('interface', name)
        threshold = exp.get('fail_threshold', config.get('default_fail_threshold', 3))
        ok, reason = check_export(exp)
        update_state_machine(state, tid, name, 'export', ok, reason, threshold, config)

    # --- wg 隧道 ---
    tun_cfg = config.get('tunnels', {})
    if tun_cfg.get('enabled', False):
        timeout = tun_cfg.get('handshake_timeout', 180)
        threshold = tun_cfg.get('fail_threshold', 2)
        watch_list = tun_cfg.get('watch_list', [])  # 空=全部
        aliases = tun_cfg.get('aliases', {})  # {peer_key: 可读名}
        handshakes = get_wg_handshakes()
        for peer_key, hs_ts in handshakes.items():
            if watch_list and peer_key not in watch_list:
                continue
            name = aliases.get(peer_key, peer_key[:16] + '...')
            tid = 'tunnel:' + peer_key
            ok, reason = check_tunnel(peer_key, hs_ts, timeout)
            update_state_machine(state, tid, name, 'tunnel', ok, reason, threshold, config)


def main():
    print('[INFO] VyOmni Line Alert 启动')
    config = load_config()
    if not config:
        print('[ERROR] 无有效配置，退出', file=sys.stderr)
        sys.exit(1)

    if not config.get('enabled', False):
        print('[INFO] 告警未启用 (enabled=false)，空转监听配置')

    state = load_state()
    interval = config.get('check_interval', 15)
    print(f'[INFO] 检测间隔: {interval}s | 出口数: {len(config.get("exports", []))} | 隧道监控: {config.get("tunnels", {}).get("enabled", False)}')

    while True:
        try:
            # 热加载配置（允许改配置不重启）
            new_config = load_config()
            if new_config:
                config = new_config
            if config.get('enabled', False):
                run_checks(config, state)
                save_state(state)
        except Exception as e:
            print(f'[ERROR] 检测循环异常: {e}', file=sys.stderr)
        time.sleep(config.get('check_interval', 15))


if __name__ == '__main__':
    main()
