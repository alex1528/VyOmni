#!/bin/bash
# ============================================================
# VyOS Monitor 运维健康检查脚本
# 一键检查所有服务状态、数据新鲜度、磁盘、进程
# 用法: ./healthcheck.sh [--quiet]
# ============================================================

set -e

QUIET=${1:-""}
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
WARN=0
FAIL=0

check() {
    local label="$1"
    local status="$2"  # ok / warn / fail
    local detail="$3"

    case "$status" in
        ok)
            PASS=$((PASS+1))
            [ -z "$QUIET" ] && echo -e "  ${GREEN}✓${NC} $label: $detail"
            ;;
        warn)
            WARN=$((WARN+1))
            echo -e "  ${YELLOW}⚠${NC} $label: $detail"
            ;;
        fail)
            FAIL=$((FAIL+1))
            echo -e "  ${RED}✗${NC} $label: $detail"
            ;;
    esac
}

echo "=========================================="
echo "  VyOS WireGuard Monitor 健康检查"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
echo ""

# ==================== 1. 服务进程状态 ====================
echo "【1】服务进程"

# Collector
if systemctl is-active --quiet wg-collector 2>/dev/null; then
    check "Collector 服务" "ok" "active"
else
    check "Collector 服务" "fail" "inactive/dead"
fi

# Aggregator
if systemctl is-active --quiet wg-aggregator 2>/dev/null; then
    check "Aggregator 服务" "ok" "active"
else
    check "Aggregator 服务" "fail" "inactive/dead"
fi

# Branch Agent（分支才有）
if systemctl list-units --type=service --all 2>/dev/null | grep -q wg-branch-agent; then
    if systemctl is-active --quiet wg-branch-agent 2>/dev/null; then
        check "Branch Agent 服务" "ok" "active"
    else
        check "Branch Agent 服务" "fail" "inactive/dead"
    fi
fi

# Nginx
if systemctl is-active --quiet nginx 2>/dev/null; then
    check "Nginx 服务" "ok" "active"
else
    check "Nginx 服务" "fail" "inactive/dead"
fi

echo ""

# ==================== 2. 端口监听 ====================
echo "【2】端口监听"

if ss -tlnp 2>/dev/null | grep -q ':8080 '; then
    check "Nginx :8080" "ok" "listening"
else
    check "Nginx :8080" "fail" "not listening"
fi

if ss -tlnp 2>/dev/null | grep -q ':9100 '; then
    check "Aggregator :9100" "ok" "listening"
else
    check "Aggregator :9100" "warn" "not listening（可能用容器模式）"
fi

echo ""

# ==================== 3. HTTP 端点验证 ====================
echo "【3】HTTP 端点"

# 前端页面
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/monitor/ 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    check "前端页面" "ok" "HTTP $HTTP_CODE"
else
    check "前端页面" "fail" "HTTP $HTTP_CODE"
fi

# 隧道 JSON
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/monitor/api/tunnel 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    check "隧道 API" "ok" "HTTP $HTTP_CODE"
else
    check "隧道 API" "fail" "HTTP $HTTP_CODE"
fi

# 分支 JSON
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/monitor/api/branches 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    check "分支 API" "ok" "HTTP $HTTP_CODE"
else
    check "分支 API" "warn" "HTTP $HTTP_CODE（分支可能尚未上报）"
fi

# 聚合器健康
HEALTH=$(curl -s http://127.0.0.1:9100/health 2>/dev/null || echo "")
if echo "$HEALTH" | grep -q '"status":"ok"'; then
    UPTIME=$(echo "$HEALTH" | python3 -c "import sys,json;print(json.load(sys.stdin).get('uptime_seconds',0))" 2>/dev/null || echo "?")
    check "聚合器 /health" "ok" "uptime=${UPTIME}s"
else
    check "聚合器 /health" "warn" "无响应"
fi

echo ""

# ==================== 4. 数据新鲜度 ====================
echo "【4】数据新鲜度"

DATA_DIR="/var/www/monitor/data"
NOW=$(date +%s)

# status-tunnel.json
if [ -f "$DATA_DIR/status-tunnel.json" ]; then
    FILE_MTIME=$(stat -c %Y "$DATA_DIR/status-tunnel.json" 2>/dev/null || stat -f %m "$DATA_DIR/status-tunnel.json" 2>/dev/null || echo "0")
    AGE=$((NOW - FILE_MTIME))
    if [ "$AGE" -lt 15 ]; then
        check "status-tunnel.json" "ok" "更新于 ${AGE}s 前"
    elif [ "$AGE" -lt 60 ]; then
        check "status-tunnel.json" "warn" "更新于 ${AGE}s 前（>15s）"
    else
        check "status-tunnel.json" "fail" "更新于 ${AGE}s 前（已过期！）"
    fi
else
    check "status-tunnel.json" "fail" "文件不存在"
fi

# status-branches.json
if [ -f "$DATA_DIR/status-branches.json" ]; then
    FILE_MTIME=$(stat -c %Y "$DATA_DIR/status-branches.json" 2>/dev/null || stat -f %m "$DATA_DIR/status-branches.json" 2>/dev/null || echo "0")
    AGE=$((NOW - FILE_MTIME))
    if [ "$AGE" -lt 15 ]; then
        check "status-branches.json" "ok" "更新于 ${AGE}s 前"
    elif [ "$AGE" -lt 60 ]; then
        check "status-branches.json" "warn" "更新于 ${AGE}s 前（>15s）"
    else
        check "status-branches.json" "fail" "更新于 ${AGE}s 前（已过期！）"
    fi
else
    check "status-branches.json" "warn" "文件不存在（分支可能尚未上报）"
fi

echo ""

# ==================== 5. WireGuard 隧道状态 ====================
echo "【5】WireGuard 隧道"

if command -v wg &>/dev/null; then
    WG_PEERS=$(sudo wg show all dump 2>/dev/null | grep -v "^#" | awk -F'\t' 'NF==9{print $0}' | wc -l)
    if [ "$WG_PEERS" -gt 0 ]; then
        # 统计在线（最后握手 < 180s）
        ONLINE=$(sudo wg show all dump 2>/dev/null | awk -F'\t' 'NF==9 && $6>0 && (systime()-$6)<180{count++}END{print count+0}')
        check "WireGuard Peers" "ok" "${ONLINE}/${WG_PEERS} 在线"
    else
        check "WireGuard Peers" "warn" "无 peer 数据"
    fi
else
    check "WireGuard" "warn" "wg 命令不可用（可能在分支侧）"
fi

echo ""

# ==================== 6. 系统资源 ====================
echo "【6】系统资源"

# 磁盘
DISK_USAGE=$(df -h /var/www/monitor 2>/dev/null | awk 'NR==2{print $5}' | tr -d '%')
if [ -n "$DISK_USAGE" ]; then
    if [ "$DISK_USAGE" -lt 80 ]; then
        check "磁盘 /var/www/monitor" "ok" "使用 ${DISK_USAGE}%"
    elif [ "$DISK_USAGE" -lt 90 ]; then
        check "磁盘 /var/www/monitor" "warn" "使用 ${DISK_USAGE}%（>80%）"
    else
        check "磁盘 /var/www/monitor" "fail" "使用 ${DISK_USAGE}%（>90%！）"
    fi
fi

# 内存
MEM_USAGE=$(free | awk '/^Mem:/{printf "%.0f", $3/$2*100}' 2>/dev/null || echo "")
if [ -n "$MEM_USAGE" ]; then
    if [ "$MEM_USAGE" -lt 80 ]; then
        check "系统内存" "ok" "使用 ${MEM_USAGE}%"
    elif [ "$MEM_USAGE" -lt 90 ]; then
        check "系统内存" "warn" "使用 ${MEM_USAGE}%（>80%）"
    else
        check "系统内存" "fail" "使用 ${MEM_USAGE}%（>90%！）"
    fi
fi

# 负载
LOAD_1=$(cat /proc/loadavg 2>/dev/null | awk '{print $1}' || echo "")
CPU_COUNT=$(nproc 2>/dev/null || echo "1")
if [ -n "$LOAD_1" ]; then
    # 负载 > CPU 核数视为 warn
    OVERLOADED=$(echo "$LOAD_1 $CPU_COUNT" | awk '{if($1 > $2) print 1; else print 0}')
    if [ "$OVERLOADED" = "1" ]; then
        check "系统负载" "warn" "load=${LOAD_1}（>${CPU_COUNT} cores）"
    else
        check "系统负载" "ok" "load=${LOAD_1}（${CPU_COUNT} cores）"
    fi
fi

echo ""

# ==================== 7. 日志最近错误 ====================
echo "【7】最近错误日志（5 分钟内）"

ERRORS=$(journalctl -u wg-collector -u wg-aggregator --since "5 min ago" --no-pager 2>/dev/null | grep -i "error\|exception\|traceback" | tail -3)
if [ -n "$ERRORS" ]; then
    check "最近错误" "warn" "发现错误:"
    echo "$ERRORS" | while read line; do
        echo "      $line"
    done
else
    check "最近错误" "ok" "无错误"
fi

echo ""

# ==================== 总结 ====================
echo "=========================================="
TOTAL=$((PASS+WARN+FAIL))
echo -e "  总计: ${TOTAL} 项 | ${GREEN}通过 ${PASS}${NC} | ${YELLOW}警告 ${WARN}${NC} | ${RED}失败 ${FAIL}${NC}"

if [ "$FAIL" -gt 0 ]; then
    echo -e "  状态: ${RED}异常${NC} — 请检查失败项"
    exit 2
elif [ "$WARN" -gt 0 ]; then
    echo -e "  状态: ${YELLOW}注意${NC} — 有告警项需关注"
    exit 1
else
    echo -e "  状态: ${GREEN}正常${NC}"
    exit 0
fi
