#!/bin/bash
# ============================================================
# VyOmni 监控平台部署脚本（Linux 服务器）
# 使用: bash scripts/deploy_server.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=========================================="
echo "  VyOmni 监控平台部署"
echo "=========================================="
echo ""
echo "项目目录: $PROJECT_DIR"
echo ""

# 1. 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "[ERROR] Docker 未安装。请先执行:"
    echo "  curl -fsSL https://get.docker.com | bash"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "[ERROR] Docker Compose V2 未安装。"
    exit 1
fi
echo "[OK] Docker 环境就绪"

# 2. 准备 server/ 目录配置文件
cd "$PROJECT_DIR/server"

if [ ! -f config.json ]; then
    cp "$PROJECT_DIR/config/config.json" ./config.json
    echo "[INFO] 已复制 config.json"
fi

if [ ! -f secrets.json ]; then
    # 生成随机 register_token 和 admin 密码
    TOKEN="tk_$(head -c 6 /dev/urandom | xxd -p)"
    ADMIN_PW="$(head -c 16 /dev/urandom | base64 | tr -d '/+=\n' | head -c 20)"
    cat > secrets.json << EOF
{
  "register_token": "$TOKEN",
  "admin_password": "$ADMIN_PW"
}
EOF
    echo "[INFO] 已生成 secrets.json"
    echo "  register_token: $TOKEN"
    echo "  admin_password: $ADMIN_PW"
    echo ""
    echo "  ★ 请妥善保存以上信息！"
fi

if [ ! -f alert.json ]; then
    cp "$PROJECT_DIR/config/alert.json" ./alert.json
    echo "[INFO] 已复制 alert.json（请配置告警 Webhook）"
fi

# 3. 下载离线前端资源
echo ""
echo "[INFO] 检查离线资源..."
bash download_assets.sh

# 4. 启动服务
echo ""
echo "[INFO] 启动 Docker Compose..."
docker compose up -d --build

echo ""
echo "=========================================="
echo "  ✅ 监控平台部署完成！"
echo "=========================================="
echo ""
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo "  看板地址:    http://${LOCAL_IP}:8080"
echo "  聚合器 API:  http://${LOCAL_IP}:9100"
echo "  健康检查:    http://${LOCAL_IP}:9100/health"
echo ""
echo "  查看日志:    cd $PROJECT_DIR/server && docker compose logs -f"
echo "  停止服务:    cd $PROJECT_DIR/server && docker compose down"
echo ""
echo "  下一步: 打开看板 → 节点管理 → [+ 新增节点] → 生成部署命令"
echo ""
