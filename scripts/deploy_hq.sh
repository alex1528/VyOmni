#!/bin/bash
# ============================================================
# VyOS Monitor - 总部部署脚本
# 在总部 VyOS 上执行，部署采集器 + 聚合器 + Nginx + 前端
# ============================================================
set -e

echo "=========================================="
echo "  VyOS WireGuard Monitor - 总部部署"
echo "=========================================="

# 配置
INSTALL_DIR="/opt/wg-monitor"
WEB_DIR="/var/www/monitor"
CONFIG_DIR="/etc/wg-monitor"
SERVICE_USER="root"

# 1. 创建目录
echo "[1/7] 创建目录结构..."
mkdir -p "$INSTALL_DIR"/{collector,aggregator}
mkdir -p "$WEB_DIR"/{css,js,assets/fontawesome/css,assets/fontawesome/webfonts,data}
mkdir -p "$CONFIG_DIR"

# 2. 安装 Python 依赖
echo "[2/7] 安装 Python 依赖..."
pip3 install psutil --quiet 2>/dev/null || apt-get install -y python3-psutil

# 3. 部署应用代码
echo "[3/7] 部署应用代码..."
cp collector/collector.py "$INSTALL_DIR/collector/"
cp aggregator/aggregator.py "$INSTALL_DIR/aggregator/"
cp aggregator/alerter.py "$INSTALL_DIR/aggregator/"

# 4. 部署前端
echo "[4/7] 部署前端文件..."
cp frontend/index.html "$WEB_DIR/"
cp frontend/css/dashboard.css "$WEB_DIR/css/"
cp frontend/js/dashboard.js "$WEB_DIR/js/"

# 5. 部署配置
echo "[5/7] 部署配置..."
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    cp config/config.json "$CONFIG_DIR/"
    echo "  → config.json 已创建（请编辑 peer 公钥映射）"
fi
if [ ! -f "$CONFIG_DIR/secrets.json" ]; then
    cp config/secrets.json "$CONFIG_DIR/"
    chmod 600 "$CONFIG_DIR/secrets.json"
    echo "  → secrets.json 已创建（请替换为实际密钥）"
fi

# 6. 下载离线前端资源
echo "[6/7] 下载离线前端资源..."
if [ ! -f "$WEB_DIR/assets/echarts.min.js" ]; then
    if command -v curl &>/dev/null && curl -s --connect-timeout 5 https://cdn.jsdelivr.net >/dev/null 2>&1; then
        curl -sL "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js" \
            -o "$WEB_DIR/assets/echarts.min.js"
        curl -sL "https://use.fontawesome.com/releases/v6.5.0/fontawesome-free-6.5.0-web.zip" \
            -o /tmp/fa.zip
        unzip -qo /tmp/fa.zip -d /tmp/fa
        cp /tmp/fa/fontawesome-free-6.5.0-web/css/all.min.css "$WEB_DIR/assets/fontawesome/css/"
        cp /tmp/fa/fontawesome-free-6.5.0-web/webfonts/* "$WEB_DIR/assets/fontawesome/webfonts/"
        rm -rf /tmp/fa /tmp/fa.zip
        echo "  → 离线资源下载完成"
    else
        echo "  ⚠ 无网络，请手动放置 echarts.min.js 和 fontawesome 到 $WEB_DIR/assets/"
    fi
else
    echo "  → 离线资源已存在，跳过"
fi

# 7. 部署 Nginx 配置
echo "[7/7] 部署 Nginx 配置..."
cp nginx/monitor.conf /etc/nginx/sites-enabled/monitor.conf
nginx -t && nginx -s reload
echo "  → Nginx 配置已生效"

# 8. 创建 systemd 服务
echo "[额外] 创建 systemd 服务..."

cat > /etc/systemd/system/wg-collector.service << 'EOF'
[Unit]
Description=WireGuard Tunnel Collector
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/wg-monitor/collector/collector.py
Environment=WG_MONITOR_CONFIG=/etc/wg-monitor/config.json
Environment=WG_MONITOR_OUTPUT=/var/www/monitor/data
Environment=WG_MONITOR_INTERVAL=5
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/wg-aggregator.service << 'EOF'
[Unit]
Description=WireGuard Branch Aggregator API
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/wg-monitor/aggregator/aggregator.py
Environment=AGG_LISTEN_HOST=127.0.0.1
Environment=AGG_LISTEN_PORT=9100
Environment=AGG_SECRETS=/etc/wg-monitor/secrets.json
Environment=AGG_OUTPUT_DIR=/var/www/monitor/data
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now wg-collector.service
systemctl enable --now wg-aggregator.service

# 9. 创建告警服务
echo "[额外] 创建告警服务..."

if [ ! -f "$CONFIG_DIR/alert.json" ]; then
    cp config/alert.json "$CONFIG_DIR/"
    echo "  → alert.json 已创建（请配置 Webhook URL）"
fi

cat > /etc/systemd/system/wg-alerter.service << 'EOF'
[Unit]
Description=WireGuard Monitor Alert Module
After=wg-collector.service wg-aggregator.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/wg-monitor/aggregator/alerter.py
Environment=ALERT_CONFIG=/etc/wg-monitor/alert.json
Environment=WG_MONITOR_OUTPUT=/var/www/monitor/data
Environment=ALERT_INTERVAL=10
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now wg-alerter.service

# 10. 安装 Watchdog crontab
echo "[额外] 安装 Watchdog..."
cp scripts/watchdog.sh /opt/wg-monitor/watchdog.sh
chmod +x /opt/wg-monitor/watchdog.sh
cp scripts/healthcheck.sh /opt/wg-monitor/healthcheck.sh
chmod +x /opt/wg-monitor/healthcheck.sh

# 添加 crontab（如果不存在）
(crontab -l 2>/dev/null | grep -v "wg-monitor/watchdog" ; echo "* * * * * /opt/wg-monitor/watchdog.sh >> /var/log/wg-monitor/watchdog.log 2>&1") | crontab -
mkdir -p /var/log/wg-monitor
echo "  → Watchdog crontab 已安装（每分钟执行）"

echo ""
echo "=========================================="
echo "  ✅ 总部部署完成！"
echo "=========================================="
echo ""
echo "  看板地址: http://<总部IP>:8080/monitor/"
echo ""
echo "  后续操作:"
echo "  1. 编辑 $CONFIG_DIR/config.json → 填入 peer 公钥映射"
echo "  2. 编辑 $CONFIG_DIR/secrets.json → 填入分支密钥"
echo "  3. 编辑 $CONFIG_DIR/alert.json → 配置告警 Webhook"
echo "  4. 生成密钥: python3 -c \"import secrets; print(secrets.token_hex(32))\""
echo "  5. 检查状态: systemctl status wg-collector wg-aggregator wg-alerter"
echo "  6. 健康检查: /opt/wg-monitor/healthcheck.sh"
echo ""
