#!/bin/bash
# ============================================================
# VyOS WireGuard Monitor - 统一部署入口
# 
# 使用方式：
#   git clone <repo-url> /opt/wg-monitor
#   cd /opt/wg-monitor
#   chmod +x install.sh
#   ./install.sh
#
# 自动检测环境（VyOS / 通用 Linux），提供部署方式选择
# ============================================================
set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${CYAN}=========================================="
echo "  VyOS WireGuard Monitor - 统一部署"
echo -e "==========================================${NC}"
echo ""

# ==================== 环境检测 ====================
detect_environment() {
    IS_VYOS=false
    IS_HQ=false
    HAS_DOCKER=false
    HAS_PODMAN=false

    # 检测 VyOS
    if [ -f /etc/vyos/version ] || command -v /opt/vyatta/sbin/vyatta-cfg-cmd-wrapper &>/dev/null; then
        IS_VYOS=true
        echo -e "  系统环境: ${GREEN}VyOS$(cat /etc/vyos/version 2>/dev/null || echo '')${NC}"
    elif [ -f /etc/os-release ]; then
        . /etc/os-release
        echo -e "  系统环境: ${YELLOW}${PRETTY_NAME:-Linux}${NC}（非 VyOS）"
    else
        echo -e "  系统环境: ${YELLOW}未知 Linux${NC}（非 VyOS）"
    fi

    # 检测 WireGuard（判断是否为总部）
    if command -v wg &>/dev/null && [ -d /sys/class/net/wg0 ] 2>/dev/null; then
        IS_HQ=true
        echo -e "  WireGuard: ${GREEN}wg0 接口存在（总部）${NC}"
    elif command -v wg &>/dev/null; then
        echo -e "  WireGuard: ${YELLOW}已安装，wg0 未配置${NC}"
    else
        echo -e "  WireGuard: ${RED}未安装${NC}"
    fi

    # 检测容器运行时
    if command -v podman &>/dev/null; then
        HAS_PODMAN=true
        echo -e "  容器引擎: ${GREEN}Podman $(podman --version 2>/dev/null | awk '{print $NF}')${NC}"
    fi
    if command -v docker &>/dev/null; then
        HAS_DOCKER=true
        echo -e "  容器引擎: ${GREEN}Docker $(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')${NC}"
    fi
    if [ "$HAS_DOCKER" = false ] && [ "$HAS_PODMAN" = false ]; then
        echo -e "  容器引擎: ${YELLOW}无（仅支持原生部署）${NC}"
    fi

    echo ""
}

# ==================== 角色选择 ====================
select_role() {
    echo -e "${CYAN}【步骤 1/3】选择部署角色：${NC}"
    echo ""
    echo "  1) 总部（HQ）— 采集器 + 聚合器 + Nginx + 前端 + 告警"
    echo "  2) 分支（Branch）— 资源上报 Agent"
    echo ""
    read -p "  请选择 [1/2]: " ROLE_CHOICE

    case "$ROLE_CHOICE" in
        1) ROLE="hq" ;;
        2) ROLE="branch" ;;
        *)
            echo -e "${RED}无效选择，退出${NC}"
            exit 1
            ;;
    esac
    echo ""
}

# ==================== 部署方式选择 ====================
select_deploy_mode() {
    echo -e "${CYAN}【步骤 2/3】选择部署方式：${NC}"
    echo ""

    if [ "$IS_VYOS" = true ]; then
        echo "  检测到 VyOS 环境，可选："
        echo ""
        echo "  1) 原生 systemd 部署（推荐，轻量，wg 命令天然可用）"
        if [ "$HAS_PODMAN" = true ]; then
            echo "  2) VyOS 容器化部署（podman + set container）"
        fi
        echo ""
        read -p "  请选择 [1$([ "$HAS_PODMAN" = true ] && echo '/2')]: " MODE_CHOICE

        case "$MODE_CHOICE" in
            1) DEPLOY_MODE="native" ;;
            2)
                if [ "$HAS_PODMAN" = true ]; then
                    DEPLOY_MODE="vyos-container"
                else
                    echo -e "${RED}Podman 不可用，回退到原生部署${NC}"
                    DEPLOY_MODE="native"
                fi
                ;;
            *) DEPLOY_MODE="native" ;;
        esac
    else
        echo "  非 VyOS 环境，可选："
        echo ""
        echo "  1) Docker Compose 部署（推荐，标准容器化）"
        echo "  2) 原生 systemd 部署（直接安装到宿主）"
        echo ""
        read -p "  请选择 [1/2]: " MODE_CHOICE

        case "$MODE_CHOICE" in
            1)
                if [ "$HAS_DOCKER" = true ]; then
                    DEPLOY_MODE="docker-compose"
                else
                    echo -e "${RED}Docker 未安装！请先安装 Docker 和 docker-compose${NC}"
                    echo "  安装: curl -fsSL https://get.docker.com | sh"
                    exit 1
                fi
                ;;
            2) DEPLOY_MODE="native" ;;
            *) DEPLOY_MODE="docker-compose" ;;
        esac
    fi

    echo ""
    echo -e "  部署方式: ${GREEN}${DEPLOY_MODE}${NC}"
    echo ""
}

# ==================== 总部：原生 systemd 部署 ====================
deploy_hq_native() {
    echo -e "${CYAN}【步骤 3/3】总部原生部署...${NC}"
    echo ""

    INSTALL_DIR="/opt/wg-monitor"
    WEB_DIR="/var/www/monitor"
    CONFIG_DIR="/etc/wg-monitor"
    LOG_DIR="/var/log/wg-monitor"

    # 1. 目录
    echo "[1/8] 创建目录..."
    mkdir -p "$WEB_DIR"/{css,js,assets/fontawesome/css,assets/fontawesome/webfonts,data}
    mkdir -p "$CONFIG_DIR"
    mkdir -p "$LOG_DIR"

    # 2. Python 依赖
    echo "[2/8] 检查 Python 依赖..."
    if ! python3 -c "import psutil" 2>/dev/null; then
        pip3 install psutil --quiet 2>/dev/null || apt-get install -y python3-psutil 2>/dev/null || true
    fi

    # 3. 前端文件
    echo "[3/8] 部署前端..."
    cp -f "$SCRIPT_DIR/frontend/index.html" "$WEB_DIR/"
    cp -f "$SCRIPT_DIR/frontend/css/dashboard.css" "$WEB_DIR/css/"
    cp -f "$SCRIPT_DIR/frontend/js/dashboard.js" "$WEB_DIR/js/"

    # 4. 离线资源
    echo "[4/8] 检查离线资源..."
    if [ -d "$SCRIPT_DIR/frontend/assets/fontawesome" ]; then
        cp -rf "$SCRIPT_DIR/frontend/assets/"* "$WEB_DIR/assets/" 2>/dev/null || true
        echo "  → 从仓库复制离线资源"
    elif [ ! -f "$WEB_DIR/assets/echarts.min.js" ]; then
        if curl -s --connect-timeout 3 https://cdn.jsdelivr.net >/dev/null 2>&1; then
            echo "  → 在线下载离线资源..."
            bash "$SCRIPT_DIR/scripts/download_assets.sh" "$WEB_DIR/assets"
        else
            echo -e "  ${YELLOW}⚠ 无网络且仓库中无离线资源，请手动执行 download_assets.sh${NC}"
        fi
    else
        echo "  → 离线资源已存在"
    fi

    # 5. 配置文件（不覆盖已有）
    echo "[5/8] 部署配置..."
    for cfg in config.json secrets.json alert.json; do
        if [ ! -f "$CONFIG_DIR/$cfg" ]; then
            cp "$SCRIPT_DIR/config/$cfg" "$CONFIG_DIR/"
            echo "  → $cfg 已创建（请编辑）"
        else
            echo "  → $cfg 已存在，跳过"
        fi
    done
    [ -f "$CONFIG_DIR/secrets.json" ] && chmod 600 "$CONFIG_DIR/secrets.json"

    # 6. Nginx
    echo "[6/8] 部署 Nginx 配置..."
    if command -v nginx &>/dev/null; then
        cp -f "$SCRIPT_DIR/nginx/monitor.conf" /etc/nginx/sites-enabled/monitor.conf
        nginx -t 2>/dev/null && (nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null) && echo "  → Nginx 已重载"
    else
        echo -e "  ${YELLOW}⚠ Nginx 未安装，请手动安装并复制 nginx/monitor.conf${NC}"
    fi

    # 7. systemd 服务
    echo "[7/8] 创建 systemd 服务..."

    cat > /etc/systemd/system/wg-collector.service << EOF
[Unit]
Description=WireGuard Tunnel Collector
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/collector/collector.py
Environment=WG_MONITOR_CONFIG=$CONFIG_DIR/config.json
Environment=WG_MONITOR_OUTPUT=$WEB_DIR/data
Environment=WG_MONITOR_INTERVAL=5
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/wg-aggregator.service << EOF
[Unit]
Description=WireGuard Branch Aggregator API
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/aggregator/aggregator.py
Environment=AGG_LISTEN_HOST=127.0.0.1
Environment=AGG_LISTEN_PORT=9100
Environment=AGG_SECRETS=$CONFIG_DIR/secrets.json
Environment=AGG_OUTPUT_DIR=$WEB_DIR/data
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/wg-alerter.service << EOF
[Unit]
Description=WireGuard Monitor Alert Module
After=wg-collector.service wg-aggregator.service

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/aggregator/alerter.py
Environment=ALERT_CONFIG=$CONFIG_DIR/alert.json
Environment=WG_MONITOR_OUTPUT=$WEB_DIR/data
Environment=ALERT_INTERVAL=10
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now wg-collector.service
    systemctl enable --now wg-aggregator.service
    systemctl enable --now wg-alerter.service

    # 8. Watchdog
    echo "[8/8] 安装 Watchdog..."
    chmod +x "$SCRIPT_DIR/scripts/watchdog.sh"
    chmod +x "$SCRIPT_DIR/scripts/healthcheck.sh"
    (crontab -l 2>/dev/null | grep -v "wg-monitor" ; echo "* * * * * $SCRIPT_DIR/scripts/watchdog.sh >> $LOG_DIR/watchdog.log 2>&1") | crontab -

    echo ""
    echo -e "${GREEN}✅ 总部原生部署完成！${NC}"
    print_hq_summary
}

# ==================== 总部：Docker Compose 部署 ====================
deploy_hq_docker() {
    echo -e "${CYAN}【步骤 3/3】总部 Docker Compose 部署...${NC}"
    echo ""

    CONFIG_DIR="/etc/wg-monitor"
    mkdir -p "$CONFIG_DIR"

    # 配置文件
    echo "[1/3] 部署配置..."
    for cfg in config.json secrets.json alert.json; do
        if [ ! -f "$CONFIG_DIR/$cfg" ]; then
            cp "$SCRIPT_DIR/config/$cfg" "$CONFIG_DIR/"
            echo "  → $cfg 已创建（请编辑）"
        fi
    done
    [ -f "$CONFIG_DIR/secrets.json" ] && chmod 600 "$CONFIG_DIR/secrets.json"

    # 下载离线资源（构建镜像需要）
    echo "[2/3] 准备离线资源..."
    if [ ! -f "$SCRIPT_DIR/frontend/assets/echarts.min.js" ]; then
        if curl -s --connect-timeout 3 https://cdn.jsdelivr.net >/dev/null 2>&1; then
            bash "$SCRIPT_DIR/scripts/download_assets.sh" "$SCRIPT_DIR/frontend/assets"
        else
            echo -e "  ${YELLOW}⚠ 无网络，请先执行 scripts/download_assets.sh${NC}"
        fi
    fi

    # 构建 & 启动
    echo "[3/3] 构建并启动容器..."
    if command -v podman-compose &>/dev/null; then
        podman-compose -f "$SCRIPT_DIR/docker-compose.yml" up -d --build
    elif command -v docker &>/dev/null; then
        docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d --build
    else
        echo -e "${RED}错误：未找到 docker 或 podman-compose${NC}"
        exit 1
    fi

    echo ""
    echo -e "${GREEN}✅ 总部 Docker Compose 部署完成！${NC}"
    print_hq_summary
}

# ==================== 总部：VyOS 容器化部署 ====================
deploy_hq_vyos_container() {
    echo -e "${CYAN}【步骤 3/3】总部 VyOS 容器化部署...${NC}"
    echo ""
    echo "VyOS 容器化需通过 VyOS CLI 配置，请参照以下文档手动执行："
    echo ""
    echo -e "  ${CYAN}$SCRIPT_DIR/scripts/container_deploy.md${NC}"
    echo ""
    echo "快速步骤："
    echo "  1. 构建镜像: bash scripts/build_images.sh"
    echo "  2. 导入镜像: for f in dist/*.tar.gz; do podman load -i "$f"; done"
    echo "  3. VyOS CLI: configure → set container ... → commit; save"
    echo ""

    # 配置文件仍然需要
    CONFIG_DIR="/etc/wg-monitor"
    mkdir -p "$CONFIG_DIR"
    for cfg in config.json secrets.json alert.json; do
        if [ ! -f "$CONFIG_DIR/$cfg" ]; then
            cp "$SCRIPT_DIR/config/$cfg" "$CONFIG_DIR/"
        fi
    done
    [ -f "$CONFIG_DIR/secrets.json" ] && chmod 600 "$CONFIG_DIR/secrets.json"

    echo -e "${GREEN}✅ 配置文件已部署，请按文档完成容器配置${NC}"
}

# ==================== 分支部署 ====================
deploy_branch() {
    echo -e "${CYAN}【步骤 3/3】分支 Agent 部署...${NC}"
    echo ""

    CONFIG_DIR="/etc/wg-monitor"
    LOG_DIR="/var/log/wg-monitor"
    mkdir -p "$CONFIG_DIR" "$LOG_DIR"

    # 收集信息
    echo "请输入分支配置信息："
    echo ""
    read -p "  分支 ID（如 branch-shanghai-01）: " BRANCH_ID
    read -p "  分支密钥（由总部提供）: " BRANCH_SECRET
    read -p "  总部监控地址（如 http://10.10.0.1:8080）: " HQ_ADDR
    read -p "  监控接口列表（逗号分隔，如 eth0,eth1,wg0）: " INTERFACES

    # 去掉末尾斜杠
    HQ_ADDR="${HQ_ADDR%/}"

    echo ""

    # Python 依赖
    echo "[1/3] 检查 Python 依赖..."
    if ! python3 -c "import psutil" 2>/dev/null; then
        pip3 install psutil --quiet 2>/dev/null || apt-get install -y python3-psutil 2>/dev/null || true
    fi

    # 写入配置
    echo "[2/3] 生成配置..."
    cat > "$CONFIG_DIR/agent.conf" << EOF
# Branch Agent 配置（由 install.sh 自动生成）
[agent]
branch_id = ${BRANCH_ID}
secret = ${BRANCH_SECRET}
hq_endpoint = ${HQ_ADDR}/monitor/api/report
report_interval = 5
interfaces = ${INTERFACES}
EOF
    chmod 600 "$CONFIG_DIR/agent.conf"

    # systemd 服务
    echo "[3/3] 创建 systemd 服务..."
    cat > /etc/systemd/system/wg-branch-agent.service << EOF
[Unit]
Description=WireGuard Branch Resource Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/collector/branch_agent.py
Environment=WG_AGENT_CONFIG=$CONFIG_DIR/agent.conf
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now wg-branch-agent.service

    echo ""
    echo -e "${GREEN}✅ 分支 Agent 部署完成！${NC}"
    echo ""
    echo "  分支 ID:   $BRANCH_ID"
    echo "  上报地址:  ${HQ_ADDR}/monitor/api/report"
    echo "  检查状态:  systemctl status wg-branch-agent"
    echo "  查看日志:  journalctl -u wg-branch-agent -f"
    echo ""
}

# ==================== 输出总部摘要 ====================
print_hq_summary() {
    echo ""
    echo "  看板地址:  http://<本机IP>:8080/monitor/"
    echo ""
    echo "  后续操作:"
    echo "  1. 编辑 /etc/wg-monitor/config.json → 填入 peer 公钥映射"
    echo "  2. 编辑 /etc/wg-monitor/secrets.json → 填入分支密钥"
    echo "  3. 编辑 /etc/wg-monitor/alert.json → 配置告警 Webhook"
    echo "  4. 生成密钥: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    echo "  5. 健康检查: $SCRIPT_DIR/scripts/healthcheck.sh"
    echo ""
}

# ==================== 主流程 ====================
detect_environment
select_role
select_deploy_mode

# 确认
echo -e "${CYAN}部署确认：${NC}"
echo "  角色: $ROLE"
echo "  方式: $DEPLOY_MODE"
echo "  目录: $SCRIPT_DIR"
echo ""
read -p "  确认开始部署？ [Y/n]: " CONFIRM
CONFIRM="${CONFIRM:-Y}"
if [[ ! "$CONFIRM" =~ ^[Yy] ]]; then
    echo "已取消"
    exit 0
fi
echo ""

# 分发
case "${ROLE}-${DEPLOY_MODE}" in
    hq-native)          deploy_hq_native ;;
    hq-docker-compose)  deploy_hq_docker ;;
    hq-vyos-container)  deploy_hq_vyos_container ;;
    branch-*)           deploy_branch ;;
    *)
        echo -e "${RED}未知组合: ${ROLE}-${DEPLOY_MODE}${NC}"
        exit 1
        ;;
esac
