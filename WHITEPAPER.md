# VyOmni 方案白皮书

> 版本: v0.3.0 | 架构: 集中式监控平台 + 轻量 Agent

## 1. 方案概述

VyOmni 是面向 VyOS 多分支 WireGuard 组网环境的一体化监控解决方案。

### 1.1 设计目标

- **零侵入 VyOS**：所有 VyOS 节点仅运行单文件 Python Agent，不安装容器/额外服务
- **集中式平台**：聚合、展示、告警全部在独立 Linux 服务器
- **全离线**：看板无需外部 CDN，ECharts 等资源本地打包
- **安全上报**：HMAC-SHA256 签名 + 时间窗口

### 1.2 核心价值

| 痛点 | 解决方案 |
|------|----------|
| VyOS 资源有限 | Agent < 5MB RAM |
| 容器兼容性差 | 不使用容器，纯 systemd |
| 端口映射 bug | 不存在，平台在标准 Linux |
| 多分支管理 | 集中看板，统一视图 |

## 2. 系统架构

### 2.1 部署架构

```
            ┌─────────────────────────────────────────────────┐
            │         Linux 监控服务器 (Docker Compose)         │
            │                                                  │
            │  ┌────────────┐  ┌───────┐  ┌────────────────┐  │
            │  │ Aggregator │  │Alerter│  │ Nginx + 前端   │  │
            │  │ :9100      │  │       │  │ :8080          │  │
            │  └─────┬──────┘  └───┬───┘  └────────────────┘  │
            │        │ JSON        │ 读取                       │
            │        ▼             ▼                            │
            │  ┌────────────────────────────────┐              │
            │  │  /data/status-tunnel.json      │              │
            │  │  /data/status-branches.json    │              │
            │  └────────────────────────────────┘              │
            └──────────────────┬───────────────────────────────┘
                               │ HTTP POST /report
            ┌──────────────────┼──────────────────────────┐
            │                  │                          │
   ┌────────┴────────┐  ┌─────┴───────┐  ┌──────────────┴──┐
   │ 总部 VyOS       │  │ 分支 VyOS   │  │ 分支 VyOS      │
   │                 │  │             │  │                │
   │ collector.py    │  │ branch_     │  │ branch_        │
   │ (wg dump+系统)  │  │ agent.py    │  │ agent.py       │
   │ systemd 管理    │  │ systemd     │  │ systemd        │
   └─────────────────┘  └─────────────┘  └────────────────┘
```

### 2.2 数据流

```
1. HQ Agent: wg show all dump → 解析 → HTTP POST /report (role=hq)
2. Branch Agent: /proc/* 读取 → HTTP POST /report (role=branch)
3. Aggregator: 验签 → 聚合 → 写入 status-*.json
4. Nginx: 静态服务 status-*.json → 前端轮询展示
5. Alerter: 读取 status-*.json → 条件判定 → Webhook 推送
```

### 2.3 安全模型

- Agent → Aggregator: HMAC-SHA256(timestamp + body)
- 时间窗口: ±60 秒防重放
- Aggregator 仅接受 POST /report，无管理接口暴露
- 可选: VPN/WireGuard 隧道内传输（无需额外加密）

## 3. 组件详情

### 3.1 HQ Agent (collector.py)

| 属性 | 说明 |
|------|------|
| 部署位置 | 总部 VyOS |
| 运行方式 | systemd 单进程 |
| 采集内容 | wg show all dump + CPU/内存 |
| 上报频率 | 5 秒（可配置） |
| 依赖 | Python 3 标准库（无第三方包） |
| 资源消耗 | < 5MB RAM, < 1% CPU |

### 3.2 Branch Agent (branch_agent.py)

| 属性 | 说明 |
|------|------|
| 部署位置 | 各分支 VyOS |
| 运行方式 | systemd 单进程 |
| 采集内容 | CPU/内存/负载/接口流量 |
| 上报频率 | 10 秒（可配置） |
| 依赖 | Python 3 标准库 |
| 资源消耗 | < 5MB RAM, < 1% CPU |

### 3.3 Aggregator (aggregator.py)

| 属性 | 说明 |
|------|------|
| 部署位置 | Linux 监控服务器 |
| 运行方式 | Docker 容器 |
| 功能 | 接收上报 → 验签 → 聚合 → 写JSON |
| 端口 | 9100 |
| 依赖 | Python 3 标准库 |

### 3.4 Alerter (alerter.py)

| 属性 | 说明 |
|------|------|
| 告警条件 | 隧道离线(180s) / Agent心跳超时(60s) / CPU>90% |
| 推送渠道 | 钉钉 / 企业微信 / 通用 Webhook |
| 去重 | 同一告警 30 分钟内不重复 |

### 3.5 前端看板

| 属性 | 说明 |
|------|------|
| 技术 | 纯静态 HTML + ECharts |
| 数据源 | 轮询 /api/tunnel + /api/branches |
| 维度1 | Peer 隧道状态矩阵 |
| 维度2 | 分支资源负载矩阵 |
| 离线 | 所有 JS/CSS/字体本地打包 |

## 4. 配置参考

### 4.1 Agent 配置 (config.conf)

```ini
# 监控平台地址
server_url = http://192.168.1.100:9100

# 上报间隔（秒）
report_interval = 5

# HMAC 密钥（须与平台一致）
hmac_key = your-secret-key

# 节点标识
hostname = HQ-VyOS
role = hq
```

### 4.2 告警配置 (alert.json)

```json
{
    "enabled": true,
    "channels": [
        {
            "type": "dingtalk",
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
        }
    ]
}
```

## 5. 运维指南

### 5.1 日常命令

```bash
# 监控服务器
cd /opt/vyomni/server
docker compose logs -f          # 查看日志
docker compose restart          # 重启全部
docker compose up -d --build    # 重新构建

# VyOS Agent
systemctl status vyomni-agent   # 状态
journalctl -u vyomni-agent -f   # 日志
systemctl restart vyomni-agent  # 重启
```

### 5.2 扩容

添加新分支：
1. 部署 branch_agent.py 到新节点
2. 配置 config.conf（branch_id 唯一）
3. 启动 systemd 服务
4. 看板自动显示新分支（无需重启平台）

## 6. 版本历史

| 版本 | 变更 |
|------|------|
| v0.3.0 | 架构重构：VyOS容器部署 → 独立Linux平台 + 轻量Agent |
| v0.2.0 | VyOS Podman 容器化部署 |
| v0.1.0 | 初始原型 |
