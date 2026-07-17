#!/bin/bash
# ============================================================
# VyOS Monitor Watchdog — 进程守护与自动恢复
# 可加入 crontab 每分钟执行：
#   * * * * * /opt/wg-monitor/watchdog.sh >> /var/log/wg-monitor/watchdog.log 2>&1
# ============================================================

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

restart_if_dead() {
    local svc="$1"
    if ! systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "$LOG_PREFIX [WATCHDOG] $svc is dead, restarting..."
        systemctl restart "$svc"
        if systemctl is-active --quiet "$svc"; then
            echo "$LOG_PREFIX [WATCHDOG] $svc restarted successfully"
        else
            echo "$LOG_PREFIX [WATCHDOG] CRITICAL: $svc failed to restart!"
        fi
    fi
}

check_data_freshness() {
    local file="$1"
    local max_age="$2"
    local svc="$3"

    if [ ! -f "$file" ]; then
        return
    fi

    NOW=$(date +%s)
    FILE_MTIME=$(stat -c %Y "$file" 2>/dev/null || echo "0")
    AGE=$((NOW - FILE_MTIME))

    if [ "$AGE" -gt "$max_age" ]; then
        echo "$LOG_PREFIX [WATCHDOG] $file stale (${AGE}s > ${max_age}s), restarting $svc"
        systemctl restart "$svc"
    fi
}

# 检查核心服务是否存活
restart_if_dead "wg-collector"
restart_if_dead "wg-aggregator"
restart_if_dead "nginx"

# 检查数据文件新鲜度（如果进程活着但卡死不输出）
check_data_freshness "/var/www/monitor/data/status-tunnel.json" 60 "wg-collector"
