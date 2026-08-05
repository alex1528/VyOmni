# 线路中断告警功能 — 实施说明

## 架构：双层告警

```
HQ 节点 (VyOS)
├── vyomni-agent.service        (现有，纯采集，未改动)
└── vyomni-line-alert.service   ★新增 — 本地线路中断检测
      ├── link 状态: /sys/class/net/{iface}/operstate
      ├── ping 探测: 绑接口名 或 绑源IP (可配)
      ├── wg 隧道: wg show all dump 解析握手
      └── 状态机防抖 → 本地直发 webhook (+可选回传Server)

独立 Server (Docker)
└── alerter 容器 (增强)
      ├── 修复 last_handshake_seconds_ago 字段Bug
      ├── 新增恢复通知 + 状态机防抖
      └── 职责收敛：节点级失联(HQ心跳/分支离线/CPU)
```

**核心设计**：HQ 本地告警不经 Server，只要 HQ 还有任一条通往公网的线路即可发出，抗上报链路中断。

## 新增/修改文件

| 文件 | 变更 |
|------|------|
| `agent/line_alert.py` | 新增 — HQ 本地告警脚本 (纯 stdlib) |
| `config/line_alert.conf` | 新增 — HQ 告警配置示例 |
| `scripts/deploy_line_alert.sh` | 新增 — 独立手动部署脚本 |
| `server/alerter.py` | 重写 — 修 Bug + 恢复通知 + 职责收敛 |
| `server/aggregator.py` | line_alert.py 加入下载白名单; HQ 一键部署自动下发; 新增 `/api/line-alert`(回传) 与 `/api/line-alerts`(看板读取) |
| `frontend/` | 新增"HQ 线路告警"面板（index.html + dashboard.js + dashboard.css），读取 `/api/line-alerts`，每 30s 刷新，有中断时红色脉冲徽章 |

## 部署方式（两种，均支持）

### ① 一键部署（HQ 角色 token 自动包含）
```bash
curl -sL http://<server>:9100/api/deploy/tk_xxx | bash
# HQ 角色会自动下发 line_alert.py + 生成配置模板(enabled=false) + 启动服务
```

### ② 独立手动部署（补装场景）
```bash
bash deploy_line_alert.sh http://<server>:9100
```

部署后编辑配置并启用：
```bash
vi /opt/vyomni-agent/line_alert.conf   # 设 enabled=true，填出口/webhook
systemctl restart vyomni-line-alert
journalctl -u vyomni-line-alert -f
```

## 配置说明 (line_alert.conf)

- **webhooks[]** — 通知渠道（可配多个，全部发送）
  - `enabled`: 单渠道开关，`false` 则跳过该渠道（缺省 `true`）——可预置多个渠道，按需逐个启停
  - `type`: `dingtalk` / `wecom` / `feishu` / `lark` / `telegram` / `generic`
  - `url`: 机器人 webhook 地址
  - Telegram 额外需 `chat_id`，url 为 `https://api.telegram.org/bot<TOKEN>/sendMessage`
  - 飞书/Lark 用自定义机器人 webhook（文本消息 `msg_type=text`）
- **exports[]** — 物理出口监控
  - `bind_mode`（默认 `auto`）: `auto`(自动取接口IPv4绑源，**推荐**) | `src_ip`(手填 `bind_src_ip` 绑源) | `interface`(绑接口名，**VyOS PBR 环境会误报，不推荐**) | `none`(不绑，走默认路由)
  - `ping_target`: 探测目标（对端网关/公网IP）
  - `fail_threshold`: 连续失败几次才告警（防抖）
  - 探测使用真实 `/bin/ping`（绕过 VyOS op-mode 包装器）；若装了 `fping` 优先用 fping（`-S` 绑源 / `-I` 绑接口）
- **tunnels** — wg 隧道监控
  - `enabled`: **false 则完全不执行隧道告警**（仅监控物理出口）
  - `watch_list`: 空=监控全部 peer；填公钥=仅监控指定
  - `aliases`: peer公钥 → 可读名映射
- **server_report.enabled** — true 则同时回传 Server 看板

### Server 端 alert.json
- `channels[]` — 同样支持 dingtalk/wecom/feishu/lark/telegram/generic
  - 每个渠道同样支持 `enabled` 单渠道开关（`false` 跳过，缺省 `true`）
- `monitors.tunnel_handshake` — **false 则 Server 端不执行隧道握手告警**（交由 HQ 本地负责，避免重复）

## ⚠️ VyOS 环境注意事项

1. **ping 出口绑定（实测重要结论）**：VyOS 1.5 多出口/策略路由环境下：
   - ❌ `-I 接口名`（如 `ping -I eth1`）：只绑出接口不触发 PBR，回程选路错误 → **假丢包误报**
   - ✅ `-I 源IP`（如 `ping -I 112.82.212.174`）：命中 PBR 正确选路 → 真实结果
   - 因此 `bind_mode` 默认 `auto`（自动取接口 IPv4 作源地址），**切勿用 `interface`**
   - 另注：VyOS 的 `ping` 是 op-mode 包装器（`ping is aliased to _vyatta_op_run ping`），不认 `-I` 短参数；脚本已固定调用真实 `/bin/ping` 绕过。
2. **fping 优先**：VyOS 自带 `fping`（实测 5.1），脚本优先用它，`-S` 绑源地址、`-t` 毫秒级超时更精准。
3. **接口无 IPv4**：`bind_mode=auto` 下若接口取不到 IPv4（如未配地址），该出口直接判为异常告警。
4. **link 状态**：`operstate` 对物理链路 down 最可靠；上游故障（本端 up 但对端不通）需靠 ping 补充。
5. **wg show 权限**：line_alert.py 需 root 运行（systemd 默认 root）才能执行 `wg show`。

## 联调建议

1. Server 端先 rebuild：`cd server && docker compose up -d --build`
2. HQ 重新执行一键部署或跑 deploy_line_alert.sh
3. 拔一条出口网线或 `ip link set eth1 down` 验证告警
4. 恢复后验证恢复通知
