# VyOS 1.5.0 多分支 WireGuard 星形组网配置

> 隧道网段：10.10.0.0/24（支持最多 253 个节点）
> 所有分支共用总部单 wg0 接口，总部为 Listen 端，分支为 Initiator 端

## 网段规划

| 节点 | 角色 | 隧道地址 | LAN 网段 | 说明 |
|------|------|----------|----------|------|
| 总部 | Listen | 10.10.0.1/24 | 192.168.1.0/24 | 公网 IP 203.0.113.1 |
| 上海 | Initiator | 10.10.0.11/24 | 192.168.11.0/24 | 无公网 |
| 北京 | Initiator | 10.10.0.12/24 | 192.168.12.0/24 | 无公网 |
| 广州 | Initiator | 10.10.0.13/24 | 192.168.13.0/24 | 无公网 |
| 成都 | Initiator | 10.10.0.14/24 | 192.168.14.0/24 | 无公网 |

## 密钥生成（每节点执行一次）

```bash
# 在 VyOS 操作模式下
generate pki wireguard key-pair

# 输出示例：
# Private key: <私钥base64>
# Public key:  <公钥base64>
```

---

## 总部配置（完整）

```bash
configure

# ============================================================
# WireGuard 接口（总部 Listen）
# ============================================================
set interfaces wireguard wg0 address '10.10.0.1/24'
set interfaces wireguard wg0 description 'WG-Star-Hub'
set interfaces wireguard wg0 private-key '<总部私钥>'
set interfaces wireguard wg0 listen-port '51820'

# ============================================================
# Peer: 上海分部
# ============================================================
set interfaces wireguard wg0 peer shanghai public-key '<上海公钥>'
set interfaces wireguard wg0 peer shanghai allowed-ips '10.10.0.11/32'
set interfaces wireguard wg0 peer shanghai allowed-ips '192.168.11.0/24'
set interfaces wireguard wg0 peer shanghai description 'Branch-Shanghai'

# ============================================================
# Peer: 北京分部
# ============================================================
set interfaces wireguard wg0 peer beijing public-key '<北京公钥>'
set interfaces wireguard wg0 peer beijing allowed-ips '10.10.0.12/32'
set interfaces wireguard wg0 peer beijing allowed-ips '192.168.12.0/24'
set interfaces wireguard wg0 peer beijing description 'Branch-Beijing'

# ============================================================
# Peer: 广州分部
# ============================================================
set interfaces wireguard wg0 peer guangzhou public-key '<广州公钥>'
set interfaces wireguard wg0 peer guangzhou allowed-ips '10.10.0.13/32'
set interfaces wireguard wg0 peer guangzhou allowed-ips '192.168.13.0/24'
set interfaces wireguard wg0 peer guangzhou description 'Branch-Guangzhou'

# ============================================================
# Peer: 成都分部
# ============================================================
set interfaces wireguard wg0 peer chengdu public-key '<成都公钥>'
set interfaces wireguard wg0 peer chengdu allowed-ips '10.10.0.14/32'
set interfaces wireguard wg0 peer chengdu allowed-ips '192.168.14.0/24'
set interfaces wireguard wg0 peer chengdu description 'Branch-Chengdu'

# ============================================================
# 防火墙：放行 UDP/51820 入站
# ============================================================
# VyOS 1.5.0 防火墙语法（请根据实际版本验证）
set firewall ipv4 input filter rule 20 action 'accept'
set firewall ipv4 input filter rule 20 protocol 'udp'
set firewall ipv4 input filter rule 20 destination port '51820'
set firewall ipv4 input filter rule 20 inbound-interface name 'eth0'
set firewall ipv4 input filter rule 20 description 'Allow WireGuard Inbound'

# ============================================================
# BGP 动态路由（eBGP，总部 AS 65500）
# ============================================================
set protocols bgp system-as '65500'
set protocols bgp parameters bestpath as-path multipath-relax
set protocols bgp parameters minimum-holdtime '30'
set protocols bgp address-family ipv4-unicast network 192.168.1.0/24

# Peer: 上海 (AS 65501)
set protocols bgp neighbor 10.10.0.11 remote-as '65501'
set protocols bgp neighbor 10.10.0.11 address-family ipv4-unicast
set protocols bgp neighbor 10.10.0.11 update-source 'wg0'
set protocols bgp neighbor 10.10.0.11 password 'changeme'
set protocols bgp neighbor 10.10.0.11 ebgp-multihop '2'
set protocols bgp neighbor 10.10.0.11 capability dynamic
set protocols bgp neighbor 10.10.0.11 capability route-refresh

# Peer: 北京 (AS 65502)
set protocols bgp neighbor 10.10.0.12 remote-as '65502'
set protocols bgp neighbor 10.10.0.12 address-family ipv4-unicast
set protocols bgp neighbor 10.10.0.12 update-source 'wg0'
set protocols bgp neighbor 10.10.0.12 password 'changeme'
set protocols bgp neighbor 10.10.0.12 ebgp-multihop '2'
set protocols bgp neighbor 10.10.0.12 capability dynamic
set protocols bgp neighbor 10.10.0.12 capability route-refresh

# Peer: 广州 (AS 65503)
set protocols bgp neighbor 10.10.0.13 remote-as '65503'
set protocols bgp neighbor 10.10.0.13 address-family ipv4-unicast
set protocols bgp neighbor 10.10.0.13 update-source 'wg0'
set protocols bgp neighbor 10.10.0.13 password 'changeme'
set protocols bgp neighbor 10.10.0.13 ebgp-multihop '2'
set protocols bgp neighbor 10.10.0.13 capability dynamic
set protocols bgp neighbor 10.10.0.13 capability route-refresh

# Peer: 成都 (AS 65504)
set protocols bgp neighbor 10.10.0.14 remote-as '65504'
set protocols bgp neighbor 10.10.0.14 address-family ipv4-unicast
set protocols bgp neighbor 10.10.0.14 update-source 'wg0'
set protocols bgp neighbor 10.10.0.14 password 'changeme'
set protocols bgp neighbor 10.10.0.14 ebgp-multihop '2'
set protocols bgp neighbor 10.10.0.14 capability dynamic
set protocols bgp neighbor 10.10.0.14 capability route-refresh

# ============================================================
# 可选：分支间互通路由（星形中心转发）
# 如果分支之间也需要互通，需在总部启用转发
# ============================================================
set firewall ipv4 forward filter default-action 'accept'
# 或更细粒度：
# set firewall ipv4 forward filter rule 10 action 'accept'
# set firewall ipv4 forward filter rule 10 inbound-interface name 'wg0'
# set firewall ipv4 forward filter rule 10 outbound-interface name 'wg0'

commit
save
exit
```

---

## 分支配置模板（以上海为例）

```bash
configure

# ============================================================
# WireGuard 接口（分支 Initiator）
# ============================================================
set interfaces wireguard wg0 address '10.10.0.11/24'
set interfaces wireguard wg0 description 'WG-To-HQ'
set interfaces wireguard wg0 private-key '<上海私钥>'

# ============================================================
# Peer: 总部（必须配置 endpoint + keepalive）
# ============================================================
set interfaces wireguard wg0 peer hq public-key '<总部公钥>'
set interfaces wireguard wg0 peer hq address '203.0.113.1'
set interfaces wireguard wg0 peer hq port '51820'
set interfaces wireguard wg0 peer hq allowed-ips '10.10.0.0/24'
set interfaces wireguard wg0 peer hq allowed-ips '192.168.1.0/24'
set interfaces wireguard wg0 peer hq allowed-ips '192.168.12.0/24'
set interfaces wireguard wg0 peer hq allowed-ips '192.168.13.0/24'
set interfaces wireguard wg0 peer hq allowed-ips '192.168.14.0/24'
set interfaces wireguard wg0 peer hq persistent-keepalive '25'
set interfaces wireguard wg0 peer hq description 'HQ-Hub'

# ============================================================
# BGP 动态路由（eBGP，上海 AS 65501，总部 AS 65500）
# ============================================================
set protocols bgp system-as '65501'
set protocols bgp parameters bestpath as-path multipath-relax
set protocols bgp parameters minimum-holdtime '30'
set protocols bgp address-family ipv4-unicast network 192.168.11.0/24

# Peer: 总部 (AS 65500)
set protocols bgp neighbor 10.10.0.1 remote-as '65500'
set protocols bgp neighbor 10.10.0.1 address-family ipv4-unicast
set protocols bgp neighbor 10.10.0.1 update-source 'wg0'
set protocols bgp neighbor 10.10.0.1 password 'changeme'
set protocols bgp neighbor 10.10.0.1 ebgp-multihop '2'
set protocols bgp neighbor 10.10.0.1 capability dynamic
set protocols bgp neighbor 10.10.0.1 capability route-refresh

commit
save
exit
```

### 分支 allowed-ips 说明

如果**分支之间不需要互通**（仅需访问总部），分支 peer 的 allowed-ips 可简化为：
```bash
set interfaces wireguard wg0 peer hq allowed-ips '10.10.0.1/32'
set interfaces wireguard wg0 peer hq allowed-ips '192.168.1.0/24'
```

如果**需要分支互通**（经总部中转），则 allowed-ips 必须包含所有对端网段（如上述完整配置）。

---

## 各分支配置差异汇总

| 分支 | AS 号 | wg0 address | 本地 LAN | allowed-ips 中不含自己 |
|------|-------|-------------|----------|------------------------|
| 上海 | 65501 | 10.10.0.11/24 | 192.168.11.0/24 | 10.10.0.0/24, 192.168.1/12/13/14.0/24 |
| 北京 | 65502 | 10.10.0.12/24 | 192.168.12.0/24 | 10.10.0.0/24, 192.168.1/11/13/14.0/24 |
| 广州 | 65503 | 10.10.0.13/24 | 192.168.13.0/24 | 10.10.0.0/24, 192.168.1/11/12/14.0/24 |
| 成都 | 65504 | 10.10.0.14/24 | 192.168.14.0/24 | 10.10.0.0/24, 192.168.1/11/12/13.0/24 |

---

## 验证命令

```bash
# 查看隧道状态
show interfaces wireguard wg0

# 查看详细 dump（采集器数据来源）
sudo wg show all dump

# 测试连通性（从总部 ping 分支隧道地址）
ping 10.10.0.11
ping 192.168.11.1

# 查看路由表
show ip route

# 实时流量
monitor interfaces wireguard wg0
```

---

## 新增分支 SOP

当需要新增一个分支时：

1. **分支侧**：生成密钥对，记录公钥
2. **总部侧**：
   ```bash
   configure
   set interfaces wireguard wg0 peer <new-branch> public-key '<新分支公钥>'
   set interfaces wireguard wg0 peer <new-branch> allowed-ips '10.10.0.1X/32'
   set interfaces wireguard wg0 peer <new-branch> allowed-ips '192.168.1X.0/24'
   # 总部侧新增 BGP peer（AS 6550X 对应新分支）
   set protocols bgp neighbor 10.10.0.1X remote-as '6550X'
   set protocols bgp neighbor 10.10.0.1X address-family ipv4-unicast
   set protocols bgp neighbor 10.10.0.1X update-source 'wg0'
   set protocols bgp neighbor 10.10.0.1X password 'changeme'
   set protocols bgp neighbor 10.10.0.1X ebgp-multihop '2'
   set protocols bgp neighbor 10.10.0.1X capability dynamic
   set protocols bgp neighbor 10.10.0.1X capability route-refresh
set protocols bgp neighbor 10.10.0.1X capability route-refresh
   commit; save
   ```
3. **分支侧**：按模板配置 wg0（填入总部公钥 + endpoint）
4. **监控侧**：
   - 编辑 `/etc/wg-monitor/config.json` 增加 peer 映射
   - 编辑 `/etc/wg-monitor/secrets.json` 增加分支密钥
   - 分支部署 Agent：`./scripts/deploy_branch.sh`
5. **验证**：`ping 10.10.0.1X` + 看板自动发现新 peer
