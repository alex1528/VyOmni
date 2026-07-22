# VyOmni 快速入门

> VyOS WireGuard 隧道组网 + 一体化监控系统

## 架构

```
Linux 监控服务器（Docker Compose）
  └── Aggregator + Nginx + Alerter + Dashboard
            ▲
            │ HTTP POST（HMAC 签名）
  ┌─────────┼─────────┐
  │         │         │
VyOS HQ   VyOS 分支  VyOS 分支
Agent     Agent      Agent
```

- VyOS 节点仅运行单文件 Agent（< 5MB RAM）
- 所有重型服务在独立 Linux 服务器

---

## 一、部署监控服务器

```bash
git clone <repo_url> /opt/vyomni && cd /opt/vyomni

# 准备配置
cd server
cp ../config/secrets.json .
cp ../config/alert.json .
cp ../config/config.json .

# 下载离线前端资源
bash download_assets.sh

# 启动
docker compose up -d --build
```

平台地址：
- 看板：`http://<server-ip>:8080`
- 聚合器 API：`http://<server-ip>:9100`

---

## 二、部署 VyOS Agent（推荐：一键部署）

### 方式 1：看板生成 Token → curl|bash（推荐）

1. 打开看板 → 节点管理 → **[+ 新增节点]**
2. 输入名称、选择角色（HQ/Branch）→ **[生成部署命令]**
3. 复制生成的命令，到目标 VyOS 终端粘贴执行：

```bash
curl -sL http://192.168.1.100:9100/api/deploy/tk_xxxxxxxxxxxx | bash
```

一条命令完成：创建目录 → 下载 Agent → 写配置 → 创建服务 → 启动 → 自注册 → 上线。

4. 回到看板 → 节点管理 → 审核新节点 ✅

### 方式 2：手动部署（离线/高级场景）

```bash
# 在 VyOS 上
sudo mkdir -p /opt/vyomni-agent
# 复制 Agent 文件
scp agent/agent_common.py agent/collector.py vyos@<ip>:/opt/vyomni-agent/
# 写配置
cat > /opt/vyomni-agent/config.conf << 'EOF'
server_url = http://192.168.1.100:9100
register_token = tk_xxxxxxxxxxxx
EOF
# 创建服务并启动
sudo bash scripts/deploy_vyos_agent.sh hq
```

详见 [Agent 部署完整指南](docs/AGENT_DEPLOY_GUIDE.md)

---

## 三、验证

```bash
# 服务器端
docker compose logs -f aggregator   # 查看注册/上报日志
curl http://localhost:8080/          # 看板
curl http://localhost:9100/health    # 聚合器健康

# VyOS Agent 端
systemctl status vyomni-agent
journalctl -u vyomni-agent -f
```

---

## 四、API 端点速查

| 端点 | 方法 | 说明 |
|------|------|------|
| `/register` | POST | Agent 自注册 |
| `/report` | POST | Agent 上报数据 |
| `/health` | GET | 健康检查 |
| `/api/tokens/generate` | POST | 生成一次性部署 Token |
| `/api/tokens` | GET | Token 列表 |
| `/api/deploy/{token}` | GET | 返回一键部署 bash 脚本 |
| `/api/deploy/files/{name}` | GET | Agent 文件下载 |
| `/api/nodes` | GET | 已注册节点列表 |
| `/api/nodes/:id/approve` | POST | 审核通过 |
| `/api/nodes/:id/reject` | POST | 拒绝 |
| `/api/upgrade` | POST | 上传新版 Agent |

---

## 五、目录结构

详见 [PROJECT.md](PROJECT.md)

## 六、方案白皮书

详见 [WHITEPAPER.md](WHITEPAPER.md)
