#!/bin/bash
# VyOmni - 下载前端离线资源到 server/assets/
# 在 Linux 监控服务器上执行一次即可

set -e
cd "$(dirname "$0")"

ASSETS_DIR="./assets"
mkdir -p "$ASSETS_DIR"

echo "下载 ECharts..."
curl -sL "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"     -o "$ASSETS_DIR/echarts.min.js"
echo "  ✓ echarts.min.js ($(du -h "$ASSETS_DIR/echarts.min.js" | cut -f1))"

echo ""
echo "✅ 离线资源准备完成: $ASSETS_DIR/"
ls -lh "$ASSETS_DIR/"
