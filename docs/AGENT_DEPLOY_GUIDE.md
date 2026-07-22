# VyOmni Agent 部署指南

> 适用于所有 VyOS 节点（总部 + 分支）的 Agent 部署完整步骤

---

## 一、架构概述

```
┌────────────────────────────────────────────────────────┐
│              Linux 监控服务器 (Docker Compose)           │
│     Aggregator :9100  ←── 接收 Agent 注册和上报         │
│     Nginx :8080       ←── 看板展示                      │
│     Alerter           ←── 告警推送                      │
└──────────────────────────────┬─────────────────────────┘
                               │ HTTP POST
          ┌────────────────────┼────────────────────┐
          │                    │                    │
 ┌────────┴────────┐  ┌───────┴───────┐  ┌────────┴───────┐
 │ 总部 VyOS       │  │ 分支A VyOS    │  │ 分支B VyOS     │
 │ collector.py    │  │ branch_       │  │ branch_        │
 │ (HQ Agent)     │  │ agent.py      │  │ agent.py       │
 └─────────────────┘  └───────────────┘  └────────────────┘

```

**所有 VyOS 节点部署流程完全相同**，Agent 启动时自动检测角色（总部/分支）。

---

## 二、Agent 文件说明

| 文件 | 说明 | 是否必需 |
| --- | --- | --- |
| `agent_common.py` | 公共模块（注册/签名/上报/配置/升级） | 必需 |
| `collector.py` | 总部 HQ Agent 主程序 | 总部必需 |
| `branch_agent.py` | 分支 Branch Agent 主程序 | 分支必需 |
| `config.conf` | 极简配置文件（仅 2 行） | 必需 |
| `.credentials.json` | 注册凭证（首次注册后自动生成） | 自动生成 |

---

## 三、配置文件 (config.conf)

Agent 只需要最简配置，所有其他参数（密钥、间隔、角色）通过自注册自动获取：

```ini
# VyOmni Agent 极简配置
# 仅需修改这两项，其余由自注册机制自动完成

server_url = http://192.168.1.100:9100
register_token = vyomni-2025

```

| 参数 | 说明 | 示例 |
| --- | --- | --- |
| `server_url` | Linux 监控平台聚合器地址 | `http://192.168.1.100:9100` |
| `register_token` | 全局注册令牌（所有节点共用） | `vyomni-2025` |

> **重要**：`register_token` 必须与 Linux 服务器端 `secrets.json` 中的 `register_token` 值一致。

---

## 三B、推荐方式：curl|bash 一键部署（首选）

> 此方式适用于所有 VyOS 节点（总部/分支），无需区分角色，无需手动复制文件。

### 操作步骤

**1. 管理员在看板生成部署 Token**

看板 → 节点管理 → [+ 新增节点] → 输入名称 + 选择角色 → [生成部署命令]

**2. 复制一键部署命令到目标 VyOS 执行**

```bash
curl -sL http://192.168.1.100:9100/api/deploy/tk_a8f3e2b1c9d4 | bash
```

**3. 自动完成的操作**

脚本将自动执行以下步骤：
1. 创建 `/opt/vyomni-agent/` 目录
2. 从平台下载 `agent_common.py` + 对应主程序（`collector.py` 或 `branch_agent.py`）
3. 写入 `config.conf`（含该 Token）
4. 创建 `vyomni-agent.service` systemd 服务
5. 启动服务 → Agent 自动注册 → 上线

**4. 管理员审核**

回到看板 → 节点管理 → 新节点显示为 "pending" → 点击 ✅ 审核通过

### Token 说明

| 属性 | 值 |
|------|-----|
| 格式 | `tk_` + 12位随机hex |
| 有效期 | 24小时 |
| 使用次数 | 一次性（注册后作废） |
| 注册后通信 | 使用平台返回的专属 HMAC 密钥 |

### 预期输出

```
[VyOmni Deploy] Downloading agent files...
[VyOmni Deploy] Writing config...
[VyOmni Deploy] Creating systemd service...
[VyOmni Deploy] Starting service...
[VyOmni Deploy] ✅ Deployment complete!
[VyOmni Deploy] Check: systemctl status vyomni-agent
```

---

> 以下为**手动部署方式**（适用于离线环境或需要自定义的场景）：

## 四、总部 VyOS Agent 部署

### 4.1 前提条件

- VyOS 1.5.0 已配置 WireGuard 接口（wg0）
- Python 3 已安装（VyOS 自带）
- 网络可达 Linux 监控服务器的 9100 端口

### 4.2 部署步骤

```bash
# ═══════════════════════════════════════════════════
# 步骤 1：创建安装目录
# ═══════════════════════════════════════════════════
sudo mkdir -p /opt/vyomni-agent

# ═══════════════════════════════════════════════════
# 步骤 2：复制 Agent 文件（从开发机/Git 仓库）
# ═══════════════════════════════════════════════════
# 方式A：从开发机 SCP 传输
scp agent/agent_common.py  vyos@<HQ_IP>:/opt/vyomni-agent/
scp agent/collector.py     vyos@<HQ_IP>:/opt/vyomni-agent/

# 方式B：直接在 VyOS 上 git clone
cd /opt && git clone <repo_url> vyomni-repo
cp vyomni-repo/agent/agent_common.py /opt/vyomni-agent/
cp vyomni-repo/agent/collector.py /opt/vyomni-agent/

# ═══════════════════════════════════════════════════
# 步骤 3：创建配置文件
# ═══════════════════════════════════════════════════
cat > /opt/vyomni-agent/config.conf << 'EOF'
server_url = http://192.168.1.100:9100
register_token = vyomni-2025
EOF

# ═══════════════════════════════════════════════════
# 步骤 4：创建 systemd 服务
# ═══════════════════════════════════════════════════
sudo tee /etc/systemd/system/vyomni-agent.service > /dev/null << 'EOF'
[Unit]
Description=VyOmni HQ Agent
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/vyomni-agent/collector.py
WorkingDirectory=/opt/vyomni-agent
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# ═══════════════════════════════════════════════════
# 步骤 5：启动服务
# ═══════════════════════════════════════════════════
sudo systemctl daemon-reload
sudo systemctl enable vyomni-agent
sudo systemctl start vyomni-agent

# ═══════════════════════════════════════════════════
# 步骤 6：验证
# ═══════════════════════════════════════════════════
systemctl status vyomni-agent
journalctl -u vyomni-agent -n 20

```

### 4.3 预期输出

```
[INFO] VyOmni HQ Agent v2.0.0 starting...
[INFO] Server: http://192.168.1.100:9100
[INFO] Detected role: hq (wireguard interfaces found)
[INFO] Registering node...
[INFO] Registration successful. node_id=hq-vyos-a1b2c3
[INFO] Agent active. node_id=hq-vyos-a1b2c3, interval=5s
[10:30:15] peers=2 cpu=5.2% mem=32.1% -> OK

```

### 4.4 总部 Agent 采集内容

| 采集项 | 数据来源 | 说明 |
| --- | --- | --- |
| WireGuard Peers | `wg show all dump` | 隧道状态、握手时间、流量 |
| CPU 使用率 | `/proc/stat` | 百分比 |
| 内存使用率 | `/proc/meminfo` | 百分比 |

---

## 五、分支 VyOS Agent 部署

### 5.1 前提条件

- VyOS 1.5.0 已配置 WireGuard 接口连接总部
- Python 3 已安装
- 网络可达 Linux 监控服务器 9100 端口（通过隧道或直连）

### 5.2 部署步骤

```bash
# ═══════════════════════════════════════════════════
# 步骤 1：创建安装目录
# ═══════════════════════════════════════════════════
sudo mkdir -p /opt/vyomni-agent

# ═══════════════════════════════════════════════════
# 步骤 2：复制 Agent 文件
# ═══════════════════════════════════════════════════
scp agent/agent_common.py   vyos@<BRANCH_IP>:/opt/vyomni-agent/
scp agent/branch_agent.py   vyos@<BRANCH_IP>:/opt/vyomni-agent/

# ═══════════════════════════════════════════════════
# 步骤 3：创建配置文件（与总部完全相同）
# ═══════════════════════════════════════════════════
cat > /opt/vyomni-agent/config.conf << 'EOF'
server_url = http://192.168.1.100:9100
register_token = vyomni-2025
EOF

# ═══════════════════════════════════════════════════
# 步骤 4：创建 systemd 服务
# ═══════════════════════════════════════════════════
sudo tee /etc/systemd/system/vyomni-agent.service > /dev/null << 'EOF'
[Unit]
Description=VyOmni Branch Agent
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/vyomni-agent/branch_agent.py
WorkingDirectory=/opt/vyomni-agent
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# ═══════════════════════════════════════════════════
# 步骤 5：启动服务
# ═══════════════════════════════════════════════════
sudo systemctl daemon-reload
sudo systemctl enable vyomni-agent
sudo systemctl start vyomni-agent

# ═══════════════════════════════════════════════════
# 步骤 6：验证
# ═══════════════════════════════════════════════════
systemctl status vyomni-agent
journalctl -u vyomni-agent -n 20

```

### 5.3 预期输出

```
[INFO] VyOmni Branch Agent v2.0.0 starting...
[INFO] Server: http://192.168.1.100:9100
[INFO] Detected role: branch
[INFO] Registering node...
[INFO] Registration successful. node_id=branch-shanghai-d4e5f6
[INFO] Agent active. node_id=branch-shanghai-d4e5f6, interval=10s
[10:30:25] cpu=12.3% mem=45.6% -> OK

```

### 5.4 分支 Agent 采集内容

| 采集项 | 数据来源 | 说明 |
| --- | --- | --- |
| CPU 使用率 | `/proc/stat` | 百分比 |
| 内存使用率 | `/proc/meminfo` | 百分比 |
| 系统负载 | `/proc/loadavg` | 1分钟平均 |
| 网络接口流量 | `/proc/net/dev` | 各接口 rx/tx 字节 |

---

## 六、自注册流程

```
Agent 启动
    │
    ├── 检查 .credentials.json 是否存在
    │       ├── 存在 → 加载凭证 → 进入上报循环
    │       └── 不存在 → 执行首次注册
    │               ├── 检测角色（有 wg 接口 = HQ，无 = Branch）
    │               ├── 生成 node_id（hostname + MAC hash）
    │               ├── POST /register
    │               ├── 平台返回 {hmac_key, report_interval, status: "pending"}
    │               └── 保存到 .credentials.json
    │
    └── 上报循环
            ├── 采集数据
            ├── HMAC 签名
            ├── POST /report
            └── 处理响应
                    ├── config_update → 热更新
                    ├── upgrade_available → 自动升级
                    └── status: rejected → 停止

```

---

## 七、节点状态与审核

| 状态 | 说明 | Agent 行为 | 看板展示 |
| --- | --- | --- | --- |
| `pending` | 新注册待审核 | 继续上报 | 不展示 |
| `approved` | 审核通过 | 正常上报 | 展示数据 |
| `rejected` | 已拒绝 | 停止退出 | 不展示 |

管理员在看板的"节点管理"面板中审核新节点。

---

## 八、动态配置下发

平台可通过 report 响应下发配置更新，Agent 立即应用无需重启：

| 可下发项 | 说明 |
| --- | --- |
| `report_interval` | 上报间隔（秒） |
| `capabilities` | 采集能力列表 |
| `custom_labels` | 自定义标签 |
| `status` | 变更状态（rejected → Agent 退出） |

---

## 九、远程升级

平台推送新版本时，Agent 自动：

1. 下载新脚本 → 2. SHA256 校验 → 3. 备份旧版(.bak) → 4. 替换 → 5. systemd 重启

校验失败则不替换，继续使用当前版本。

---

## 十、运维命令速查

```bash
# 状态
systemctl status vyomni-agent
journalctl -u vyomni-agent -f

# 管理
sudo systemctl restart vyomni-agent
sudo systemctl stop vyomni-agent

# 排障
cat /opt/vyomni-agent/.credentials.json
cat /opt/vyomni-agent/config.conf
python3 /opt/vyomni-agent/collector.py   # 前台调试

# 重新注册
sudo rm /opt/vyomni-agent/.credentials.json
sudo systemctl restart vyomni-agent

# 连通性测试
curl -s http://192.168.1.100:9100/health

```

---

## 十一、批量部署

准备 `nodes.txt`：

```
10.10.0.1,hq
10.10.0.11,branch
10.10.0.12,branch
10.10.0.13,branch

```

执行：

```bash
#!/bin/bash
SERVER="http://192.168.1.100:9100"
TOKEN="vyomni-2025"

while IFS=',' read -r ip role; do
    SCRIPT=$( [ "$role" = "hq" ] && echo "collector.py" || echo "branch_agent.py" )
    
    scp agent/agent_common.py agent/$SCRIPT vyos@${ip}:/opt/vyomni-agent/
    
    ssh vyos@${ip} "
        echo 'server_url = $SERVER' > /opt/vyomni-agent/config.conf
        echo 'register_token = $TOKEN' >> /opt/vyomni-agent/config.conf
        sudo systemctl restart vyomni-agent
    "
    echo "✓ $ip ($role)"
done < nodes.txt

```

---

## 十二、常见问题

| 问题 | 原因 | 解决 |
| --- | --- | --- |
| Registration failed | 平台不可达或 token 错误 | 检查 server_url；curl /health |
| wg dump failed | 无 wg 接口或权限不足 | 确认 wg0 存在 |
| 持续 FAIL | 签名验证失败 | 删除 .credentials.json 重新注册 |
| Agent 启动后退出 | 节点被 reject | 在看板审核通过 |

---

## 十三、安全建议

1. `register_token` 定期轮换（已注册节点不受影响，使用专属密钥）
2. `.credentials.json` 权限设为 `chmod 600`
3. 若 Agent 经公网上报，建议通过 WireGuard 隧道传输
4. 平台 9100 端口仅对隧道网段开放（如 10.10.0.0/24）

