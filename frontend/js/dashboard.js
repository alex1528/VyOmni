/**
 * VyOmni Dashboard - Enhanced with 6 dynamic features
 * 1. Status change animations  2. Card click modal  3. Topology graph
 * 4. Time window selector  5. Branch map  6. Theme toggle + responsive
 */

// ==================== 配置 ====================
const API = {
    tunnel: '/api/tunnel',
    branches: '/api/branches',
    config: '/api/config',
};
const POLL_INTERVAL = 5000;
const DATA_EXPIRE_SEC = 30;
const HANDSHAKE_TIMEOUT = 180;
const BRANCH_STALE_SEC = 20;
const ALERT_DEBOUNCE = 2;
const ALERT_SOUND_ENABLED = true;

// 历史窗口配置（每个选项对应的数据点数）
const TIME_WINDOWS = { 60: '5min', 360: '30min', 720: '1h', 4320: '6h', 17280: '24h' };
let currentTimeWindow = 60; // 默认5分钟（60个5秒点）

// ==================== 全局状态 ====================
let tunnelData = null;
let branchData = null;
let configData = null;
let previousPeerStatus = {};  // 记录上次状态用于动画
let offlineCounts = {};
let alerts = [];

// 图表实例
let chartTunnel = null;
let chartBranch = null;
let chartTopology = null;
let chartMap = null;
let modalChart = null;

// 历史数据（最大保留24h = 17280点）
let tunnelHistory = { time: [], rx: [], tx: [] };
let branchHistory = {};

// ==================== 初始化 ====================
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    initCharts();
    initTimeSelector();
    initModal();
    fetchConfig();
    poll();
    setInterval(poll, POLL_INTERVAL);
});

// ==================== 主题切换 ====================
function initTheme() {
    const saved = localStorage.getItem('vyomni-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    updateThemeIcon(saved);

    document.getElementById('theme-toggle').addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('vyomni-theme', next);
        updateThemeIcon(next);
        // 重新初始化图表以适配主题
        reinitAllCharts();
    });
}

function updateThemeIcon(theme) {
    const icon = document.getElementById('theme-icon');
    icon.className = theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
}

function getEchartsTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : null;
}

// ==================== 时间选择器 ====================
function initTimeSelector() {
    document.getElementById('time-selector').addEventListener('click', (e) => {
        const btn = e.target.closest('.time-btn');
        if (!btn) return;
        document.querySelectorAll('.time-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentTimeWindow = parseInt(btn.dataset.window);
        updateTrendCharts();
    });
}

// ==================== 模态弹窗 ====================
function initModal() {
    const overlay = document.getElementById('modal-overlay');
    const closeBtn = document.getElementById('modal-close');
    closeBtn.addEventListener('click', closeModal);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeModal();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });
}

function openModal(title, bodyHtml) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = bodyHtml;
    document.getElementById('modal-overlay').classList.add('active');
}

function closeModal() {
    document.getElementById('modal-overlay').classList.remove('active');
    if (modalChart) { modalChart.dispose(); modalChart = null; }
}

function showPeerDetail(peer) {
    const html = `
        <div class="detail-grid">
            <div class="detail-item"><span class="label">名称</span><span class="value">${escHtml(peer.name)}</span></div>
            <div class="detail-item"><span class="label">状态</span><span class="value">${peer.status === 'online' ? '🟢 在线' : '🔴 离线'}</span></div>
            <div class="detail-item"><span class="label">接口</span><span class="value">${peer.interface}</span></div>
            <div class="detail-item"><span class="label">Endpoint</span><span class="value">${peer.endpoint || '-'}</span></div>
            <div class="detail-item"><span class="label">最近握手</span><span class="value">${peer.last_handshake_seconds_ago}s ago</span></div>
            <div class="detail-item"><span class="label">分支</span><span class="value">${peer.branch_id || '-'}</span></div>
            <div class="detail-item"><span class="label">下行速率</span><span class="value">${peer.rx_rate_mbps} Mbps</span></div>
            <div class="detail-item"><span class="label">上行速率</span><span class="value">${peer.tx_rate_mbps} Mbps</span></div>
            <div class="detail-item"><span class="label">总接收</span><span class="value">${formatBytes(peer.rx_bytes)}</span></div>
            <div class="detail-item"><span class="label">总发送</span><span class="value">${formatBytes(peer.tx_bytes)}</span></div>
        </div>
        <h4 style="margin:12px 0 8px;font-size:0.9em;color:var(--text-secondary);">流量曲线</h4>
        <div class="modal-chart" id="modal-chart-peer"></div>
    `;
    openModal(`Peer: ${peer.name}`, html);
    // 渲染模态图表
    setTimeout(() => renderModalPeerChart(peer), 100);
}

function showBranchDetail(br) {
    const ifaceRows = br.interfaces ? Object.entries(br.interfaces).map(([iface, data]) =>
        `<div class="detail-item"><span class="label">${iface}</span><span class="value">↓${data.rx_mbps} / ↑${data.tx_mbps} Mbps</span></div>`
    ).join('') : '';

    const html = `
        <div class="detail-grid">
            <div class="detail-item"><span class="label">分支 ID</span><span class="value">${escHtml(br.branch_id)}</span></div>
            <div class="detail-item"><span class="label">状态</span><span class="value">${br.stale ? '🟡 中断' : '🟢 正常'}</span></div>
            <div class="detail-item"><span class="label">CPU</span><span class="value">${br.cpu_percent?.toFixed(1) || '-'}%</span></div>
            <div class="detail-item"><span class="label">内存</span><span class="value">${br.memory_percent?.toFixed(1) || '-'}%</span></div>
            <div class="detail-item"><span class="label">负载 1/5/15</span><span class="value">${br.load_1m || '-'} / ${br.load_5m || '-'} / ${br.load_15m || '-'}</span></div>
            <div class="detail-item"><span class="label">上报时间</span><span class="value">${formatTime(br.reported_at)}</span></div>
            ${ifaceRows}
        </div>
        <h4 style="margin:12px 0 8px;font-size:0.9em;color:var(--text-secondary);">负载曲线</h4>
        <div class="modal-chart" id="modal-chart-branch"></div>
    `;
    openModal(`分支: ${br.branch_id}`, html);
    setTimeout(() => renderModalBranchChart(br), 100);
}

function renderModalPeerChart(peer) {
    const el = document.getElementById('modal-chart-peer');
    if (!el || typeof echarts === 'undefined') return;
    modalChart = echarts.init(el, getEchartsTheme());
    // 从隧道历史中提取该peer相关数据（简化：显示总流量趋势）
    const window = Math.min(currentTimeWindow, tunnelHistory.time.length);
    const times = tunnelHistory.time.slice(-window);
    const rx = tunnelHistory.rx.slice(-window);
    const tx = tunnelHistory.tx.slice(-window);
    modalChart.setOption({
        backgroundColor: 'transparent',
        grid: { top: 30, right: 20, bottom: 30, left: 50 },
        xAxis: { type: 'category', data: times, axisLabel: { color: 'var(--text-secondary)', fontSize: 10, rotate: 30 } },
        yAxis: { type: 'value', axisLabel: { color: 'var(--text-secondary)' }, splitLine: { lineStyle: { color: 'var(--chart-split)' } } },
        series: [
            { name: '下行 Mbps', type: 'line', smooth: true, data: rx, lineStyle: { width: 2, color: '#4fc3f7' }, areaStyle: { opacity: 0.1, color: '#4fc3f7' } },
            { name: '上行 Mbps', type: 'line', smooth: true, data: tx, lineStyle: { width: 2, color: '#66bb6a' }, areaStyle: { opacity: 0.1, color: '#66bb6a' } },
        ],
        legend: { textStyle: { color: '#9aa0a6' }, top: 0 },
        tooltip: { trigger: 'axis' },
    });
}

function renderModalBranchChart(br) {
    const el = document.getElementById('modal-chart-branch');
    if (!el || typeof echarts === 'undefined') return;
    modalChart = echarts.init(el, getEchartsTheme());
    const bh = branchHistory[br.branch_id];
    if (!bh) { el.innerHTML = '<p style="text-align:center;color:var(--text-secondary);padding:20px;">暂无历史数据</p>'; return; }
    const window = Math.min(currentTimeWindow, bh.time.length);
    modalChart.setOption({
        backgroundColor: 'transparent',
        grid: { top: 30, right: 20, bottom: 30, left: 50 },
        xAxis: { type: 'category', data: bh.time.slice(-window), axisLabel: { color: 'var(--text-secondary)', fontSize: 10, rotate: 30 } },
        yAxis: { type: 'value', max: 100, axisLabel: { color: 'var(--text-secondary)' }, splitLine: { lineStyle: { color: 'var(--chart-split)' } } },
        series: [
            { name: 'CPU %', type: 'line', smooth: true, data: bh.cpu.slice(-window), lineStyle: { width: 2, color: '#ffa726' }, areaStyle: { opacity: 0.1, color: '#ffa726' } },
            { name: '内存 %', type: 'line', smooth: true, data: bh.mem.slice(-window), lineStyle: { width: 2, color: '#ab47bc' }, areaStyle: { opacity: 0.1, color: '#ab47bc' } },
        ],
        legend: { textStyle: { color: '#9aa0a6' }, top: 0 },
        tooltip: { trigger: 'axis' },
    });
}

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

async function fetchConfig() { configData = await fetchJSON(API.config); }

async function poll() {
    const [t, b] = await Promise.all([fetchJSON(API.tunnel), fetchJSON(API.branches)]);
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
        const key = peer.peer || peer.name;
        const prevStatus = previousPeerStatus[key];
        let animClass = '';
        if (prevStatus && prevStatus !== peer.status) {
            animClass = peer.status === 'online' ? 'status-changed went-online' : 'status-changed went-offline';
        }
        previousPeerStatus[key] = peer.status;

        return `
        <div class="peer-card ${statusClass} ${animClass}" data-peer='${escAttr(JSON.stringify(peer))}'>
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

    // 绑定点击事件
    grid.querySelectorAll('.peer-card').forEach(card => {
        card.addEventListener('click', () => {
            try { showPeerDetail(JSON.parse(card.dataset.peer)); } catch(e) {}
        });
    });

    // 动画结束后移除类
    grid.querySelectorAll('.status-changed').forEach(card => {
        card.addEventListener('animationend', () => {
            card.classList.remove('status-changed', 'went-online', 'went-offline');
        });
    });
}

function renderBranchGrid() {
    if (!branchData) return;
    const grid = document.getElementById('branch-grid');
    const now = Math.floor(Date.now() / 1000);

    grid.innerHTML = branchData.branches.map(br => {
        const isStale = br.stale || (now - br.reported_at > BRANCH_STALE_SEC);
        const statusClass = isStale ? 'stale' : 'active';
        const statusLabel = isStale ? '上报中断' : '正常';
        const ifaceHtml = br.interfaces ? Object.entries(br.interfaces).map(([iface, data]) =>
            `<div class="metric"><span class="label">${iface} ↓↑</span><span class="value">${data.rx_mbps}/${data.tx_mbps} Mbps</span></div>`
        ).join('') : '';

        return `
        <div class="branch-card ${statusClass}" data-branch='${escAttr(JSON.stringify(br))}'>
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

    grid.querySelectorAll('.branch-card').forEach(card => {
        card.addEventListener('click', () => {
            try { showBranchDetail(JSON.parse(card.dataset.branch)); } catch(e) {}
        });
    });
}

function renderDataFreshness() {
    const el = document.getElementById('data-freshness');
    if (!tunnelData) { el.className = 'data-freshness expired'; el.title = '无数据'; return; }
    const age = Math.floor(Date.now() / 1000) - tunnelData.updated_at;
    if (age < 10) { el.className = 'data-freshness fresh'; el.title = '数据实时'; }
    else if (age < DATA_EXPIRE_SEC) { el.className = 'data-freshness stale'; el.title = `数据延迟 ${age}s`; }
    else { el.className = 'data-freshness expired'; el.title = `数据过期 ${age}s！采集器可能已停止`; }
}

// ==================== 告警引擎 ====================
function checkAlerts() {
    if (!tunnelData) return;
    const now = Math.floor(Date.now() / 1000);
    tunnelData.peers.forEach(peer => {
        const key = peer.peer;
        if (peer.status === 'offline') {
            offlineCounts[key] = (offlineCounts[key] || 0) + 1;
            if (offlineCounts[key] === ALERT_DEBOUNCE) {
                addAlert('critical', `隧道离线: ${peer.name}（连续 ${ALERT_DEBOUNCE} 次无握手）`);
            }
        } else {
            if (offlineCounts[key] >= ALERT_DEBOUNCE) addAlert('info', `隧道恢复: ${peer.name}`);
            offlineCounts[key] = 0;
        }
    });
    if (branchData) {
        branchData.branches.forEach(br => {
            if (br.stale || (now - br.reported_at > BRANCH_STALE_SEC))
                addAlert('warning', `分支上报中断: ${br.branch_id}（超过 ${BRANCH_STALE_SEC}s 无数据）`);
            if (br.cpu_percent > 90) addAlert('warning', `分支 CPU 高: ${br.branch_id} = ${br.cpu_percent}%`);
            if (br.memory_percent > 90) addAlert('warning', `分支内存高: ${br.branch_id} = ${br.memory_percent}%`);
        });
    }
    if (tunnelData.collector_heartbeat) {
        const hbAge = now - tunnelData.collector_heartbeat;
        if (hbAge > 15) addAlert('critical', `采集器心跳丢失！最后心跳 ${hbAge}s 前`);
    }
}

function addAlert(level, message) {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });
    const recent = alerts.find(a => a.message === message && (now - a.timestamp) < 5000);
    if (recent) return;
    alerts.unshift({ level, message, time: timeStr, timestamp: now });
    if (alerts.length > 50) alerts.pop();
    renderAlerts();
    if (level === 'critical' && ALERT_SOUND_ENABLED) {
        try { document.getElementById('alert-sound').play(); } catch(e) {}
    }
}

function renderAlerts() {
    const el = document.getElementById('alert-log');
    if (alerts.length === 0) { el.innerHTML = '<div class="no-alerts">暂无告警</div>'; return; }
    el.innerHTML = alerts.slice(0, 20).map(a => `
        <div class="alert-item ${a.level}">
            <span class="icon"><i class="fas fa-${a.level === 'critical' ? 'exclamation-circle' : a.level === 'warning' ? 'exclamation-triangle' : 'info-circle'}"></i></span>
            <span class="time">${a.time}</span>
            <span class="msg">${escHtml(a.message)}</span>
        </div>
    `).join('');
}

// ==================== 图表初始化 ====================
function initCharts() {
    if (typeof echarts === 'undefined') {
        document.querySelectorAll('.chart').forEach(el => {
            el.innerHTML = '<p style="text-align:center;color:var(--text-secondary);padding:40px;">ECharts 未加载</p>';
        });
        return;
    }
    const theme = getEchartsTheme();
    chartTunnel = echarts.init(document.getElementById('chart-tunnel-bw'), theme);
    chartBranch = echarts.init(document.getElementById('chart-branch-load'), theme);
    chartTopology = echarts.init(document.getElementById('chart-topology'), theme);
    chartMap = echarts.init(document.getElementById('chart-map'), theme);

    chartTunnel.setOption(getLineChartOption(['总下行 (Mbps)', '总上行 (Mbps)']));
    chartBranch.setOption(getLineChartOption(['CPU %', '内存 %']));

    window.addEventListener('resize', () => {
        chartTunnel?.resize();
        chartBranch?.resize();
        chartTopology?.resize();
        chartMap?.resize();
    });
}

function reinitAllCharts() {
    if (typeof echarts === 'undefined') return;
    const theme = getEchartsTheme();
    [chartTunnel, chartBranch, chartTopology, chartMap].forEach(c => { if (c) c.dispose(); });
    chartTunnel = echarts.init(document.getElementById('chart-tunnel-bw'), theme);
    chartBranch = echarts.init(document.getElementById('chart-branch-load'), theme);
    chartTopology = echarts.init(document.getElementById('chart-topology'), theme);
    chartMap = echarts.init(document.getElementById('chart-map'), theme);
    chartTunnel.setOption(getLineChartOption(['总下行 (Mbps)', '总上行 (Mbps)']));
    chartBranch.setOption(getLineChartOption(['CPU %', '内存 %']));
    updateTrendCharts();
    updateTopology();
    updateMap();
}

function getLineChartOption(seriesNames) {
    return {
        backgroundColor: 'transparent',
        grid: { top: 30, right: 20, bottom: 30, left: 50 },
        xAxis: { type: 'category', data: [], axisLabel: { fontSize: 10 } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: '#2a3a4a' } } },
        series: seriesNames.map(name => ({
            name, type: 'line', smooth: true, data: [],
            lineStyle: { width: 2 }, areaStyle: { opacity: 0.1 },
        })),
        legend: { top: 0 },
        tooltip: { trigger: 'axis' },
    };
}

// ==================== 图表更新 ====================
function updateCharts() {
    if (!tunnelData || !chartTunnel) return;
    const now = new Date().toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // 推入历史（保留最大24h的点）
    tunnelHistory.time.push(now);
    tunnelHistory.rx.push(tunnelData.totals.rx_mbps);
    tunnelHistory.tx.push(tunnelData.totals.tx_mbps);
    const maxPoints = 17280;
    if (tunnelHistory.time.length > maxPoints) {
        tunnelHistory.time.shift(); tunnelHistory.rx.shift(); tunnelHistory.tx.shift();
    }

    if (branchData && branchData.branches.length > 0) {
        branchData.branches.forEach(br => {
            const bid = br.branch_id;
            if (!branchHistory[bid]) branchHistory[bid] = { time: [], cpu: [], mem: [] };
            const bh = branchHistory[bid];
            bh.time.push(now);
            bh.cpu.push(br.cpu_percent || 0);
            bh.mem.push(br.memory_percent || 0);
            if (bh.time.length > maxPoints) { bh.time.shift(); bh.cpu.shift(); bh.mem.shift(); }
        });
    }

    updateTrendCharts();
    updateTopology();
    updateMap();
}

function updateTrendCharts() {
    if (!chartTunnel) return;
    const w = currentTimeWindow;
    const times = tunnelHistory.time.slice(-w);
    const rx = tunnelHistory.rx.slice(-w);
    const tx = tunnelHistory.tx.slice(-w);

    chartTunnel.setOption({ xAxis: { data: times }, series: [{ data: rx }, { data: tx }] });

    // 分支负载（取第一个分支）
    if (branchData && branchData.branches.length > 0) {
        const bid = branchData.branches[0].branch_id;
        const bh = branchHistory[bid];
        if (bh) {
            chartBranch.setOption({
                xAxis: { data: bh.time.slice(-w) },
                series: [{ data: bh.cpu.slice(-w) }, { data: bh.mem.slice(-w) }]
            });
        }
    }
}

// ==================== 网络拓扑图 ====================
function updateTopology() {
    if (!chartTopology || !tunnelData) return;
    const peers = tunnelData.peers;
    const nodes = [
        {
            name: '总部',
            x: 300, y: 200,
            symbolSize: 50,
            itemStyle: { color: '#4fc3f7' },
            label: { show: true, fontSize: 14, fontWeight: 'bold' }
        }
    ];
    const links = [];

    const angleStep = (2 * Math.PI) / Math.max(peers.length, 1);
    const radius = 160;

    peers.forEach((peer, i) => {
        const angle = angleStep * i - Math.PI / 2;
        const x = 300 + radius * Math.cos(angle);
        const y = 200 + radius * Math.sin(angle);
        const isOnline = peer.status === 'online';
        const traffic = (peer.rx_rate_mbps || 0) + (peer.tx_rate_mbps || 0);

        nodes.push({
            name: peer.name || `Peer-${i}`,
            x: x, y: y,
            symbolSize: 30 + Math.min(traffic * 2, 20),
            itemStyle: { color: isOnline ? '#66bb6a' : '#ef5350' },
            label: { show: true, fontSize: 11 }
        });

        links.push({
            source: '总部',
            target: peer.name || `Peer-${i}`,
            lineStyle: {
                width: 1 + Math.min(traffic, 10),
                color: isOnline ? '#66bb6a' : '#ef5350',
                opacity: isOnline ? 0.8 : 0.3,
                type: isOnline ? 'solid' : 'dashed'
            }
        });
    });

    chartTopology.setOption({
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'item',
            formatter: (params) => {
                if (params.dataType === 'node') return params.name;
                if (params.dataType === 'edge') return `${params.data.source} → ${params.data.target}`;
                return '';
            }
        },
        series: [{
            type: 'graph',
            layout: 'none',
            roam: true,
            data: nodes,
            links: links,
            lineStyle: { curveness: 0.1 },
            emphasis: {
                focus: 'adjacency',
                lineStyle: { width: 6 }
            },
            animationDuration: 800,
            animationEasingUpdate: 'quinticInOut',
        }]
    });
}

// ==================== 分支地图 ====================
function updateMap() {
    if (!chartMap || !branchData) return;
    const branches = branchData.branches;

    // 使用散点图坐标模拟地理位置
    // 如果分支数据有 lat/lng 就用，否则均匀分布
    const scatterData = branches.map((br, i) => {
        const lat = br.latitude || (25 + Math.random() * 15);
        const lng = br.longitude || (100 + Math.random() * 20);
        const isStale = br.stale || (Math.floor(Date.now() / 1000) - br.reported_at > BRANCH_STALE_SEC);
        return {
            name: br.branch_id,
            value: [lng, lat, br.cpu_percent || 0],
            itemStyle: { color: isStale ? '#ffa726' : '#66bb6a' },
            symbolSize: isStale ? 12 : 16,
        };
    });

    // 尝试用 geo 组件（如果注册了中国地图），否则用坐标散点
    const hasGeo = echarts.getMap && echarts.getMap('china');
    if (hasGeo) {
        chartMap.setOption({
            backgroundColor: 'transparent',
            geo: {
                map: 'china',
                roam: true,
                itemStyle: { areaColor: '#1e2d3d', borderColor: '#3a4a5a' },
                emphasis: { itemStyle: { areaColor: '#2a3a4a' } },
                label: { show: false }
            },
            tooltip: {
                trigger: 'item',
                formatter: (p) => `${p.name}<br/>CPU: ${p.value[2]}%`
            },
            series: [{
                type: 'scatter',
                coordinateSystem: 'geo',
                data: scatterData,
                encode: { value: 2 },
                label: { show: true, formatter: '{b}', position: 'right', fontSize: 11 },
            }]
        });
    } else {
        // 无地图注册：使用笛卡尔坐标散点
        chartMap.setOption({
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'item',
                formatter: (p) => `${p.name}<br/>CPU: ${p.value[2]}%`
            },
            xAxis: {
                type: 'value', min: 95, max: 125, name: '经度',
                axisLine: { lineStyle: { color: '#555' } },
                splitLine: { lineStyle: { color: '#2a3a4a' } }
            },
            yAxis: {
                type: 'value', min: 20, max: 45, name: '纬度',
                axisLine: { lineStyle: { color: '#555' } },
                splitLine: { lineStyle: { color: '#2a3a4a' } }
            },
            series: [{
                type: 'scatter',
                data: scatterData.map(d => ({
                    name: d.name,
                    value: d.value,
                    itemStyle: d.itemStyle,
                    symbolSize: d.symbolSize
                })),
                label: { show: true, formatter: '{b}', position: 'right', fontSize: 11, color: 'var(--text-primary)' },
                emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(79,195,247,0.5)' } }
            }]
        });
    }
}

// ==================== 工具函数 ====================
function formatTime(unixTs) {
    if (!unixTs) return '--:--:--';
    return new Date(unixTs * 1000).toLocaleTimeString('zh-CN', { hour12: false });
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
}

function escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

function escAttr(str) {
    return (str || '').replace(/'/g, '&#39;').replace(/"/g, '&quot;');
}
// ==================== 节点管理面板 ====================
let nodesData = null;
let nodesPollTimer = null;

const NODE_API = {
    list: '/api/nodes',
    approve: (id) => `/api/nodes/${id}/approve`,
    reject: (id) => `/api/nodes/${id}/reject`,
    delete: (id) => `/api/nodes/${id}`,
    config: (id) => `/api/nodes/${id}/config`,
};

// 初始化节点管理（在 DOMContentLoaded 中追加调用）
function initNodeManagement() {
    const navBtn = document.getElementById('nav-nodes');
    if (navBtn) {
        navBtn.addEventListener('click', () => {
            showNodePanel();
        });
    }
    // 如果存在节点面板容器，注入初始 HTML
    const container = document.getElementById('node-panel');
    if (container) {
        container.innerHTML = getNodePanelHtml();
        bindNodePanelEvents();
    }
}

function getNodePanelHtml() {
    return `
    <div class="node-mgmt-panel">
        <div class="node-panel-header">
            <h2><i class="fas fa-server"></i> 节点管理</h2>
            <div class="node-panel-actions">
                <button class="btn btn-sm" id="btn-refresh-nodes"><i class="fas fa-sync-alt"></i> 刷新</button>
                <select id="node-filter-status" class="node-filter-select">
                    <option value="all">全部状态</option>
                    <option value="pending">待审核</option>
                    <option value="approved">已通过</option>
                    <option value="rejected">已拒绝</option>
                </select>
                <select id="node-filter-role" class="node-filter-select">
                    <option value="all">全部角色</option>
                    <option value="hq">总部 HQ</option>
                    <option value="branch">分支 Branch</option>
                </select>
            </div>
        </div>
        <div class="node-stats-bar" id="node-stats-bar"></div>
        <div class="node-table-wrap">
            <table class="node-table" id="node-table">
                <thead>
                    <tr>
                        <th>节点 ID</th>
                        <th>主机名</th>
                        <th>角色</th>
                        <th>IP</th>
                        <th>版本</th>
                        <th>状态</th>
                        <th>上报间隔</th>
                        <th>最后在线</th>
                        <th>注册时间</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody id="node-table-body"></tbody>
            </table>
        </div>
    </div>`;
}

function bindNodePanelEvents() {
    const refreshBtn = document.getElementById('btn-refresh-nodes');
    if (refreshBtn) refreshBtn.addEventListener('click', fetchNodes);

    const filterStatus = document.getElementById('node-filter-status');
    if (filterStatus) filterStatus.addEventListener('change', renderNodeTable);

    const filterRole = document.getElementById('node-filter-role');
    if (filterRole) filterRole.addEventListener('change', renderNodeTable);
}

function showNodePanel() {
    const panel = document.getElementById('node-panel');
    if (panel) {
        panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
        if (panel.style.display === 'block') {
            if (!panel.innerHTML.trim()) {
                panel.innerHTML = getNodePanelHtml();
                bindNodePanelEvents();
            }
            fetchNodes();
            // 开始轮询
            if (!nodesPollTimer) {
                nodesPollTimer = setInterval(fetchNodes, 10000);
            }
        } else {
            if (nodesPollTimer) { clearInterval(nodesPollTimer); nodesPollTimer = null; }
        }
    }
}

async function fetchNodes() {
    const data = await fetchJSON(NODE_API.list);
    if (data && data.nodes) {
        nodesData = data.nodes;
        renderNodeStats();
        renderNodeTable();
    }
}

function renderNodeStats() {
    const el = document.getElementById('node-stats-bar');
    if (!el || !nodesData) return;
    const total = nodesData.length;
    const pending = nodesData.filter(n => n.status === 'pending').length;
    const approved = nodesData.filter(n => n.status === 'approved').length;
    const rejected = nodesData.filter(n => n.status === 'rejected').length;
    const now = Math.floor(Date.now() / 1000);
    const online = nodesData.filter(n => n.status === 'approved' && now - n.last_seen < 60).length;

    el.innerHTML = `
        <div class="node-stat"><span class="node-stat-num">${total}</span><span class="node-stat-label">总节点</span></div>
        <div class="node-stat pending"><span class="node-stat-num">${pending}</span><span class="node-stat-label">待审核</span></div>
        <div class="node-stat approved"><span class="node-stat-num">${approved}</span><span class="node-stat-label">已通过</span></div>
        <div class="node-stat online"><span class="node-stat-num">${online}</span><span class="node-stat-label">在线</span></div>
        <div class="node-stat rejected"><span class="node-stat-num">${rejected}</span><span class="node-stat-label">已拒绝</span></div>
    `;
}

function renderNodeTable() {
    const tbody = document.getElementById('node-table-body');
    if (!tbody || !nodesData) return;

    const statusFilter = document.getElementById('node-filter-status')?.value || 'all';
    const roleFilter = document.getElementById('node-filter-role')?.value || 'all';

    let filtered = nodesData;
    if (statusFilter !== 'all') filtered = filtered.filter(n => n.status === statusFilter);
    if (roleFilter !== 'all') filtered = filtered.filter(n => n.role === roleFilter);

    const now = Math.floor(Date.now() / 1000);

    tbody.innerHTML = filtered.map(node => {
        const isOnline = node.status === 'approved' && now - node.last_seen < 60;
        const statusBadge = getNodeStatusBadge(node.status);
        const roleBadge = node.role === 'hq'
            ? '<span class="role-badge hq">HQ</span>'
            : '<span class="role-badge branch">Branch</span>';
        const lastSeen = node.last_seen ? formatTimeAgo(now - node.last_seen) : '从未';
        const regTime = node.registered_at ? formatDateTime(node.registered_at) : '-';

        let actions = '';
        if (node.status === 'pending') {
            actions = `
                <button class="btn btn-xs btn-approve" onclick="nodeAction('approve','${escAttr(node.node_id)}')">
                    <i class="fas fa-check"></i> 通过
                </button>
                <button class="btn btn-xs btn-reject" onclick="nodeAction('reject','${escAttr(node.node_id)}')">
                    <i class="fas fa-times"></i> 拒绝
                </button>`;
        } else if (node.status === 'approved') {
            actions = `
                <button class="btn btn-xs btn-config" onclick="showNodeConfig('${escAttr(node.node_id)}')">
                    <i class="fas fa-cog"></i> 配置
                </button>`;
        }
        actions += `
            <button class="btn btn-xs btn-delete" onclick="nodeAction('delete','${escAttr(node.node_id)}')">
                <i class="fas fa-trash"></i>
            </button>`;

        return `
        <tr class="${isOnline ? 'row-online' : ''} ${node.status === 'pending' ? 'row-pending' : ''}">
            <td class="node-id-cell" title="${escAttr(node.node_id)}">${escHtml(node.node_id.length > 20 ? node.node_id.slice(0,20)+'...' : node.node_id)}</td>
            <td>${escHtml(node.hostname)}</td>
            <td>${roleBadge}</td>
            <td><code>${escHtml(node.ip)}</code></td>
            <td><code>${escHtml(node.version || '-')}</code></td>
            <td>${statusBadge}</td>
            <td>${node.report_interval}s</td>
            <td>${lastSeen}</td>
            <td>${regTime}</td>
            <td class="actions-cell">${actions}</td>
        </tr>`;
    }).join('');
}

function getNodeStatusBadge(status) {
    const map = {
        'pending': '<span class="status-badge pending"><i class="fas fa-clock"></i> 待审核</span>',
        'approved': '<span class="status-badge approved"><i class="fas fa-check-circle"></i> 已通过</span>',
        'rejected': '<span class="status-badge rejected"><i class="fas fa-ban"></i> 已拒绝</span>',
    };
    return map[status] || `<span class="status-badge">${status}</span>`;
}

async function nodeAction(action, nodeId) {
    if (action === 'delete') {
        if (!confirm(`确定删除节点 ${nodeId}？此操作不可逆。`)) return;
        const resp = await fetch(NODE_API.delete(nodeId), { method: 'DELETE' });
        if (resp.ok) { fetchNodes(); addAlert('info', `节点已删除: ${nodeId}`); }
    } else if (action === 'approve') {
        const resp = await fetch(NODE_API.approve(nodeId), { method: 'POST' });
        if (resp.ok) { fetchNodes(); addAlert('info', `节点已审核通过: ${nodeId}`); }
    } else if (action === 'reject') {
        const resp = await fetch(NODE_API.reject(nodeId), { method: 'POST' });
        if (resp.ok) { fetchNodes(); addAlert('info', `节点已拒绝: ${nodeId}`); }
    }
}

function showNodeConfig(nodeId) {
    const node = nodesData?.find(n => n.node_id === nodeId);
    if (!node) return;

    const html = `
    <div class="node-config-form">
        <h4>动态配置下发 — ${escHtml(node.hostname)}</h4>
        <div class="form-group">
            <label>上报间隔（秒）</label>
            <input type="number" id="cfg-interval" value="${node.report_interval || 10}" min="1" max="3600" />
        </div>
        <div class="form-group">
            <label>采集项（逗号分隔）</label>
            <input type="text" id="cfg-capabilities" value="${(node.capabilities || []).join(',')}" placeholder="system,interfaces,wireguard" />
        </div>
        <div class="form-group">
            <label>自定义标签（JSON）</label>
            <textarea id="cfg-labels" rows="3" placeholder='{"region":"east","env":"prod"}'>${JSON.stringify(node.custom_labels || {}, null, 2)}</textarea>
        </div>
        <div class="form-actions">
            <button class="btn btn-primary" onclick="submitNodeConfig('${escAttr(nodeId)}')">
                <i class="fas fa-paper-plane"></i> 下发配置
            </button>
            <button class="btn" onclick="closeModal()">取消</button>
        </div>
    </div>`;

    openModal(`节点配置: ${nodeId}`, html);
}

async function submitNodeConfig(nodeId) {
    const interval = document.getElementById('cfg-interval')?.value;
    const capsStr = document.getElementById('cfg-capabilities')?.value;
    const labelsStr = document.getElementById('cfg-labels')?.value;

    const configData = {};
    if (interval) configData.report_interval = parseInt(interval);
    if (capsStr) configData.capabilities = capsStr.split(',').map(s => s.trim()).filter(Boolean);
    if (labelsStr) {
        try { configData.custom_labels = JSON.parse(labelsStr); }
        catch(e) { alert('自定义标签 JSON 格式错误'); return; }
    }

    const resp = await fetch(NODE_API.config(nodeId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configData),
    });

    if (resp.ok) {
        closeModal();
        addAlert('info', `配置已下发到 ${nodeId}（下次上报时生效）`);
        fetchNodes();
    } else {
        alert('配置下发失败');
    }
}

// 工具函数：时间前显示
function formatTimeAgo(seconds) {
    if (seconds < 0) return '刚刚';
    if (seconds < 60) return `${seconds}s 前`;
    if (seconds < 3600) return `${Math.floor(seconds/60)}m 前`;
    if (seconds < 86400) return `${Math.floor(seconds/3600)}h 前`;
    return `${Math.floor(seconds/86400)}d 前`;
}

function formatDateTime(unixTs) {
    if (!unixTs) return '-';
    const d = new Date(unixTs * 1000);
    return d.toLocaleDateString('zh-CN') + ' ' + d.toLocaleTimeString('zh-CN', {hour12:false, hour:'2-digit', minute:'2-digit'});
}

// 在 DOMContentLoaded 时追加初始化
(function() {
    const origInit = window.onload;
    // 延迟初始化，确保 DOM 就绪
    setTimeout(() => {
        initNodeManagement();
    }, 100);
})();
