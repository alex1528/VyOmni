# VyOS WireGuard 隧道监控系统

> 基于 VyOS 1.5.0 的轻量级、本地闭环 WireGuard 隧道 + 分支资源一体化监控

## 项目结构

```
monitor/
├── README.md                 # 本文件
├── info.md                   # 方案白皮书
├── collector/
│   ├── collector.py          # 总部隧道采集器
│   └── branch_agent.py      # 分支资源上报 Agent
├── aggregator/
│   └── aggregator.py         # 总部聚合器 API（接收分支上报）
├── frontend/
│   ├── index.html            # 看板入口
│   ├── css/dashboard.css     # 样式
│   ├── js/dashboard.js       # 前端逻辑
│   └── assets/               # 离线静态资源（ECharts, FontAwesome）
├── config/
│   ├── config.json           # peer 映射配置模板
│   ├── secrets.json          # 分支密钥模板（部署后权限 600）
│   └── agent.conf            # 分支 Agent 配置模板
├── nginx/
│   └── monitor.conf          # Nginx 站点配置
└── scripts/
    ├── deploy_hq.sh          # 总部一键部署
    ├── deploy_branch.sh      # 分支一键部署
    └── download_assets.sh    # 离线资源下载
```

## 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│                         总部 VyOS                             │
│                                                              │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────┐  │
│  │  Collector   │    │  Aggregator  │    │     Nginx      │  │
│  │ (wg dump)   │    │ (HTTP API)   │    │   (反向代理)    │  │
│  └──────┬──────┘    └──────┬───────┘    └───────┬────────┘  │
│         │                  │                    │            │
│         ▼                  ▼                    │            │
│  status-tunnel.json  status-branches.json       │            │
│         └──────────────────┼────────────────────┘            │
│                            ▼                                 │
│                    Dashboard 前端                              │
└──────────────────────────────────────────────────────────────┘
         ▲                        ▲
         │ WireGuard 隧道          │ HTTP POST (HMAC 签名)
         │                        │
┌────────┴────────┐     ┌────────┴────────┐
│   分支1 VyOS     │     │   分支2 VyOS     │
│  Branch Agent   │     │  Branch Agent   │
└─────────────────┘     └─────────────────┘
```

## 双维度监控

| 维度 | 数据来源 | 采集方式 | 输出 |
|------|----------|----------|------|
| 维度1：隧道状态 | 总部 `wg show all dump` | Collector 本地采集 | status-tunnel.json |
| 维度2：分支资源 | 各分支 CPU/MEM/接口 | Branch Agent 上报 | status-branches.json |

两个维度通过 `branch_id` + `peer_pubkey` 关联，前端合并展示。

## 部署步骤

### 总部

```bash
cd /path/to/monitor
chmod +x scripts/deploy_hq.sh
./scripts/deploy_hq.sh
```

部署完成后编辑：
1. `/etc/wg-monitor/config.json` — 填入各分支 peer 公钥映射
2. `/etc/wg-monitor/secrets.json` — 填入各分支通信密钥

### 分支

```bash
cd /path/to/monitor
chmod +x scripts/deploy_branch.sh
./scripts/deploy_branch.sh
```

按提示输入 branch_id、密钥和总部 IP。

### 离线资源（可选）

如果总部无外网，先在有网环境执行：
```bash
./scripts/download_assets.sh ./frontend/assets
```
然后将 `frontend/assets/` 目录复制到总部 `/var/www/monitor/assets/`。

## 安全设计

- 分支上报使用 **HMAC-SHA256 签名**（branch_id + timestamp + body）
- 时间窗口防重放（±60秒）
- 密钥文件权限 600，每分支独立密钥
- Aggregator 经 Nginx 反向代理暴露（`/monitor/api/report`），内部监听 127.0.0.1:9100

## 隧道网段规划

| 节点 | 隧道地址 | LAN 网段 |
|------|----------|----------|
| 总部 | 10.10.0.1/24 | 192.168.1.0/24 |
| 上海 | 10.10.0.11/24 | 192.168.11.0/24 |
| 北京 | 10.10.0.12/24 | 192.168.12.0/24 |
| 广州 | 10.10.0.13/24 | 192.168.13.0/24 |

## 服务管理

```bash
# 查看状态
systemctl status wg-collector wg-aggregator wg-branch-agent

# 查看日志
journalctl -u wg-collector -f
journalctl -u wg-aggregator -f
journalctl -u wg-branch-agent -f

# 重启
systemctl restart wg-collector
```

## 看板访问

部署完成后访问：`http://<总部IP>:8080/monitor/`
