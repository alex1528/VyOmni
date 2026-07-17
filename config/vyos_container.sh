#!/bin/vbash
# ============================================================
# VyOmni - VyOS 1.5.0 容器化配置命令集
# 使用方式（在 VyOS 上）：
#   configure
#   source /opt/wg-monitor/config/vyos_container.sh
#   commit
#   save
#   exit
# ============================================================

# === 容器网络 ===
set container network monitor-net prefix '172.20.0.0/24'

# === 容器1: Collector（采集隧道状态） ===
set container name wg-collector image 'localhost/wg-collector:latest'
set container name wg-collector network monitor-net address '172.20.0.10'
set container name wg-collector allow-host-networks
set container name wg-collector restart 'on-failure'

set container name wg-collector volume config source '/etc/wg-monitor'
set container name wg-collector volume config destination '/etc/wg-monitor'
set container name wg-collector volume config mode 'ro'

set container name wg-collector volume data source '/var/www/monitor/data'
set container name wg-collector volume data destination '/output'

set container name wg-collector environment WG_MONITOR_CONFIG value '/etc/wg-monitor/config.json'
set container name wg-collector environment WG_MONITOR_OUTPUT value '/output'
set container name wg-collector environment WG_MONITOR_INTERVAL value '5'

# === 容器2: Aggregator（接收分支上报） ===
set container name wg-aggregator image 'localhost/wg-aggregator:latest'
set container name wg-aggregator network monitor-net address '172.20.0.11'
set container name wg-aggregator restart 'on-failure'

set container name wg-aggregator volume secrets source '/etc/wg-monitor'
set container name wg-aggregator volume secrets destination '/etc/wg-monitor'
set container name wg-aggregator volume secrets mode 'ro'

set container name wg-aggregator volume data source '/var/www/monitor/data'
set container name wg-aggregator volume data destination '/output'

set container name wg-aggregator environment AGG_LISTEN_HOST value '0.0.0.0'
set container name wg-aggregator environment AGG_LISTEN_PORT value '9100'
set container name wg-aggregator environment AGG_SECRETS value '/etc/wg-monitor/secrets.json'
set container name wg-aggregator environment AGG_OUTPUT_DIR value '/output'

set container name wg-aggregator port report source '9100'
set container name wg-aggregator port report destination '9100'
set container name wg-aggregator port report listen-address '127.0.0.1'

# === 容器3: Nginx（前端 + API网关） ===
set container name wg-nginx image 'localhost/wg-nginx:latest'
set container name wg-nginx network monitor-net address '172.20.0.12'
set container name wg-nginx restart 'on-failure'

set container name wg-nginx volume data source '/var/www/monitor/data'
set container name wg-nginx volume data destination '/var/www/monitor/data'
set container name wg-nginx volume data mode 'ro'

set container name wg-nginx port web source '8080'
set container name wg-nginx port web destination '8080'

# === 容器4: Alerter（告警推送） ===
set container name wg-alerter image 'localhost/wg-aggregator:latest'
set container name wg-alerter network monitor-net address '172.20.0.13'
set container name wg-alerter restart 'on-failure'

set container name wg-alerter volume config source '/etc/wg-monitor'
set container name wg-alerter volume config destination '/etc/wg-monitor'
set container name wg-alerter volume config mode 'ro'

set container name wg-alerter volume data source '/var/www/monitor/data'
set container name wg-alerter volume data destination '/var/www/monitor/data'
set container name wg-alerter volume data mode 'ro'

set container name wg-alerter environment ALERT_CONFIG value '/etc/wg-monitor/alert.json'
set container name wg-alerter environment WG_MONITOR_OUTPUT value '/var/www/monitor/data'
set container name wg-alerter environment ALERT_INTERVAL value '10'

set container name wg-alerter command '/usr/local/bin/python3 -u /app/alerter.py'
