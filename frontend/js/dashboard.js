/**
 * VyOS WireGuard Monitor Dashboard
 * 前端核心逻辑：数据拉取、渲染、告警、图表
 */

// ==================== 配置 ====================
const API = {
    tunnel: '/monitor/api/tunnel',
    branches: '/monitor/api/branches',
    config: '/monitor/api/config',
};
const POLL_INTERVAL = 5000;       // 5秒轮询
const DATA_EXPIRE_SEC = 30;       // 数据超过30秒判定过期
const HANDSHAKE_TIMEOUT = 180;    // 握手超时阈值
const BRANCH_STALE_SEC = 20;      // 分支上报过期阈值
const ALERT_DEBOUNCE = 2;         // 连续N次离线才告警
const CHART_WINDOW = 360;         // 趋势图保留点数（30分钟÷5秒）
const ALERT_SOUND_ENABLED = true;

// ==================== 全局状态 ====================
let tunnelData = null;
let branchData = null;
let configData = null;
let offlineCounts = {};           // {peer_key: consecutive_offline_count}
let alerts = [];                  // 告警历史
let chartTunnel = null;
let chartBranch = null;
let tunnelHistory = { time: [], rx: [], tx: [] };
let branchHistory = {};           // {branch_id: {time:[], cpu:[], mem:[]}}

// ==================== 初始化 ====================
document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    fetchConfig();
    poll();
    setInterval(poll, POLL_INTERVAL);
});

// ==================== 数据拉取 ====================
async function fetchJSON(url) {
    try {
        const resp = await fetch(url, { cache: 'no-store' });
        if (!resp.ok) return null;
        return await resp.json();
    } catch (e) {
        console.error(`Fetch ${url} 失败:`, e);
        return null;
    }
}

async function fetchConfig() {
    configData = await fetchJSON(API.config);
}

async function poll() {
    const [t, b] = await Promise.all([
        fetchJSON(API.tunnel),
        fetchJSON(API.branches),
    ]);

    if (t) tunnelData = t;
    if (b) branchData = b;

    render();
    updateCharts();
    checkAlerts();
}

// ==================== 渲染 ====================
function render() {
    renderGlobalBar();
    renderPeerGrid();
    renderBranchGrid();
    renderDataFreshness();
}

function renderGlobalBar() {
    if (!tunnelData) return;
    const sys = tunnelData.system;
    document.getElementById('hostname').textContent = sys.hostname || 'Unknown';
    document.getElementById('global-cpu').textContent = sys.cpu_percent.toFixed(1) + '%';
    document.getElementById('global-mem').textContent = sys.memory_percent.toFixed(1) + '%';
    document.getElementById('global-tunnel').textContent = `${sys.tunnel_active} / ${sys.tunnel_total}`;
    document.getElementById('last-update').textContent = formatTime(tunnelData.updated_at);
}

function renderPeerGrid() {
    if (!tunnelData) return;
    const grid = document.getElementById('peer-grid');
    grid.innerHTML = tunnelData.peers.map(peer => {
        const statusClass = peer.status === 'online' ? 'online' : 'offline';
        return `
        <div class="peer-card ${statusClass}">
            <div class="card-header">
                <span class="name"><i class="fas fa-globe"></i> ${escHtml(peer.name)}</span>
                <span class="status ${statusClass}">${peer.status === 'online' ? 'CONNECTED' : 'DISCONNECTED'}</span>
            </div>
            <div class="card-metrics">
                <div class="metric"><span class="label">接口</span><span class="value">${peer.interface}</span></div>
                <div class="metric"><span class="label">握手</span><span class="value">${peer.last_handshake_seconds_ago}s ago</span></div>
                <div class="metric"><span class="label">↓ 下行</span><span class="value">${peer.rx_rate_mbps} Mbps</span></div>
                <div class="metric"><span class="label">↑ 上行</span><span class="value">${peer.tx_rate_mbps} Mbps</span></div>
                <div class="metric"><span class="label">Endpoint</span><span class="value">${peer.endpoint || '-'}</span></div>
                <div class="metric"><span class="label">Branch</span><span class="value">${peer.branch_id || '-'}</span></div>
            </div>
        </div>`;
    }).join('');
}

function renderBranchGrid() {
    if (!branchData) return;
    const grid = document.getElementById('branch-grid');
    const now = Math.floor(Date.now() / 1000);

    grid.innerHTML = branchData.branches.map(br => {
        const isStale = br.stale || (now - br.reported_at > BRANCH_STALE_SEC);
        const statusClass = isStale ? 'stale' : 'active';
        const statusLabel = isStale ? '上报中断' : '正常';

        // 接口流量汇总
        const ifaceHtml = br.interfaces ? Object.entries(br.interfaces).map(([iface, data]) =>
            `<div class="metric"><span class="label">${iface} ↓↑</span><span class="value">${data.rx_mbps}/${data.tx_mbps} Mbps</span></div>`
        ).join('') : '';

        return `
        <div class="branch-card ${statusClass}">
            <div class="card-header">
                <span class="name"><i class="fas fa-building"></i> ${escHtml(br.branch_id)}</span>
                <span class="status ${statusClass}">${statusLabel}</span>
            </div>
            <div class="card-metrics">
                <div class="metric"><span class="label">CPU</span><span class="value">${br.cpu_percent?.toFixed(1) || '-'}%</span></div>
                <div class="metric"><span class="label">内存</span><span class="value">${br.memory_percent?.toFixed(1) || '-'}%</span></div>
                <div class="metric"><span class="label">负载 1/5/15</span><span class="value">${br.load_1m || '-'} / ${br.load_5m || '-'} / ${br.load_15m || '-'}</span></div>
                <div class="metric"><span class="label">上报时间</span><span class="value">${formatTime(br.reported_at)}</span></div>
                ${ifaceHtml}
            </div>
        </div>`;
    }).join('');
}

function renderDataFreshness() {
    const el = document.getElementById('data-freshness');
    if (!tunnelData) {
        el.className = 'data-freshness expired';
        el.title = '无数据';
        return;
    }
    const age = Math.floor(Date.now() / 1000) - tunnelData.updated_at;
    if (age < 10) {
        el.className = 'data-freshness fresh';
        el.title = '数据实时';
    } else if (age < DATA_EXPIRE_SEC) {
        el.className = 'data-freshness stale';
        el.title = `数据延迟 ${age}s`;
    } else {
        el.className = 'data-freshness expired';
        el.title = `数据过期 ${age}s！采集器可能已停止`;
    }
}

// ==================== 告警引擎 ====================
function checkAlerts() {
    if (!tunnelData) return;
    const now = Math.floor(Date.now() / 1000);

    // 维度1：隧道告警
    tunnelData.peers.forEach(peer => {
        const key = peer.peer;
        if (peer.status === 'offline') {
            offlineCounts[key] = (offlineCounts[key] || 0) + 1;
            if (offlineCounts[key] === ALERT_DEBOUNCE) {
                addAlert('critical', `隧道离线: ${peer.name}（连续 ${ALERT_DEBOUNCE} 次无握手）`);
            }
        } else {
            if (offlineCounts[key] >= ALERT_DEBOUNCE) {
                addAlert('info', `隧道恢复: ${peer.name}`);
            }
            offlineCounts[key] = 0;
        }
    });

    // 维度2：分支告警
    if (branchData) {
        branchData.branches.forEach(br => {
            if (br.stale || (now - br.reported_at > BRANCH_STALE_SEC)) {
                addAlert('warning', `分支上报中断: ${br.branch_id}（超过 ${BRANCH_STALE_SEC}s 无数据）`);
            }
            if (br.cpu_percent > 90) {
                addAlert('warning', `分支 CPU 高: ${br.branch_id} = ${br.cpu_percent}%`);
            }
            if (br.memory_percent > 90) {
                addAlert('warning', `分支内存高: ${br.branch_id} = ${br.memory_percent}%`);
            }
        });
    }

    // 采集器心跳检测
    if (tunnelData.collector_heartbeat) {
        const hbAge = now - tunnelData.collector_heartbeat;
        if (hbAge > 15) {
            addAlert('critical', `采集器心跳丢失！最后心跳 ${hbAge}s 前`);
        }
    }
}

function addAlert(level, message) {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });

    // 去重（5秒内相同消息不重复）
    const recent = alerts.find(a => a.message === message && (now - a.timestamp) < 5000);
    if (recent) return;

    alerts.unshift({ level, message, time: timeStr, timestamp: now });
    if (alerts.length > 50) alerts.pop();

    renderAlerts();

    // 播放告警音
    if (level === 'critical' && ALERT_SOUND_ENABLED) {
        try { document.getElementById('alert-sound').play(); } catch(e) {}
    }
}

function renderAlerts() {
    const el = document.getElementById('alert-log');
    if (alerts.length === 0) {
        el.innerHTML = '<div class="no-alerts">暂无告警</div>';
        return;
    }
    el.innerHTML = alerts.slice(0, 20).map(a => `
        <div class="alert-item ${a.level}">
            <span class="icon"><i class="fas fa-${a.level === 'critical' ? 'exclamation-circle' : a.level === 'warning' ? 'exclamation-triangle' : 'info-circle'}"></i></span>
            <span class="time">${a.time}</span>
            <span class="msg">${escHtml(a.message)}</span>
        </div>
    `).join('');
}

// ==================== 图表 ====================
function initCharts() {
    const tunnelEl = document.getElementById('chart-tunnel-bw');
    const branchEl = document.getElementById('chart-branch-load');

    if (typeof echarts !== 'undefined') {
        chartTunnel = echarts.init(tunnelEl, 'dark');
        chartBranch = echarts.init(branchEl, 'dark');

        chartTunnel.setOption(getLineChartOption('隧道带宽', ['总下行 (Mbps)', '总上行 (Mbps)']));
        chartBranch.setOption(getLineChartOption('分支负载', ['CPU %', '内存 %']));

        window.addEventListener('resize', () => {
            chartTunnel?.resize();
            chartBranch?.resize();
        });
    } else {
        tunnelEl.innerHTML = '<p style="text-align:center;color:#999;padding:40px;">ECharts 未加载（需部署离线资源）</p>';
        branchEl.innerHTML = '<p style="text-align:center;color:#999;padding:40px;">ECharts 未加载（需部署离线资源）</p>';
    }
}

function getLineChartOption(title, seriesNames) {
    return {
        backgroundColor: 'transparent',
        grid: { top: 30, right: 20, bottom: 30, left: 50 },
        xAxis: { type: 'category', data: [], axisLabel: { color: '#9aa0a6', fontSize: 10 } },
        yAxis: { type: 'value', axisLabel: { color: '#9aa0a6' }, splitLine: { lineStyle: { color: '#2a3a4a' } } },
        series: seriesNames.map((name, i) => ({
            name,
            type: 'line',
            smooth: true,
            data: [],
            lineStyle: { width: 2 },
            areaStyle: { opacity: 0.1 },
        })),
        legend: { textStyle: { color: '#9aa0a6' }, top: 0 },
        tooltip: { trigger: 'axis' },
    };
}

function updateCharts() {
    if (!tunnelData || !chartTunnel) return;

    const now = new Date().toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // 隧道带宽趋势
    tunnelHistory.time.push(now);
    tunnelHistory.rx.push(tunnelData.totals.rx_mbps);
    tunnelHistory.tx.push(tunnelData.totals.tx_mbps);
    if (tunnelHistory.time.length > CHART_WINDOW) {
        tunnelHistory.time.shift();
        tunnelHistory.rx.shift();
        tunnelHistory.tx.shift();
    }

    chartTunnel.setOption({
        xAxis: { data: tunnelHistory.time },
        series: [
            { data: tunnelHistory.rx },
            { data: tunnelHistory.tx },
        ],
    });

    // 分支负载趋势（取第一个分支作示例，实际可扩展为多系列）
    if (branchData && branchData.branches.length > 0) {
        const br = branchData.branches[0];
        const bid = br.branch_id;
        if (!branchHistory[bid]) branchHistory[bid] = { time: [], cpu: [], mem: [] };

        const bh = branchHistory[bid];
        bh.time.push(now);
        bh.cpu.push(br.cpu_percent || 0);
        bh.mem.push(br.memory_percent || 0);
        if (bh.time.length > CHART_WINDOW) {
            bh.time.shift();
            bh.cpu.shift();
            bh.mem.shift();
        }

        chartBranch.setOption({
            xAxis: { data: bh.time },
            series: [
                { data: bh.cpu },
                { data: bh.mem },
            ],
        });
    }
}

// ==================== 工具函数 ====================
function formatTime(unixTs) {
    if (!unixTs) return '--:--:--';
    return new Date(unixTs * 1000).toLocaleTimeString('zh-CN', { hour12: false });
}

function escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}
