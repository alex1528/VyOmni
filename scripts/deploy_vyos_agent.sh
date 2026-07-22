#!/bin/bash
# ============================================================
# VyOmni Agent 手动部署脚本（离线/高级场景备用）
#
# ★ 推荐方式：使用看板一键部署（无需此脚本）
#   看板 → 节点管理 → [+ 新增节点] → 复制 curl|bash 命令 → VyOS 执行
#
# 本脚本用于无法访问平台 API 的离线场景
# 使用: bash deploy_vyos_agent.sh [hq|branch]
# ============================================================
set -e

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  VyOmni Agent 手动部署（离线备选）               ║"
echo "║                                                  ║"
echo "║  ★ 推荐方式: 看板生成Token → curl|bash一键部署   ║"
echo "║    curl -sL http://<server>:9100/api/deploy/tk_xxx | bash"
echo "╚══════════════════════════════════════════════════╝"
echo ""

ROLE="${1:-branch}"
INSTALL_DIR="/opt/vyomni-agent"

echo "=== 手动部署 (角色: $ROLE) ==="

# 1. 创建目录
mkdir -p "$INSTALL_DIR"

# 2. 检查文件
if [ "$ROLE" = "hq" ]; then
    SCRIPT="collector.py"
else
    SCRIPT="branch_agent.py"
fi

if [ ! -f "$INSTALL_DIR/agent_common.py" ]; then
    echo "[ERROR] 请先将 agent/agent_common.py 复制到 $INSTALL_DIR/"
    echo "  scp agent/agent_common.py vyos@<this-host>:$INSTALL_DIR/"
    exit 1
fi

if [ ! -f "$INSTALL_DIR/$SCRIPT" ]; then
    echo "[ERROR] 请先将 agent/$SCRIPT 复制到 $INSTALL_DIR/"
    echo "  scp agent/$SCRIPT vyos@<this-host>:$INSTALL_DIR/"
    exit 1
fi

if [ ! -f "$INSTALL_DIR/config.conf" ]; then
    echo "[ERROR] 请先创建配置文件 $INSTALL_DIR/config.conf"
    echo "  内容:"
    echo "    server_url = http://<server-ip>:9100"
    echo "    register_token = tk_xxxxxxxxxxxx"
    exit 1
fi

# 3. 创建 systemd 服务
cat > /etc/systemd/system/vyomni-agent.service << EOF
[Unit]
Description=VyOmni ${ROLE^} Agent
After=network.target

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
