#!/usr/bin/env python3
"""
HQ Aggregator API — 总部聚合器
运行于总部，接收分支 Agent 上报，验证签名，聚合写入 status-branches.json

功能：
- HTTP Server 接收 POST /report
- HMAC-SHA256 签名验证
- 时间窗口防重放
- 聚合多分支数据并原子写入 JSON
- 分支超时检测
"""

import hashlib
import hmac
import json
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ==================== 配置 ====================
LISTEN_HOST = os.environ.get('AGG_LISTEN_HOST', '0.0.0.0')
LISTEN_PORT = int(os.environ.get('AGG_LISTEN_PORT', '9100'))
SECRETS_PATH = os.environ.get('AGG_SECRETS', '/etc/wg-monitor/secrets.json')
OUTPUT_DIR = os.environ.get('AGG_OUTPUT_DIR', '/var/www/monitor/data')
MAX_CLOCK_SKEW = 60  # 秒
BRANCH_TIMEOUT = 20  # 秒，超过此值标记分支上报中断
FLUSH_INTERVAL = 3   # 秒，写入 JSON 频率

# ==================== 全局状态 ====================
branches_data = {}  # {branch_id: {payload + metadata}}
data_lock = threading.Lock()
secrets = {}
start_time = time.time()


def load_secrets() -> dict:
    """加载分支密钥"""
    try:
        with open(SECRETS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[WARN] 密钥文件加载失败: {e}")
        return {}


def verify_signature(headers: dict, body: str) -> tuple:
    """
    验证 HMAC 签名
    返回: (is_valid: bool, branch_id: str, error_msg: str)
    """
    branch_id = headers.get('X-Branch-ID', '')
    timestamp = headers.get('X-Timestamp', '')
    signature = headers.get('X-Signature', '')

    if not branch_id:
        return False, '', '缺少 X-Branch-ID'

    # 无密钥模式（开发/调试）
    if not secrets:
        return True, branch_id, ''

    if branch_id not in secrets:
        return False, branch_id, f'未注册的分支: {branch_id}'

    if not timestamp or not signature:
        return False, branch_id, '缺少签名字段'

    # 时间窗口
    try:
        ts = int(timestamp)
    except ValueError:
        return False, branch_id, '时间戳格式错误'

    if abs(time.time() - ts) > MAX_CLOCK_SKEW:
        return False, branch_id, f'时间偏差过大: {abs(time.time() - ts):.0f}s'

    # 重算签名
    secret = secrets[branch_id]
    sign_string = f"{branch_id}\n{timestamp}\n{body}"
    expected = hmac.new(
        secret.encode('utf-8'),
        sign_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return False, branch_id, '签名验证失败'

    return True, branch_id, ''


class ReportHandler(BaseHTTPRequestHandler):
    """处理分支上报请求"""

    def do_GET(self):
        if self.path == '/health':
            # 健康检查端点
            health = {
                'status': 'ok',
                'branches_count': len(branches_data),
                'uptime_seconds': int(time.time() - start_time),
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(health).encode())
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != '/report':
            self.send_error(404)
            return

        # 读取 body
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 65536:  # 限制 64KB
            self.send_error(413, 'Payload too large')
            return

        body = self.rfile.read(content_length).decode('utf-8')

        # 提取 headers
        headers = {
            'X-Branch-ID': self.headers.get('X-Branch-ID', ''),
            'X-Timestamp': self.headers.get('X-Timestamp', ''),
            'X-Signature': self.headers.get('X-Signature', ''),
        }

        # 验证签名
        is_valid, branch_id, error_msg = verify_signature(headers, body)
        if not is_valid:
            print(f"[REJECT] {branch_id}: {error_msg}")
            self.send_error(403, error_msg)
            return

        # 解析 payload
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, 'Invalid JSON')
            return

        # 更新聚合数据
        with data_lock:
            payload['verified'] = True
            payload['received_at'] = int(time.time())
            branches_data[branch_id] = payload

        # 响应
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, format, *args):
        """简化日志"""
        print(f"[HTTP] {args[0]}")


def flush_worker():
    """定期将聚合数据写入 JSON 文件"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, 'status-branches.json')

    while True:
        time.sleep(FLUSH_INTERVAL)
        try:
            with data_lock:
                now = int(time.time())
                branches_list = []
                for bid, data in branches_data.items():
                    entry = data.copy()
                    # 标记上报是否过期
                    reported_at = entry.get('reported_at', 0)
                    entry['stale'] = (now - reported_at) > BRANCH_TIMEOUT
                    branches_list.append(entry)

                output = {
                    'updated_at': now,
                    'branches': branches_list,
                }

            # 原子写入
            tmp_path = output_path + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, output_path)

        except Exception as e:
            print(f"[ERROR] flush 异常: {e}")


def main():
    global secrets

    print(f"[INFO] HQ Aggregator API 启动")
    print(f"[INFO] 监听: {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"[INFO] 密钥文件: {SECRETS_PATH}")
    print(f"[INFO] 输出目录: {OUTPUT_DIR}")

    secrets = load_secrets()
    if secrets:
        print(f"[INFO] 已加载 {len(secrets)} 个分支密钥")
    else:
        print("[WARN] 无密钥文件，运行于无认证模式（仅限调试）")

    # 启动 flush 线程
    flush_thread = threading.Thread(target=flush_worker, daemon=True)
    flush_thread.start()

    # 启动 HTTP 服务
    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), ReportHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] 聚合器停止")
        server.shutdown()


if __name__ == '__main__':
    main()
