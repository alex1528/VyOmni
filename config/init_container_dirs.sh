#!/bin/bash
# ============================================================
# VyOmni - 容器化部署前置初始化（configure前执行）
# 用法: sudo bash /opt/wg-monitor/config/init_container_dirs.sh
# ============================================================
set -e

echo "创建容器 volume 所需的宿主机目录..."

mkdir -p /var/www/monitor/data
mkdir -p /etc/wg-monitor

# 复制配置模板（不覆盖已有）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

for cfg in config.json secrets.json alert.json; do
    if [ ! -f "/etc/wg-monitor/$cfg" ]; then
        cp "$SCRIPT_DIR/$cfg" /etc/wg-monitor/
        echo "  ✓ /etc/wg-monitor/$cfg"
    else
        echo "  → /etc/wg-monitor/$cfg 已存在，跳过"
    fi
done

chmod 600 /etc/wg-monitor/secrets.json

echo ""
echo "✅ 目录和配置初始化完成"
echo ""
echo "接下来执行："
echo "  configure"
echo "  source /opt/wg-monitor/config/vyos_container.sh"
echo "  commit"
echo "  save"
echo "  exit"
