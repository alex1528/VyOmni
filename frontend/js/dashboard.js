/**
 * VyOmni Dashboard - Enhanced with 6 dynamic features + Token Deploy UI
 * 1. Status change animations  2. Card click modal  3. Topology graph
 * 4. Time window selector  5. Branch map  6. Theme toggle + responsive
 * 7. Token-based node deployment panel
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
    initNodeManagement();
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
            <div class="detail-item"><span class="label">名称</span><span class="value">${escHtml(peer.display_name || peer.name)}</span></div>
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
    setTimeout(() => renderModalPeerChart(peer), 100);
}

function showBranchDetail(br) {
    const ifaceRows = br.interfaces ? Object.entries(br.interfaces).map(([iface, data]) =>
        `<div class="detail-item"><span class="label">${iface}</span><span class="value">↓${data.rx_mbps} / ↑${data.tx_mbps} Mbps</span></div>`
    ).join('') : '';

    const html = `
        <div class="detail-grid">
            <div class="detail-item"><span class="label">分支 ID</span><span class="value">${escHtml(br.display_name || br.hostname || br.branch_id)}</span></div>
            <div class="detail-item"><span class="label">状态</span><span class="value">${br.stale ? '🟡 中断' : '🟢 正常'}</span></div>
            <div class="detail-item"><span class="label">CPU</span><span class="value">${br.cpu_percent?.toFixed(1) || '-'}%</span></div>
            <div class="detail-item"><span class="label">内存</span><span class="value">${br.memory_percent?.toFixed(1) || '-'}%</span></div>
            <div class="detail-item"><span class="label">负载 1/5/15</span><span class="value">${(br.load_1m != null ? br.load_1m : '-')} / ${(br.load_5m != null ? br.load_5m : '-')} / ${(br.load_15m != null ? br.load_15m : '-')}</span></div>
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
        const ct = resp.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
            console.warn(`[VyOmni] ${url} 返回非JSON (${ct}), 跳过`);
            return null;
        }
        const text = await resp.text();
        if (!text || !text.trim()) return null;
        return JSON.parse(text);
    } catch (e) {
        console.warn(`[VyOmni] Fetch ${url}:`, e.message);
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
                <span class="name"><i class="fas fa-globe"></i> ${escHtml(peer.display_name || peer.name)}</span>
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
        const ifaceHtml = br.interfaces ? Object.entries(br.interfaces)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([iface, data]) => {
                const rx = formatRate(data.rx_mbps);
                const tx = formatRate(data.tx_mbps);
                return `<div class="iface-row"><span class="iface-name">${iface}</span><span class="iface-rate">↓${rx} ↑${tx}</span></div>`;
            }).join('') : '';

        return `
        <div class="branch-card ${statusClass}" data-branch='${escAttr(JSON.stringify(br))}'>
            <div class="card-header">
                <span class="name"><i class="fas fa-building"></i> ${escHtml(br.display_name || br.hostname || br.branch_id)}</span>
                <span class="status ${statusClass}">${statusLabel}</span>
            </div>
            <div class="card-metrics">
                <div class="metric"><span class="label">CPU</span><span class="value">${br.cpu_percent?.toFixed(1) || '-'}%</span></div>
                <div class="metric"><span class="label">内存</span><span class="value">${br.memory_percent?.toFixed(1) || '-'}%</span></div>
                <div class="metric"><span class="label">负载 1/5/15</span><span class="value">${(br.load_1m != null ? br.load_1m : '-')} / ${(br.load_5m != null ? br.load_5m : '-')} / ${(br.load_15m != null ? br.load_15m : '-')}</span></div>
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
        try { document.getElementById('alert-sound').play().catch(()=>{}); } catch(e) {}
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
    const centerName = tunnelData.system.hostname || '总部';
    const nodes = [
        {
            name: centerName,
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
            name: peer.display_name || peer.name || `Peer-${i}`,
            x: x, y: y,
            symbolSize: 30 + Math.min(traffic * 2, 20),
            itemStyle: { color: isOnline ? '#66bb6a' : '#ef5350' },
            label: { show: true, fontSize: 11 }
        });

        links.push({
            source: centerName,
            target: peer.display_name || peer.name || `Peer-${i}`,
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
                if (params.dataType === 'node') {
                    // 查找对应 peer 的详细数据
                    const peer = tunnelData.peers.find(p => (p.display_name || p.name) === params.name);
                    if (peer) {
                        return '<b>' + params.name + '</b>' +
                            '<br/>状态: ' + (peer.status === 'online' ? '🟢 在线' : '🔴 离线') +
                            '<br/>Endpoint: ' + (peer.endpoint || '-') +
                            '<br/>握手: ' + peer.last_handshake_seconds_ago + 's ago' +
                            '<br/>↓下行: ' + (peer.rx_rate_mbps || 0) + ' Mbps' +
                            '<br/>↑上行: ' + (peer.tx_rate_mbps || 0) + ' Mbps';
                    }
                    return '<b>' + params.name + '</b><br/>总部节点';
                }
                if (params.dataType === 'edge') {
                    const lineWidth = params.data.lineStyle.width || 1;
                    return params.data.source + ' → ' + params.data.target +
                        '<br/>流量: ' + (lineWidth - 1).toFixed(2) + ' Mbps';
                }
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

    // 内置城市坐标表
    const CITY_COORDS = {
        '常州': [119.97, 31.81], '上海': [121.47, 31.23], '北京': [116.40, 39.90],
        '广州': [113.26, 23.13], '深圳': [114.06, 22.55], '成都': [104.07, 30.57],
        '杭州': [120.15, 30.27], '南京': [118.78, 32.06], '武汉': [114.30, 30.59],
        '西安': [108.94, 34.26], '重庆': [106.55, 29.56], '天津': [117.20, 39.08],
        '苏州': [120.62, 31.30], '无锡': [120.31, 31.49], '合肥': [117.28, 31.82],
        '郑州': [113.65, 34.76], '长沙': [112.94, 28.23], '济南': [117.00, 36.67],
        '青岛': [120.38, 36.07], '大连': [121.62, 38.91], '厦门': [118.09, 24.48],
        '昆明': [102.83, 25.02], '贵阳': [106.63, 26.65], '南宁': [108.37, 22.82],
        '海口': [110.35, 20.02], '哈尔滨': [126.63, 45.75], '长春': [125.32, 43.88],
        '沈阳': [123.43, 41.80], '石家庄': [114.48, 38.03], '太原': [112.55, 37.87],
        '兰州': [103.83, 36.06], '乌鲁木齐': [87.62, 43.83], '拉萨': [91.17, 29.65],
        '呼和浩特': [111.75, 40.84], '银川': [106.23, 38.49], '西宁': [101.78, 36.62],
        'changzhou': [119.97, 31.81], 'shanghai': [121.47, 31.23], 'beijing': [116.40, 39.90],
        'guangzhou': [113.26, 23.13], 'shenzhen': [114.06, 22.55], 'chengdu': [104.07, 30.57],
        'hangzhou': [120.15, 30.27], 'nanjing': [118.78, 32.06], 'wuhan': [114.30, 30.59],
        'xian': [108.94, 34.26], 'chongqing': [106.55, 29.56], 'tianjin': [117.20, 39.08],
        'suzhou': [120.62, 31.30], 'wuxi': [120.31, 31.49], 'hefei': [117.28, 31.82],
    };

    // 根据 IP 地理定位结果获取坐标（服务端自动查询并缓存）
    function resolveCoords(br) {
        // 优先：服务端 IP 地理定位结果
        if (br.geo && br.geo.lat && br.geo.lng) {
            return { coords: [br.geo.lng, br.geo.lat], label: br.geo.city || br.branch_id };
        }

        // 次选：configData.geo_locations 手动配置
        const geoMap = (configData && configData.geo_locations) || {};
        const bid = (br.branch_id || br.hostname || '').toLowerCase();
        const hostname = (br.hostname || '').toLowerCase();
        for (const [key, val] of Object.entries(geoMap)) {
            if (bid.includes(key.toLowerCase()) || hostname.includes(key.toLowerCase())) {
                return { coords: [val.lng, val.lat], label: val.label || key };
            }
        }

        // 次选：内置城市表模糊匹配
        for (const [city, coords] of Object.entries(CITY_COORDS)) {
            if (bid.includes(city) || hostname.includes(city)) {
                return { coords, label: city };
            }
        }

        // 最终 fallback：固定默认位置（北京），绝不随机！
        return { coords: [116.40, 39.90], label: br.hostname || br.branch_id || '未定位' };
    }

    const branches = branchData.branches || [];
    const now = Math.floor(Date.now() / 1000);

    const scatterData = branches.map(br => {
        const resolved = resolveCoords(br);
        const lastSeen = br.last_seen || br.reported_at || 0;
        const isStale = br.stale || (now - lastSeen > BRANCH_STALE_SEC);
        const cpuVal = br.cpu_percent || (br.system && br.system.cpu_percent) || 0;

        return {
            name: br.display_name || resolved.label || br.branch_id,
            value: [...resolved.coords, cpuVal],
            _branch: br,
            itemStyle: { color: isStale ? '#ffa726' : '#66bb6a' },
            symbolSize: isStale ? 14 : 20,
        };
    });

    // 尝试加载中国地图
    if (!updateMap._mapLoaded) {
        updateMap._mapLoaded = 'loading';
        fetch('assets/china.json')
            .then(r => { if (r.ok) return r.json(); throw new Error('no map'); })
            .then(geoJson => {
                echarts.registerMap('china', geoJson);
                updateMap._mapLoaded = 'done';
                updateMap(); // 重新渲染
            })
            .catch(() => {
                updateMap._mapLoaded = 'fallback';
                updateMap(); // 用 fallback 模式渲染
            });
        return;
    }

    if (updateMap._mapLoaded === 'loading') return;

    if (updateMap._mapLoaded === 'done') {
        // 中国地图模式
        chartMap.setOption({
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'item',
                formatter: (p) => {
                    const d = p.data;
                    const br = d._branch;
                    if (!br) return '<b>' + p.name + '</b>';
                    const status = d.itemStyle.color === '#66bb6a' ? '🟢 正常' : '🟠 中断';
                    let html = '<b>' + p.name + '</b>';
                    html += '<br/>状态: ' + status;
                    html += '<br/>CPU: ' + (br.cpu_percent || 0).toFixed(1) + '%';
                    html += '<br/>内存: ' + (br.memory_percent || 0).toFixed(1) + '%';
                    html += '<br/>负载: ' + (br.load_1m != null ? br.load_1m : '-');
                    if (br.geo && br.geo.city) html += '<br/>位置: ' + br.geo.city;
                    if (br.interfaces) {
                        const ifaces = Object.entries(br.interfaces);
                        if (ifaces.length > 0) {
                            html += '<br/>---';
                            ifaces.forEach(([name, data]) => {
                                html += '<br/>' + name + ': ↓' + (data.rx_mbps||0).toFixed(2) + ' ↑' + (data.tx_mbps||0).toFixed(2) + ' Mbps';
                            });
                        }
                    }
                    return html;
                }
            },
            geo: {
                map: 'china',
                roam: true,
                zoom: 1.2,
                itemStyle: {
                    areaColor: 'var(--bg-card, #1e2d3d)',
                    borderColor: 'var(--border-color, #3a4a5a)',
                    borderWidth: 0.5,
                },
                emphasis: {
                    itemStyle: { areaColor: '#2a3a4a' },
                    label: { show: false }
                },
                label: { show: false },
            },
            series: [{
                type: 'effectScatter',
                coordinateSystem: 'geo',
                data: scatterData,
                encode: { value: 2 },
                showEffectOn: 'render',
                rippleEffect: { brushType: 'stroke', scale: 3 },
                label: {
                    show: true,
                    formatter: '{b}',
                    position: 'right',
                    fontSize: 11,
                    color: 'var(--text-primary, #e8eaed)',
                },
            }]
        }, true);
    } else {
        // Fallback：笛卡尔坐标散点图
        chartMap.setOption({
            backgroundColor: 'transparent',
            tooltip: { trigger: 'item', formatter: (p) => `${p.name}<br/>CPU: ${p.value[2]}%` },
            xAxis: { type: 'value', min: 85, max: 135, name: '经度', axisLine: { lineStyle: { color: '#555' } }, splitLine: { lineStyle: { color: '#2a3a4a' } } },
            yAxis: { type: 'value', min: 18, max: 50, name: '纬度', axisLine: { lineStyle: { color: '#555' } }, splitLine: { lineStyle: { color: '#2a3a4a' } } },
            series: [{
                type: 'effectScatter',
                data: scatterData.map(d => ({ name: d.name, value: d.value, itemStyle: d.itemStyle, symbolSize: d.symbolSize })),
                rippleEffect: { brushType: 'stroke', scale: 3 },
                label: { show: true, formatter: '{b}', position: 'right', fontSize: 11, color: '#e8eaed' },
            }]
        }, true);
    }
}
updateMap._mapLoaded = false;


// ==================== 工具函数 ====================

// 自适应速率格式化
function formatRate(mbps) {
    if (mbps === undefined || mbps === null) return '0';
    if (mbps === 0) return '0';
    if (mbps < 0.01) return '<0.01';
    if (mbps >= 1) return mbps.toFixed(1);
    return mbps.toFixed(2);
}
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
let tokensData = null;
let nodesPollTimer = null;
let tokenCountdownTimer = null;

const NODE_API = {
    list: '/api/nodes',
    approve: (id) => `/api/nodes/${id}/approve`,
    reject: (id) => `/api/nodes/${id}/reject`,
    delete: (id) => `/api/nodes/${id}`,
    config: (id) => `/api/nodes/${id}/config`,
    tokens: '/api/tokens',
    generateToken: '/api/tokens/generate',
};

// 初始化节点管理（在 DOMContentLoaded 中追加调用）
function initNodeManagement() {
    const navBtn = document.getElementById('nav-nodes');
    if (navBtn) {
        navBtn.onclick = function(e) {
            e.preventDefault();
            openNodeManagementModal();
        };
    }
}

function getNodePanelHtml() {
    return `
    <div class="node-mgmt-panel">
        <div class="node-panel-header">
            <h2><i class="fas fa-server"></i> 节点管理</h2>
            <div class="node-panel-actions">
                <button class="btn btn-sm btn-primary" id="btn-add-node"><i class="fas fa-plus"></i> 新增节点</button>
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

        <!-- Token 列表 -->
        <div class="token-section" id="token-section">
            <h3><i class="fas fa-key"></i> 部署 Token</h3>
            <div class="token-list" id="token-list"></div>
        </div>

        <div class="node-table-wrap">
            <table class="node-table" id="node-table">
                <thead>
                    <tr>
                        <th>主机名</th>
                        <th>角色</th>
                        <th>IP</th>
                        <th>版本</th>
                        <th>状态</th>
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
    if (refreshBtn) refreshBtn.addEventListener('click', () => { fetchNodes(); fetchTokens(); });

    const addBtn = document.getElementById('btn-add-node');
    if (addBtn) addBtn.addEventListener('click', showAddNodeModal);

    const filterStatus = document.getElementById('node-filter-status');
    if (filterStatus) filterStatus.addEventListener('change', renderNodeTable);

    const filterRole = document.getElementById('node-filter-role');
    if (filterRole) filterRole.addEventListener('change', renderNodeTable);
}

function openNodeManagementModal() {
    const html = getNodePanelHtml();
    openModal('节点管理', html);
    // 延迟绑定事件（等 modal DOM 渲染完）
    setTimeout(() => {
        bindNodePanelEvents();
        fetchNodes();
        fetchTokens();
    }, 100);
}

function showNodePanel() {
    openNodeManagementModal();
}

async function showAddNodeModal() {
    const formHtml = `
    <div class="add-node-form">
        <div class="form-header">
            <i class="fas fa-plus-circle form-header-icon"></i>
            <div>
                <h4>新增节点 — 生成部署 Token</h4>
                <p class="form-desc">生成一次性 Token，在目标节点执行命令即可自动注册</p>
            </div>
        </div>
        <div class="form-body">
            <div class="form-group">
                <label><i class="fas fa-tag"></i> 节点名称 <span class="required">*</span></label>
                <input type="text" id="add-node-name" placeholder="例：上海分部、深圳机房" autocomplete="off" />
            </div>
            <div class="form-group">
                <label><i class="fas fa-user-tag"></i> 角色</label>
                <select id="add-node-role">
                    <option value="branch">分支 (Branch) — 资源采集上报</option>
                    <option value="hq">总部 (HQ) — WireGuard 隧道 + 资源采集</option>
                </select>
            </div>
            <div class="form-actions">
                <button class="btn btn-primary btn-lg" id="btn-generate-token">
                    <i class="fas fa-key"></i> 生成 Token
                </button>
                <button class="btn btn-lg" onclick="closeModal()">
                    <i class="fas fa-times"></i> 取消
                </button>
            </div>
        </div>
        <div id="token-result"></div>
    </div>`;

    openModal('新增节点', formHtml);

    // 延迟绑定确保 DOM 渲染完成
    setTimeout(() => {
        const btn = document.getElementById('btn-generate-token');
        if (!btn) { console.error('btn-generate-token not found after modal open'); return; }

        btn.onclick = async function() {
            const nameEl = document.getElementById('add-node-name');
            const roleEl = document.getElementById('add-node-role');
            if (!nameEl || !roleEl) return;

            const name = nameEl.value.trim();
            const role = roleEl.value;
            if (!name) { nameEl.focus(); nameEl.style.borderColor = 'var(--accent-red)'; return; }

            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 生成中...';

            try {
                const controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), 15000);

                const resp = await fetch('/api/tokens/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, role }),
                    signal: controller.signal,
                });
                clearTimeout(timeout);

                let data;
                const ct = resp.headers.get('content-type') || '';
                if (ct.includes('application/json')) {
                    data = await resp.json();
                } else {
                    const text = await resp.text();
                    throw new Error('服务器返回非JSON: ' + text.substring(0, 100));
                }

                if (!resp.ok) {
                    throw new Error(data.error || '生成失败 (HTTP ' + resp.status + ')');
                }

                // 成功 - 显示结果
                showTokenResult(data);
            } catch (e) {
                const msg = e.name === 'AbortError' ? '请求超时(15s)，请检查服务器连接' : e.message;
                const resultEl = document.getElementById('token-result');
                if (resultEl) {
                    resultEl.innerHTML = '<div class="token-error"><i class="fas fa-exclamation-triangle"></i> ' + msg + '</div>';
                } else {
                    alert('生成失败: ' + msg);
                }
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-key"></i> 生成 Token';
            }
        };
    }, 50);
}

function showTokenResult(data) {
    const resultEl = document.getElementById('token-result');
    if (!resultEl) { console.error('token-result element not found'); return; }

    const expiresIn = (data.expires_at || 0) - Math.floor(Date.now() / 1000);
    const hours = Math.max(0, Math.floor(expiresIn / 3600));
    const minutes = Math.max(0, Math.floor((expiresIn % 3600) / 60));

    const deployCmd = data.deploy_cmd || 'curl -sL http://' + location.hostname + ':9100/api/deploy/' + data.token + ' | bash';
    const configContent = data.config_content || 'server_url = http://' + location.hostname + ':9100\nregister_token = ' + data.token;

    resultEl.innerHTML = `
    <div class="token-success-card">
        <div class="token-success-header">
            <i class="fas fa-check-circle"></i>
            <span>Token 生成成功！有效期 ${hours}h${minutes}m</span>
        </div>

        <div class="token-section">
            <label><i class="fas fa-terminal"></i> 一键部署命令 <small>（在目标节点以 root 执行）</small></label>
            <div class="copy-box">
                <code id="deploy-cmd-text">${deployCmd}</code>
                <button class="btn btn-xs btn-copy" onclick="copyToClipboard('deploy-cmd-text')">
                    <i class="fas fa-copy"></i> 复制
                </button>
            </div>
        </div>

        <div class="token-section">
            <label><i class="fas fa-file-alt"></i> 手动配置 (config.conf)</label>
            <div class="copy-box">
                <pre id="config-content-text">${configContent}</pre>
                <button class="btn btn-xs btn-copy" onclick="copyToClipboard('config-content-text')">
                    <i class="fas fa-copy"></i> 复制
                </button>
            </div>
        </div>

        <div class="token-meta">
            <span><i class="fas fa-clock"></i> 有效期: ${hours}小时${minutes}分钟</span>
            <span><i class="fas fa-key"></i> Token: <code>${data.token || '?'}</code></span>
        </div>
    </div>`;
}

function startTokenCountdown(expiresAt) {
    if (tokenCountdownTimer) clearInterval(tokenCountdownTimer);
    const el = document.getElementById('token-countdown');
    if (!el) return;

    tokenCountdownTimer = setInterval(() => {
        const remaining = expiresAt - Math.floor(Date.now() / 1000);
        if (remaining <= 0) {
            el.innerHTML = '<i class="fas fa-times-circle" style="color:#ef5350;"></i> 已过期';
            clearInterval(tokenCountdownTimer);
            return;
        }
        const h = Math.floor(remaining / 3600);
        const m = Math.floor((remaining % 3600) / 60);
        const s = remaining % 60;
        el.innerHTML = `<i class="fas fa-clock"></i> 剩余: ${h}h ${m}m ${s}s`;
    }, 1000);
}

function copyToClipboard(elementId) {
    const el = document.getElementById(elementId);
    if (!el) { console.warn('copyToClipboard: element not found:', elementId); return; }
    const text = (el.textContent || el.innerText || '').trim();
    if (!text) { console.warn('copyToClipboard: empty text'); return; }

    function onSuccess() {
        const btn = el.closest('.copy-box')?.querySelector('.btn-copy') || el.parentElement?.querySelector('.btn-copy');
        if (btn) {
            const orig = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-check"></i> 已复制';
            btn.classList.add('copied');
            setTimeout(() => { btn.innerHTML = orig; btn.classList.remove('copied'); }, 2000);
        }
    }

    // 方案1: Clipboard API (requires HTTPS or localhost)
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(onSuccess).catch(() => fallbackCopy(text, onSuccess));
    } else {
        fallbackCopy(text, onSuccess);
    }
}

function fallbackCopy(text, onSuccess) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0;';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
        const ok = document.execCommand('copy');
        if (ok && onSuccess) onSuccess();
    } catch(e) { console.warn('execCommand copy failed:', e); }
    document.body.removeChild(ta);
}

// ==================== Token 列表 ====================
async function fetchTokens() {
    const data = await fetchJSON(NODE_API.tokens);
    if (data && data.tokens) {
        tokensData = data.tokens;
        renderTokenList();
    }
}

function renderTokenList() {
    const el = document.getElementById('token-list');
    if (!el || !tokensData) return;

    if (tokensData.length === 0) {
        el.innerHTML = '<p class="no-tokens">暂无 Token，点击"新增节点"生成</p>';
        return;
    }

    const now = Math.floor(Date.now() / 1000);
    const sorted = [...tokensData].sort((a, b) => b.created_at - a.created_at);

    el.innerHTML = `
    <table class="token-table">
        <thead>
            <tr>
                <th>Token</th>
                <th>名称</th>
                <th>角色</th>
                <th>状态</th>
                <th>有效期</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            ${sorted.map(tk => {
                let statusBadge = '';
                let canDelete = false;
                const remaining = tk.expires_at - now;

                if (tk.status === 'unused' && remaining > 0) {
                    statusBadge = '<span class="status-badge pending"><i class="fas fa-clock"></i> 待使用</span>';
                    canDelete = true;
                } else if (tk.status === 'used') {
                    statusBadge = '<span class="status-badge approved"><i class="fas fa-check-circle"></i> 已使用</span>';
                    canDelete = true;
                } else {
                    statusBadge = '<span class="status-badge expired"><i class="fas fa-times-circle"></i> 已过期</span>';
                    canDelete = true;
                }

                const roleBadge = tk.role === 'hq'
                    ? '<span class="role-badge hq">HQ</span>'
                    : '<span class="role-badge branch">Branch</span>';

                let expiryText = '';
                if (tk.status === 'used') {
                    expiryText = '已消费';
                } else if (remaining <= 0) {
                    expiryText = '已过期';
                } else {
                    const h = Math.floor(remaining / 3600);
                    const m = Math.floor((remaining % 3600) / 60);
                    expiryText = h > 0 ? h + 'h ' + m + 'm' : m + 'min';
                }

                const actionBtn = canDelete
                    ? '<button class="btn btn-xs btn-danger" onclick="deleteToken(\'' + tk.token + '\')"><i class="fas fa-trash"></i></button>'
                    : '<span class="text-muted">—</span>';

                return '<tr class="' + (tk.status === 'used' ? 'row-used' : remaining <= 0 ? 'row-expired' : '') + '">' +
                    '<td><code>' + tk.token + '</code></td>' +
                    '<td>' + escHtml(tk.name) + '</td>' +
                    '<td>' + roleBadge + '</td>' +
                    '<td>' + statusBadge + '</td>' +
                    '<td>' + expiryText + '</td>' +
                    '<td>' + actionBtn + '</td>' +
                    '</tr>';
            }).join('')}
        </tbody>
    </table>`;
}

async function fetchNodes() {
    const data = await fetchJSON(NODE_API.list);
    if (data && data.nodes) {
        nodesData = data.nodes;
        renderNodeStats();
        renderNodeTable();
    } else {
        // API 不可达时显示提示
        const tbody = document.getElementById('node-table-body');
        if (tbody) tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-secondary);padding:20px;">暂无节点数据（等待 Agent 注册）</td></tr>';
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
        <div class="node-stat-card stat-total"><div class="stat-value">${total}</div><div class="stat-label">总节点</div></div>
        <div class="node-stat-card stat-pending"><div class="stat-value">${pending}</div><div class="stat-label">待审核</div></div>
        <div class="node-stat-card stat-online"><div class="stat-value">${approved}</div><div class="stat-label">已通过</div></div>
        <div class="node-stat-card stat-online"><div class="stat-value">${online}</div><div class="stat-label"><i class="fas fa-circle" style="color:var(--accent-green);font-size:8px"></i> 在线</div></div>
        <div class="node-stat-card stat-offline"><div class="stat-value">${rejected}</div><div class="stat-label">已拒绝</div></div>
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
        const now2 = Math.floor(Date.now() / 1000);
        const isOnline = node.status === 'approved' && now2 - node.last_seen < 60;
        const statusBadge = getNodeStatusBadge(node.status);
        // role 判断用 toLowerCase 兼容大小写
        const role = (node.role || '').toLowerCase();
        const roleBadge = role === 'hq'
            ? '<span class="role-badge hq">HQ</span>'
            : '<span class="role-badge branch">Branch</span>';
        const lastSeen = node.last_seen ? formatTimeAgo(now2 - node.last_seen) : '从未';
        const regTime = node.registered_at ? formatShortDate(node.registered_at) : '-';

        let actions = '';
        if (node.status === 'pending') {
            actions += '<button class="btn btn-xs btn-approve" onclick="nodeAction(\'approve\',\'' + escAttr(node.node_id) + '\')"><i class="fas fa-check"></i></button> ';
            actions += '<button class="btn btn-xs btn-reject" onclick="nodeAction(\'reject\',\'' + escAttr(node.node_id) + '\')"><i class="fas fa-times"></i></button> ';
        } else if (node.status === 'approved') {
            actions += '<button class="btn btn-xs btn-config" onclick="showNodeConfig(\'' + escAttr(node.node_id) + '\')"><i class="fas fa-cog"></i></button> ';
            actions += '<button class="btn btn-xs" style="background:var(--accent-blue);color:#fff;border:none" onclick="renameNode(\'' + escAttr(node.node_id) + '\',\'' + escAttr(node.display_name || node.hostname || '') + '\')"><i class="fas fa-pen"></i></button> ';
        }
        actions += '<button class="btn btn-xs btn-delete" onclick="nodeAction(\'delete\',\'' + escAttr(node.node_id) + '\')"><i class="fas fa-trash"></i></button>';

        return '<tr>' +
            '<td class="td-hostname" title="' + escAttr(node.node_id) + '">' + escHtml(node.display_name || node.hostname || node.node_id) + '</td>' +
            '<td>' + roleBadge + '</td>' +
            '<td class="td-ip">' + escHtml(node.ip || '-') + '</td>' +
            '<td>' + escHtml(node.version || '-') + '</td>' +
            '<td>' + statusBadge + '</td>' +
            '<td>' + lastSeen + '</td>' +
            '<td class="td-date">' + regTime + '</td>' +
            '<td class="td-actions">' + actions + '</td>' +
            '</tr>';
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
    setTimeout(() => {
        initNodeManagement();
    }, 100);
})();

async function deleteToken(tokenId) {
    if (!confirm('确认删除此 Token？')) return;
    try {
        const resp = await fetch('/api/tokens/' + tokenId, { method: 'DELETE' });
        if (resp.ok) {
            fetchTokens();
        } else {
            const ct = resp.headers.get('content-type') || '';
            if (ct.includes('json')) {
                const err = await resp.json();
                alert('删除失败: ' + (err.error || '未知错误'));
            } else {
                alert('删除失败: HTTP ' + resp.status);
            }
        }
    } catch (e) {
        alert('请求失败: ' + e.message);
    }
}

function formatShortDate(unixTs) {
    if (!unixTs) return '-';
    const d = new Date(unixTs * 1000);
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const min = String(d.getMinutes()).padStart(2, '0');
    return mm + '-' + dd + ' ' + hh + ':' + min;
}

async function renameNode(nodeId, currentName) {
    const newName = prompt('设置节点显示名称:', currentName);
    if (newName === null || newName.trim() === '') return;
    try {
        const resp = await fetch('/api/nodes/' + nodeId + '/rename', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({display_name: newName.trim()}),
        });
        if (resp.ok) {
            fetchNodes();
        } else {
            const err = await resp.json().catch(() => ({}));
            alert('重命名失败: ' + (err.error || 'HTTP ' + resp.status));
        }
    } catch(e) {
        alert('请求失败: ' + e.message);
    }
}
