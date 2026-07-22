# VyOmni

> VyOS WireGuard 隧道组网与一体化监控系统

## 项目简介

VyOmni 是一套面向 VyOS 1.5.0 环境的轻量级网络监控解决方案，实现跨地域 WireGuard 星形隧道组网 + 集中式监控看板。通过「看板生成 Token → 目标节点一条命令部署」实现零配置节点纳管。

## 架构原则

- **VyOS 节点极简化**：所有 VyOS 节点仅部署轻量 Agent（单文件 Python），不安装容器
- **集中式监控平台**：独立 Linux 服务器承载聚合/看板/告警全部功能
- **一键部署**：看板生成一次性 Token → VyOS 终端粘贴 `curl | bash` 即完成部署
- **自注册+审核**：新节点自动注册 → 管理员看板审核 → 纳入监控

## 核心能力

| 能力 | 说明 |
|------|------|
| 🔗 多分支星形组网 | eBGP 动态路由（总部 AS 65500 + 各分支独立 AS） |
| 📊 双维度监控 | 维度1：隧道状态；维度2：分支资源（CPU/内存/负载/流量） |
| 🎫 按节点 Token | 一次性 Token（24h 过期），看板生成 → 复制命令 → 一键部署 |
| 🚀 curl\|bash 部署 | 目标节点一条命令完成全部部署（下载 Agent + 配置 + 服务 + 注册） |
| ✅ 平台审核 | 新节点默认 pending，管理员审核通过后才展示数据 |
| ⚙️ 动态配置 | 平台可下发采集间隔/能力集变更，Agent 热更新无需重启 |
| 🔄 远程升级 | 平台推送新版 Agent，节点自动下载→校验→替换→重启 |
| 🔐 安全上报 | HMAC-SHA256 签名 + 时间窗口防重放 |
| 🔔 多通道告警 | 钉钉 / 企业微信 / 通用 Webhook |
| 🖥️ 全离线看板 | 暗色/亮色主题切换，拓扑图/地图/趋势图/弹窗详情 |

## 部署架构

```
┌───────────────────────────────────────────────────────────────┐
│                    Linux 监控服务器 (Docker Compose)            │
│                                                               │
│  ┌─────────────┐ ┌──────────────┐ ┌────────┐ ┌───────────┐  │
│  │ Aggregator  │ │    Nginx     │ │Alerter │ │ Dashboard │  │
│  │ :9100       │ │ :8080        │ │        │ │  (前端)   │  │
│  │ ·注册/上报  │ │ ·静态文件    │ │·告警   │ │·拓扑/地图 │  │
│  │ ·Token管理  │ │ ·反代API     │ │·Webhook│ │·节点管理  │  │
│  │ ·一键部署   │ │              │ │        │ │·Token生成 │  │
│  └──────┬──────┘ └──────────────┘ └────────┘ └───────────┘  │
│         │                                                     │
│         ▼  /data/status-*.json + /data/nodes.json             │
└───────────────────────────────┬───────────────────────────────┘
                                │ HTTP POST (HMAC / Token)
              ┌─────────────────┼─────────────────────┐
              │                 │                     │
     ┌────────┴──────┐ ┌───────┴───────┐ ┌──────────┴─────┐
     │ 总部 VyOS      │ │ 分支1 VyOS     │ │ 分支2 VyOS      │
     │ collector.py   │ │ branch_       │ │ branch_        │
     │ (wg dump+资源) │ │ agent.py      │ │ agent.py       │
     │ < 5MB RAM      │ │ < 5MB RAM     │ │ < 5MB RAM      │
     └───────────────┘ └───────────────┘ └────────────────┘
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/register` | POST | Agent 自注册（含 Token 验证） |
| `/report` | POST | Agent 定期上报（响应含动态配置+升级通知） |
| `/health` | GET | 健康检查 |
| `/api/tokens/generate` | POST | 生成一次性部署 Token |
| `/api/tokens` | GET | 列出所有 Token 及状态 |
| `/api/deploy/{token}` | GET | 返回 bash 一键部署脚本 |
| `/api/deploy/files/{name}` | GET | Agent 文件下载 |
| `/api/nodes` | GET | 已注册节点列表 |
| `/api/nodes/:id/approve` | POST | 审核通过 |
| `/api/nodes/:id/reject` | POST | 拒绝节点 |
| `/api/nodes/:id` | DELETE | 删除节点 |
| `/api/upgrade` | POST | 上传新版 Agent |
| `/api/upgrade/latest` | GET | Agent 下载新版本 |

## 项目结构

```
VyOmni/
├── agent/                    # VyOS 轻量 Agent
│   ├── agent_common.py       #   公共模块（注册/签名/上报/配置/升级）
│   ├── collector.py          #   总部 HQ Agent（wg dump + 系统资源）
│   └── branch_agent.py      #   分支 Agent（系统资源 + 接口流量）
├── server/                   # 监控平台（Linux 服务器）
│   ├── docker-compose.yml    #   一键编排（aggregator + nginx + alerter）
│   ├── Dockerfile            #   平台容器镜像
│   ├── aggregator.py         #   聚合器 API（注册/上报/Token/审核/升级/部署）
│   ├── alerter.py            #   Webhook 告警服务
│   └── download_assets.sh    #   下载离线前端资源
├── frontend/                 # 前端看板
│   ├── index.html
│   ├── css/dashboard.css     #   暗色/亮色双主题 + 响应式
│   ├── js/dashboard.js       #   拓扑/地图/动画/弹窗/节点管理/Token生成
│   └── assets/               #   ECharts 等离线资源
├── config/                   # 配置模板
│   ├── agent.conf            #   Agent 极简配置（server_url + register_token）
│   ├── secrets.json          #   平台密钥（register_token + admin_password）
│   ├── alert.json            #   告警通道配置
│   ├── config.json           #   平台运行配置 + 地理位置
│   └── wireguard_multi_branch.md  # WireGuard eBGP 组网配置参考
├── nginx/
│   └── monitor.conf          #   Nginx 站点配置
├── scripts/                  # 部署与运维脚本
│   ├── deploy_server.sh      #   Linux 服务器部署
│   ├── deploy_vyos_agent.sh  #   VyOS Agent 手动部署（离线备选）
│   ├── download_assets.sh    #   离线资源下载
│   ├── healthcheck.sh        #   健康检查
│   ├── watchdog.sh           #   进程守护
│   └── logrotate_monitor.conf
├── docs/
│   └── AGENT_DEPLOY_GUIDE.md #   Agent 部署完整指南
├── PROJECT.md                # 项目描述（本文件）
├── README.md                 # 快速入门
└── WHITEPAPER.md             # 方案白皮书
```

## 网络规划

| 节点 | AS 号 | 隧道地址 | LAN 网段 |
|------|-------|----------|----------|
| 总部 | 65500 | 10.10.0.1/24 | 192.168.1.0/24 |
| 上海 | 65501 | 10.10.0.11/24 | 192.168.11.0/24 |
| 北京 | 65502 | 10.10.0.12/24 | 192.168.12.0/24 |
| 广州 | 65503 | 10.10.0.13/24 | 192.168.13.0/24 |
| 成都 | 65504 | 10.10.0.14/24 | 192.168.14.0/24 |
| 监控服务器 | — | 部署在总部 LAN 或独立网络 | — |
