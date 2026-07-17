#!/bin/bash
# ============================================================
# VyOmni - Docker 镜像构建脚本
# 构建三个容器镜像并导出为 tar.gz（离线部署用）
# ============================================================
set -e
cd "$(dirname "$0")/.."

REGISTRY="${REGISTRY:-localhost}"
TAG="${TAG:-latest}"

echo "=========================================="
echo "  VyOmni - Docker 镜像构建"
echo "=========================================="
echo ""
echo "  Registry: $REGISTRY"
echo "  Tag: $TAG"
echo ""

# 构建 Collector
echo "[1/3] 构建 Collector 镜像..."
docker build -t "$REGISTRY/wg-collector:$TAG" -f collector/Dockerfile collector/
echo "  ✓ $REGISTRY/wg-collector:$TAG"
echo ""

# 构建 Aggregator
echo "[2/3] 构建 Aggregator 镜像..."
docker build -t "$REGISTRY/wg-aggregator:$TAG" -f aggregator/Dockerfile aggregator/
echo "  ✓ $REGISTRY/wg-aggregator:$TAG"
echo ""

# 构建 Nginx + 前端
echo "[3/3] 构建 Nginx 镜像..."
# 需要将前端文件复制到 nginx 构建上下文
TMPDIR=$(mktemp -d)
cp -r frontend/* "$TMPDIR/"
cp nginx/monitor.conf "$TMPDIR/"
cat > "$TMPDIR/Dockerfile" << 'EOF'
FROM nginx:1.25-alpine
RUN rm -f /etc/nginx/conf.d/default.conf
COPY monitor.conf /etc/nginx/sites-enabled/monitor.conf
COPY index.html /var/www/monitor/
COPY css/ /var/www/monitor/css/
COPY js/ /var/www/monitor/js/
COPY assets/ /var/www/monitor/assets/
RUN mkdir -p /var/www/monitor/data
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/monitor/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
EOF
docker build -t "$REGISTRY/wg-nginx:$TAG" -f "$TMPDIR/Dockerfile" "$TMPDIR"
rm -rf "$TMPDIR"
echo "  ✓ $REGISTRY/wg-nginx:$TAG"
echo ""

# 导出 tar（离线部署用）
echo "导出离线镜像..."
mkdir -p dist/
docker save "$REGISTRY/wg-collector:$TAG" | gzip > dist/wg-collector.tar.gz
docker save "$REGISTRY/wg-aggregator:$TAG" | gzip > dist/wg-aggregator.tar.gz
docker save "$REGISTRY/wg-nginx:$TAG" | gzip > dist/wg-nginx.tar.gz

echo ""
echo "=========================================="
echo "  ✅ 镜像构建完成"
echo "=========================================="
echo ""
ls -lh dist/
echo ""
echo "  部署到 VyOS:"
echo "    scp dist/*.tar.gz vyos@<host>:/tmp/"
echo "    sudo podman load -i /tmp/wg-collector.tar.gz"
echo "    sudo podman load -i /tmp/wg-aggregator.tar.gz"
echo "    sudo podman load -i /tmp/wg-nginx.tar.gz"
echo ""
