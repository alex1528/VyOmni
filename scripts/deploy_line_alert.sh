#!/bin/bash
# ============================================================
# VyOmni HQ 线路中断告警 — 独立部署脚本
#
# 用于已部署 vyomni-agent 的 HQ 节点，单独安装线路告警服务。
# 一键部署（curl|bash）已自动包含此步骤；本脚本用于手动/补装场景。
#
# 用法: bash deploy_line_alert.sh <SERVER_URL>
#   例: bash deploy_line_alert.sh http://192.168.1.10:9100
# ============================================================
set -e

# --- TTY 安全的颜色输出 ---
if [ -t 1 ]; then
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RESET=$'\033[0m'
else
  C_GREEN=''; C_YELLOW=''; C_RESET=''
fi

SERVER_URL="${1:-}"
INSTALL_DIR="/opt/vyomni-agent"

if [ -z "$SERVER_URL" ]; then
  echo "用法: bash deploy_line_alert.sh <SERVER_URL>"
  echo "  例: bash deploy_line_alert.sh http://192.168.1.10:9100"
  exit 1
fi

echo "=========================================="
echo " VyOmni HQ 线路告警 独立部署"
echo " Server: $SERVER_URL"
echo "=========================================="

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# 1. 下载告警脚本
echo "[1/3] 下载告警脚本..."
curl -sL "${SERVER_URL}/api/deploy/files/line_alert.py" -o line_alert.py
chmod +x line_alert.py
echo "  已下载: line_alert.py"

# 2. 写入配置模板（已存在则保留）
echo "[2/3] 准备配置文件..."
if [ ! -f "$INSTALL_DIR/line_alert.conf" ]; then
  cat > "$INSTALL_DIR/line_alert.conf" << 'LACONF_EOF'
{
  "enabled": false,
  "check_interval": 15,
  "default_fail_threshold": 3,
  "webhooks": [
    {"type": "dingtalk", "url": "https://oapi.dingtalk.com/robot/send?access_token=[REDACTED_PARAM]
  ],
  "server_report": {"enabled": false, "url": "SERVER_URL_PLACEHOLDER/api/line-alert"},
  "exports": [
    {"name": "主线路", "interface": "eth1", "enabled": true, "ping_target": "8.8.8.8", "bind_mode": "interface", "bind_src_ip": "", "fail_threshold": 3}
  ],
  "tunnels": {"enabled": true, "handshake_timeout": 180, "fail_threshold": 2, "watch_list": [], "aliases": {}}
}
LACONF_EOF
  # 替换 server_report url 占位符
  sed -i "s|SERVER_URL_PLACEHOLDER|${SERVER_URL}|g" "$INSTALL_DIR/line_alert.conf"
  echo "  ${YELLOW}已生成配置模板 (enabled=false)，请编辑 line_alert.conf 后启用${C_RESET}"
else
  echo "  配置已存在，保留原配置"
fi

# 3. 创建 systemd 服务
echo "[3/3] 创建 systemd 服务..."
cat > /etc/systemd/system/vyomni-line-alert.service << 'SVC_EOF'
[Unit]
Description=VyOmni HQ Line Alert
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/vyomni-agent
ExecStart=/usr/bin/python3 /opt/vyomni-agent/line_alert.py
MemoryMax=20M
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC_EOF

systemctl daemon-reload
systemctl enable vyomni-line-alert
systemctl restart vyomni-line-alert

echo ""
echo "${C_GREEN}=========================================="
echo " 部署完成！"
echo "==========================================${C_RESET}"
echo " 配置文件: $INSTALL_DIR/line_alert.conf"
echo " 编辑后重启: systemctl restart vyomni-line-alert"
echo " 查看日志: journalctl -u vyomni-line-alert -f"
