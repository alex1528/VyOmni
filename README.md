# VyOmni 快速入门

## 一、总体说明

VyOmni 采用 **"VyOS 极简 Agent + 独立 Linux 监控平台"** 架构：

- 所有 VyOS 节点（总部 + 分支）仅运行**单文件 Python Agent**，不安装容器
- 独立 Linux 服务器运行完整监控平台（聚合/看板/告警）

## 二、快速部署

### 2.1 Linux 监控服务器

```bash
# 安装 Docker（如已有跳过）
curl -fsSL https://get.docker.com | bash

# 克隆项目
git clone <repo_url> /opt/vyomni && cd /opt/vyomni

# 一键启动
cd server
cp ../config/config.json .
cp ../config/secrets.json .
cp ../config/alert.json .
docker compose up -d
```

平台监听：
- `http://<server-ip>:8080` — 监控看板
- `http://<server-ip>:9100` — 聚合器 API（仅供 Agent 上报）

### 2.2 总部 VyOS Agent

```bash
# 复制 Agent 到 VyOS
scp agent/collector.py vyos@<hq-ip>:/opt/vyomni-agent/
scp config/agent_hq.conf vyos@<hq-ip>:/opt/vyomni-agent/config.conf

# 在 VyOS 上配置 systemd 服务（自动启动）
ssh vyos@<hq-ip>
cat > /etc/systemd/system/vyomni-agent.service << 'EOF'
[Unit]
Description=VyOmni HQ Agent
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/vyomni-agent/collector.py
WorkingDirectory=/opt/vyomni-agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now vyomni-agent
```

### 2.3 分支 VyOS Agent

```bash
scp agent/branch_agent.py vyos@<branch-ip>:/opt/vyomni-agent/
scp config/agent_branch.conf vyos@<branch-ip>:/opt/vyomni-agent/config.conf

# 同样配置 systemd 服务（同上，ExecStart 改为 branch_agent.py）
```

## 三、验证

```bash
# 在监控服务器查看日志
docker compose logs -f aggregator

# 检查看板
curl http://localhost:8080/
curl http://localhost:8080/api/tunnel
```

## 四、架构对比（新 vs 旧）

| 维度 | 旧（VyOS 容器部署） | 新（独立 Linux 平台） |
|------|---------------------|----------------------|
| VyOS 负担 | Podman + 3容器 ~300MB | 单进程 Agent <5MB |
| 部署复杂度 | VyOS container命令 | systemd 一个service |
| 可维护性 | VyOS podman兼容问题多 | 标准 Docker Compose |
| 扩展性 | 受限于VyOS资源 | Linux无限制 |
| 看板访问 | 宿主机端口映射(bug) | 标准端口映射(稳定) |

## 五、目录结构

详见 [PROJECT.md](PROJECT.md)

## 六、配置说明

详见 `config/` 目录下各配置文件模板及注释。
