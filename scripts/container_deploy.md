# 容器化部署方案

> 使用 VyOS 1.5.0 内置容器引擎部署监控组件（无需额外安装 Docker）
> VyOS 1.5.0 支持 `set container` 命令直接运行 OCI 容器

---

## 架构

```
VyOS 主机
├── 容器: wg-collector      (采集隧道状态)
├── 容器: wg-aggregator     (接收分支上报)
├── 容器: wg-nginx          (Web 服务)
└── 共享卷: /var/www/monitor/data  (JSON 数据交换)
```

---

## 1. 构建容器镜像

### 1.1 Collector 镜像

```dockerfile
# collector/Dockerfile
FROM python:3.11-slim

RUN pip install --no-cache-dir psutil

WORKDIR /app
COPY collector.py /app/

CMD ["python3", "-u", "collector.py"]
```

### 1.2 Aggregator 镜像

```dockerfile
# aggregator/Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY aggregator.py /app/

EXPOSE 9100

CMD ["python3", "-u", "aggregator.py"]
```

### 1.3 Nginx 镜像（含前端）

```dockerfile
# nginx/Dockerfile
FROM nginx:alpine

# 复制前端文件
COPY frontend/ /var/www/monitor/
COPY nginx/monitor.conf /etc/nginx/sites-enabled/default.conf

# 创建数据目录
RUN mkdir -p /var/www/monitor/data

EXPOSE 8080
```

### 1.4 构建脚本（在开发机执行）

```bash
#!/bin/bash
# scripts/build_images.sh
set -e

REGISTRY="registry.local:5000"  # 或直接使用本地 tar

echo "构建 Collector 镜像..."
docker build -t $REGISTRY/wg-collector:latest -f collector/Dockerfile collector/

echo "构建 Aggregator 镜像..."
docker build -t $REGISTRY/wg-aggregator:latest -f aggregator/Dockerfile aggregator/

echo "构建 Nginx 镜像..."
docker build -t $REGISTRY/wg-nginx:latest -f nginx/Dockerfile .

echo "导出为 tar（离线部署用）..."
mkdir -p dist/
docker save $REGISTRY/wg-collector:latest | gzip > dist/wg-collector.tar.gz
docker save $REGISTRY/wg-aggregator:latest | gzip > dist/wg-aggregator.tar.gz
docker save $REGISTRY/wg-nginx:latest | gzip > dist/wg-nginx.tar.gz

echo "✅ 镜像构建完成: dist/"
ls -lh dist/
```

---

## 2. VyOS 容器部署配置

### 2.1 导入镜像到 VyOS

```bash
# 方法1：从 tar 导入（离线）
sudo podman load -i /tmp/wg-collector.tar.gz
sudo podman load -i /tmp/wg-aggregator.tar.gz
sudo podman load -i /tmp/wg-nginx.tar.gz

# 方法2：从私有 registry 拉取（有网）
# VyOS set container 会自动 pull
```

### 2.2 VyOS 容器配置命令

```bash
configure

# ============================================================
# 共享数据网络（容器间通信 + 数据卷）
# ============================================================
set container network monitor-net prefix '172.20.0.0/24'

# ============================================================
# 容器1: Collector（采集隧道状态）
# ============================================================
set container name wg-collector image 'localhost/wg-collector:latest'
set container name wg-collector network monitor-net address '172.20.0.10'

# 挂载配置
set container name wg-collector volume config source '/etc/wg-monitor'
set container name wg-collector volume config destination '/etc/wg-monitor'
set container name wg-collector volume config mode 'ro'

# 挂载数据输出目录
set container name wg-collector volume data source '/var/www/monitor/data'
set container name wg-collector volume data destination '/output'

# 环境变量
set container name wg-collector environment WG_MONITOR_CONFIG value '/etc/wg-monitor/config.json'
set container name wg-collector environment WG_MONITOR_OUTPUT value '/output'
set container name wg-collector environment WG_MONITOR_INTERVAL value '5'

# ⚠️ 关键：需要 host network 或挂载 wg socket 才能执行 wg show
# 方案A：使用 host 网络命名空间（推荐，可直接执行 wg 命令）
set container name wg-collector allow-host-networks

# 重启策略
set container name wg-collector restart 'on-failure'

# ============================================================
# 容器2: Aggregator（接收分支上报）
# ============================================================
set container name wg-aggregator image 'localhost/wg-aggregator:latest'
set container name wg-aggregator network monitor-net address '172.20.0.11'

# 挂载密钥 + 数据输出
set container name wg-aggregator volume secrets source '/etc/wg-monitor'
set container name wg-aggregator volume secrets destination '/etc/wg-monitor'
set container name wg-aggregator volume secrets mode 'ro'

set container name wg-aggregator volume data source '/var/www/monitor/data'
set container name wg-aggregator volume data destination '/output'

# 环境变量
set container name wg-aggregator environment AGG_LISTEN_HOST value '0.0.0.0'
set container name wg-aggregator environment AGG_LISTEN_PORT value '9100'
set container name wg-aggregator environment AGG_SECRETS value '/etc/wg-monitor/secrets.json'
set container name wg-aggregator environment AGG_OUTPUT_DIR value '/output'

# 端口映射：隧道网段可访问 9100
set container name wg-aggregator port report source '9100'
set container name wg-aggregator port report destination '9100'
set container name wg-aggregator port report listen-address '10.10.0.1'

set container name wg-aggregator restart 'on-failure'

# ============================================================
# 容器3: Nginx（前端 + API 网关）
# ============================================================
set container name wg-nginx image 'localhost/wg-nginx:latest'
set container name wg-nginx network monitor-net address '172.20.0.12'

# 挂载数据目录（供 JSON 读取）
set container name wg-nginx volume data source '/var/www/monitor/data'
set container name wg-nginx volume data destination '/var/www/monitor/data'
set container name wg-nginx volume data mode 'ro'

# 端口映射
set container name wg-nginx port web source '8080'
set container name wg-nginx port web destination '8080'

set container name wg-nginx restart 'on-failure'

commit
save
exit
```

---

## 3. 容器化 vs 原生部署对比

| 维度 | 容器化 | 原生 systemd |
|------|--------|--------------|
| 隔离性 | ✅ 进程/文件系统隔离 | ❌ 共享宿主环境 |
| 升级 | ✅ 替换镜像即可 | 需手动替换文件 |
| 依赖管理 | ✅ 镜像内置 | 需宿主装 psutil 等 |
| wg 命令访问 | ⚠️ 需 host-network | ✅ 天然可用 |
| 资源占用 | 稍多（容器开销） | ✅ 最小 |
| VyOS 原生支持 | ✅ 1.4+ 内置 | ✅ systemd |
| 适合场景 | 多组件、需隔离 | 轻量、极简环境 |

**推荐**：
- 如果只有 1-2 个分支、资源有限 → 原生 systemd（`deploy_hq.sh`）
- 如果需要标准化交付、多节点统一管理 → 容器化

---

## 4. Collector 容器特殊处理

Collector 需要执行 `wg show all dump`，这需要：

### 方案 A：allow-host-networks（推荐）

```bash
set container name wg-collector allow-host-networks
```

容器共享宿主网络命名空间，可直接调用 `wg` 命令。

### 方案 B：挂载 wg 工具 + netns

```bash
# 挂载 wg 二进制
set container name wg-collector volume wg-bin source '/usr/bin/wg'
set container name wg-collector volume wg-bin destination '/usr/bin/wg'
set container name wg-collector volume wg-bin mode 'ro'

# 需要 NET_ADMIN capability
set container name wg-collector capability 'net-admin'
```

### 方案 C：采集器在宿主运行，仅容器化 Aggregator + Nginx

这是最简方案——Collector 保持 systemd 原生服务（天然能访问 wg），其他两个容器化：

```bash
# Collector 保持 systemd
systemctl enable --now wg-collector.service

# 仅容器化 Aggregator + Nginx
set container name wg-aggregator ...
set container name wg-nginx ...
```

---

## 5. 容器管理命令

```bash
# 查看运行状态
show container

# 查看容器日志
show container log wg-collector
show container log wg-aggregator
show container log wg-nginx

# 重启容器
restart container wg-collector

# 更新镜像
sudo podman pull localhost/wg-collector:latest
restart container wg-collector
```

---

## 6. 离线部署流程（完整 SOP）

```bash
# === 开发机（有网） ===
# 1. 构建镜像
./scripts/build_images.sh

# 2. 导出 tar
# (build_images.sh 已自动导出到 dist/)

# === 总部 VyOS（离线） ===
# 3. 复制 tar 到 VyOS
scp dist/*.tar.gz vyos@hq-vyos:/tmp/

# 4. 导入镜像
ssh vyos@hq-vyos
sudo podman load -i /tmp/wg-collector.tar.gz
sudo podman load -i /tmp/wg-aggregator.tar.gz
sudo podman load -i /tmp/wg-nginx.tar.gz

# 5. 执行 VyOS 容器配置（§2.2 的命令）
configure
# ... (paste commands)
commit; save; exit

# 6. 验证
show container
curl http://localhost:8080/monitor/
```
