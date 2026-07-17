#!/bin/bash
# ============================================================
# 离线资源下载脚本
# 在有网络环境执行一次，将 ECharts + FontAwesome 下载到本地
# 然后将 assets/ 目录复制到目标 VyOS 的 /var/www/monitor/assets/
# ============================================================
set -e

ASSETS_DIR="${1:-./frontend/assets}"
echo "下载离线前端资源到: $ASSETS_DIR"

mkdir -p "$ASSETS_DIR/fontawesome/css" "$ASSETS_DIR/fontawesome/webfonts"

# ECharts 5.5.0（精简版）
echo "  → 下载 ECharts..."
curl -sL "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js" \
    -o "$ASSETS_DIR/echarts.min.js"
echo "    ✓ echarts.min.js ($(du -h "$ASSETS_DIR/echarts.min.js" | cut -f1))"

# Font Awesome 6.5.0 Free
echo "  → 下载 Font Awesome..."
curl -sL "https://use.fontawesome.com/releases/v6.5.0/fontawesome-free-6.5.0-web.zip" \
    -o /tmp/fa.zip
unzip -qo /tmp/fa.zip -d /tmp/fa
cp /tmp/fa/fontawesome-free-6.5.0-web/css/all.min.css "$ASSETS_DIR/fontawesome/css/"
cp /tmp/fa/fontawesome-free-6.5.0-web/webfonts/fa-solid-900.woff2 "$ASSETS_DIR/fontawesome/webfonts/"
cp /tmp/fa/fontawesome-free-6.5.0-web/webfonts/fa-regular-400.woff2 "$ASSETS_DIR/fontawesome/webfonts/"
cp /tmp/fa/fontawesome-free-6.5.0-web/webfonts/fa-brands-400.woff2 "$ASSETS_DIR/fontawesome/webfonts/"
rm -rf /tmp/fa /tmp/fa.zip
echo "    ✓ Font Awesome webfonts"

echo ""
echo "✅ 离线资源准备完成！"
echo "   目录: $ASSETS_DIR"
ls -la "$ASSETS_DIR"
