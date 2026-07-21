#!/bin/bash
# VyOmni 监控平台部署脚本（Linux 服务器）
# 使用: bash scripts/deploy_server.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="/opt/vyomni"

echo "=== VyOmni 监控平台部署 ==="
echo "项目目录: $PROJECT_DIR"
echo "安装目录: $INSTALL_DIR"
echo ""

# 1. 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "[ERROR] Docker 未安装。请先执行: curl -fsSL https://get.docker.com | bash"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "[ERROR] Docker Compose V2 未安装。"
    exit 1
fi

echo "[OK] Docker 环境就绪"

# 2. 复制项目到安装目录
if [ "$PROJECT_DIR" != "$INSTALL_DIR" ]; then
    echo "[INFO] 复制项目到 $INSTALL_DIR ..."
    mkdir -p "$INSTALL_DIR"
    rsync -a --delete "$PROJECT_DIR/" "$INSTALL_DIR/"
fi

# 3. 准备配置文件
cd "$INSTALL_DIR/server"

if [ ! -f config.json ]; then
    cp "$INSTALL_DIR/config/config.json" ./config.json
    echo "[INFO] 已复制 config.json（请按需修改）"
fi

if [ ! -f secrets.json ]; then
    # 生成随机密钥
    KEY=$(head -c 32 /dev/urandom | base64 | tr -d '\n/+=')
    cat > secrets.json << EOF
{
    "hmac_key": "$KEY"
}
EOF
    echo "[INFO] 已生成 secrets.json（HMAC密钥: $KEY）"
    echo "[IMPORTANT] 请将此密钥配置到所有 VyOS Agent 的 config.conf 中"
fi

if [ ! -f alert.json ]; then
    cp "$INSTALL_DIR/config/alert.json" ./alert.json
    echo "[INFO] 已复制 alert.json（请配置告警 Webhook）"
fi

# 4. 启动服务
echo ""
echo "[INFO] 启动 Docker Compose ..."
docker compose up -d --build

echo ""
echo "=== 部署完成 ==="
echo "看板地址: http://$(hostname -I | awk '{print $1}'):8080"
echo "聚合器:   http://$(hostname -I | awk '{print $1}'):9100"
echo ""
echo "查看日志: cd $INSTALL_DIR/server && docker compose logs -f"
echo "停止服务: cd $INSTALL_DIR/server && docker compose down"
