#!/bin/bash
# ============================================================
# VyOmni - 容器镜像构建脚本
# 自动检测 podman / docker，优先使用 podman（VyOS 原生）
# ============================================================
set -e
cd "$(dirname "$0")/.."

# 自动选择容器引擎
if command -v podman &>/dev/null; then
    CTR="podman"
    # podman 默认 OCI 格式不支持 HEALTHCHECK，需指定 docker 格式
    BUILD_FMT="--format docker"
elif command -v docker &>/dev/null; then
    CTR="docker"
    BUILD_FMT=""
else
    echo "错误：未找到 podman 或 docker，无法构建镜像"
    echo ""
    echo "  VyOS: podman 应已内置，请检查 PATH"
    echo "  Linux: apt install podman 或 curl -fsSL https://get.docker.com | sh"
    exit 1
fi

REGISTRY="${REGISTRY:-localhost}"
TAG="${TAG:-latest}"

echo "=========================================="
echo "  VyOmni - 容器镜像构建"
echo "=========================================="
echo ""
echo "  容器引擎: $CTR"
echo "  Registry: $REGISTRY"
echo "  Tag: $TAG"
echo ""

# 构建 Collector
echo "[1/3] 构建 Collector 镜像..."
$CTR build $BUILD_FMT -t "$REGISTRY/wg-collector:$TAG" -f collector/Dockerfile collector/
echo "  ✓ $REGISTRY/wg-collector:$TAG"
echo ""

# 构建 Aggregator
echo "[2/3] 构建 Aggregator 镜像..."
$CTR build $BUILD_FMT -t "$REGISTRY/wg-aggregator:$TAG" -f aggregator/Dockerfile aggregator/
echo "  ✓ $REGISTRY/wg-aggregator:$TAG"
echo ""

# 构建 Nginx + 前端
echo "[3/3] 构建 Nginx 镜像..."
TMPDIR=$(mktemp -d)
cp -r frontend/* "$TMPDIR/"
cp nginx/monitor_container.conf "$TMPDIR/monitor.conf"
cat > "$TMPDIR/Dockerfile" << 'EOF'
FROM nginx:1.25-alpine
RUN rm -f /etc/nginx/conf.d/default.conf
COPY monitor.conf /etc/nginx/conf.d/monitor.conf
COPY index.html /var/www/monitor/
COPY css/ /var/www/monitor/css/
COPY js/ /var/www/monitor/js/
RUN mkdir -p /var/www/monitor/data /var/www/monitor/assets
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
EOF
# 如果有离线资源则复制进去
[ -d frontend/assets/fontawesome ] && cp -r frontend/assets/* "$TMPDIR/assets/" 2>/dev/null || mkdir -p "$TMPDIR/assets"
$CTR build $BUILD_FMT -t "$REGISTRY/wg-nginx:$TAG" -f "$TMPDIR/Dockerfile" "$TMPDIR"
rm -rf "$TMPDIR"
echo "  ✓ $REGISTRY/wg-nginx:$TAG"
echo ""

# 导出 tar（离线部署用）
echo "导出离线镜像..."
mkdir -p dist/
$CTR save "$REGISTRY/wg-collector:$TAG" | gzip > dist/wg-collector.tar.gz
$CTR save "$REGISTRY/wg-aggregator:$TAG" | gzip > dist/wg-aggregator.tar.gz
$CTR save "$REGISTRY/wg-nginx:$TAG" | gzip > dist/wg-nginx.tar.gz

echo ""
echo "=========================================="
echo "  ✅ 镜像构建完成"
echo "=========================================="
echo ""
ls -lh dist/
echo ""
echo "  本机加载（需逐个执行）:"
echo "    $CTR load -i dist/wg-collector.tar.gz"
echo "    $CTR load -i dist/wg-aggregator.tar.gz"
echo "    $CTR load -i dist/wg-nginx.tar.gz"
echo ""
echo "  或一键加载: for f in dist/*.tar.gz; do $CTR load -i \"\$f\"; done"
echo ""
