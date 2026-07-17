#!/bin/bash
# ============================================================
# VyOS Monitor - 分支部署脚本
# 在各分支 VyOS 上执行，部署资源上报 Agent
# ============================================================
set -e

echo "=========================================="
echo "  VyOS WireGuard Monitor - 分支部署"
echo "=========================================="

# 提示输入配置
read -p "分支 ID (如 branch-shanghai-01): " BRANCH_ID
read -p "分支密钥 (由总部提供): " BRANCH_SECRET
read -p "总部隧道 IP (如 10.10.0.1): " HQ_IP
read -p "监控接口列表 (逗号分隔, 如 eth0,eth1,wg0): " INTERFACES

INSTALL_DIR="/opt/wg-monitor"
CONFIG_DIR="/etc/wg-monitor"

# 1. 创建目录
echo "[1/4] 创建目录..."
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"

# 2. 安装依赖
echo "[2/4] 安装 Python 依赖..."
pip3 install psutil --quiet 2>/dev/null || apt-get install -y python3-psutil

# 3. 部署代码和配置
echo "[3/4] 部署 Agent..."
cp collector/branch_agent.py "$INSTALL_DIR/"

cat > "$CONFIG_DIR/agent.conf" << EOF
# Branch Agent 配置
[agent]
branch_id = ${BRANCH_ID}
secret=[REDACTED_PASSWORD]
hq_endpoint = http://${HQ_IP}:8080/monitor/api/report
report_interval = 5
interfaces = ${INTERFACES}
EOF

chmod 600 "$CONFIG_DIR/agent.conf"

# 4. 创建 systemd 服务
echo "[4/4] 创建服务..."

cat > /etc/systemd/system/wg-branch-agent.service << EOF
[Unit]
Description=WireGuard Branch Resource Agent
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/wg-monitor/branch_agent.py
Environment=WG_AGENT_CONFIG=/etc/wg-monitor/agent.conf
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now wg-branch-agent.service

echo ""
echo "=========================================="
echo "  ✅ 分支 Agent 部署完成！"
echo "=========================================="
echo ""
echo "  分支 ID: ${BRANCH_ID}"
echo "  上报目标: http://${HQ_IP}:8080/monitor/api/report"
echo "  检查状态: systemctl status wg-branch-agent"
echo "  查看日志: journalctl -u wg-branch-agent -f"
echo ""
