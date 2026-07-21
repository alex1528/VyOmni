#!/bin/bash
# VyOmni Agent 部署脚本（在 VyOS 节点上执行）
# 使用: bash deploy_vyos_agent.sh [hq|branch]

set -e

ROLE="${1:-branch}"
INSTALL_DIR="/opt/vyomni-agent"

echo "=== VyOmni Agent 部署 (角色: $ROLE) ==="

# 1. 创建目录
mkdir -p "$INSTALL_DIR"

# 2. 检查文件
if [ "$ROLE" = "hq" ]; then
    SCRIPT="collector.py"
else
    SCRIPT="branch_agent.py"
fi

if [ ! -f "$INSTALL_DIR/$SCRIPT" ]; then
    echo "[ERROR] 请先将 agent/$SCRIPT 复制到 $INSTALL_DIR/"
    echo "  scp agent/$SCRIPT vyos@<this-host>:$INSTALL_DIR/"
    exit 1
fi

if [ ! -f "$INSTALL_DIR/config.conf" ]; then
    echo "[ERROR] 请先将配置文件复制到 $INSTALL_DIR/config.conf"
    echo "  scp config/agent_${ROLE}.conf vyos@<this-host>:$INSTALL_DIR/config.conf"
    exit 1
fi

# 3. 创建 systemd 服务
cat > /etc/systemd/system/vyomni-agent.service << EOF
[Unit]
Description=VyOmni ${ROLE^} Agent
After=network.target wireguard.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $INSTALL_DIR/$SCRIPT
WorkingDirectory=$INSTALL_DIR
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 4. 启动服务
systemctl daemon-reload
systemctl enable vyomni-agent
systemctl restart vyomni-agent

echo ""
echo "=== 部署完成 ==="
echo "查看状态: systemctl status vyomni-agent"
echo "查看日志: journalctl -u vyomni-agent -f"
