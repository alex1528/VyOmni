#!/bin/bash
# VyOmni - 下载前端离线资源到 server/assets/
# 在 Linux 监控服务器上执行一次即可
# 用法: cd server && bash download_assets.sh

set -e
cd "$(dirname "$0")"

ASSETS_DIR="./assets"
mkdir -p "$ASSETS_DIR/fontawesome/css" "$ASSETS_DIR/fontawesome/webfonts"

echo "=== VyOmni 离线资源下载 ==="
echo ""

# ECharts
echo "[1/3] 下载 ECharts 5.5.0..."
curl -sL "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"     -o "$ASSETS_DIR/echarts.min.js"
echo "  ✓ echarts.min.js ($(du -h "$ASSETS_DIR/echarts.min.js" | cut -f1))"

# Font Awesome
echo "[2/3] 下载 Font Awesome 6.5.0..."
curl -sL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css"     -o "$ASSETS_DIR/fontawesome/css/all.min.css"
curl -sL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/fa-solid-900.woff2"     -o "$ASSETS_DIR/fontawesome/webfonts/fa-solid-900.woff2"
curl -sL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/fa-regular-400.woff2"     -o "$ASSETS_DIR/fontawesome/webfonts/fa-regular-400.woff2"
curl -sL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/fa-brands-400.woff2"     -o "$ASSETS_DIR/fontawesome/webfonts/fa-brands-400.woff2"
echo "  ✓ fontawesome/"

# 中国地图 GeoJSON（ECharts 用）
echo "[3/3] 下载中国地图数据..."
curl -sL "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json"     -o "$ASSETS_DIR/china.json"
if [ ! -s "$ASSETS_DIR/china.json" ]; then
    # 备用地址
    curl -sL "https://cdn.jsdelivr.net/npm/echarts@4.9.0/map/json/china.json"         -o "$ASSETS_DIR/china.json"
fi
echo "  ✓ china.json ($(du -h "$ASSETS_DIR/china.json" | cut -f1))"

echo ""
echo "✅ 离线资源准备完成"
ls -lh "$ASSETS_DIR/"
