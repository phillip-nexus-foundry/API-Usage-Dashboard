// API Usage Dashboard - Vanilla JS

const COLORS_PALETTE = [
    '#58a6ff','#3fb950','#f85149','#d29922','#bc8ef6','#39d2c0','#f0e443','#ff6b9d',
];

// Stable provider-to-color map — fully dynamic and persisted in localStorage
let providerColorMap = {};
try {
    providerColorMap = JSON.parse(localStorage.getItem('dash_providerColorMap') || '{}') || {};
} catch {
    providerColorMap = {};
}
function getProviderColor(provider) {
    if (!providerColorMap[provider]) {
        // Assign next unused palette color
        const usedColors = new Set(Object.values(providerColorMap));
        const available = COLORS_PALETTE.filter(c => !usedColors.has(c));
        providerColorMap[provider] = available.length > 0 ? available[0] : COLORS_PALETTE[Object.keys(providerColorMap).length % COLORS_PALETTE.length];
        try { localStorage.setItem('dash_providerColorMap', JSON.stringify(providerColorMap)); } catch {}
    }
    return providerColorMap[provider];
}

let allData = { summary:null, timeseries:null, calls:null, models:null, tools:null, balance:null, resources:null, evals:null, costDaily:null, costProjection:null, ratelimits:null, spendlimits:null };
let unfilteredMeta = { providers:[], models:[] };
let chartInstances = {};

// ============================================================================
// STATE PERSISTENCE — all UI state saved to localStorage
// ============================================================================
function loadState(key, fallback) {
    try { const v = localStorage.getItem('dash_'+key); return v !== null ? JSON.parse(v) : fallback; }
    catch { return fallback; }
}
function saveState(key, value) { localStorage.setItem('dash_'+key, JSON.stringify(value)); }

let currentSort = loadState('sort', { field:'timestamp', order:'desc' });
let currentPage = loadState('page', 1);
let callsPerPage = loadState('perPage', 50);
let useUTC = loadState('useUTC', false);
let hiddenProviders = loadState('hiddenProviders', []);
let selectedProject = loadState('selectedProject', {}); // { provider: '__all__' }

// Saved filter/range values (applied after DOM ready)
const savedFilters = {
    provider: loadState('filterProvider', ''),
    model: loadState('filterModel', ''),
    interval: loadState('filterInterval', 'hour'),
    balanceSort: loadState('balanceSort', 'usage-desc'),
    kpiRange: loadState('kpiRange', 'month'),
    kpiRolling: loadState('kpiRolling', false),
    minTokens: loadState('filterMinTokens', ''),
    maxTokens: loadState('filterMaxTokens', ''),
    minCost: loadState('filterMinCost', ''),
    maxCost: loadState('filterMaxCost', ''),
};

// Time series range state — tracks the visible window per chart
// endMs = right edge (defaults to now), windowMs = width of visible range
const INTERVAL_RANGES = {
    minute: { min: 3600000, max: 4*3600000, default: 2*3600000, step: 3600000 },        // 1-4 hrs, default 2hrs, step 1hr
    hour:   { min: 86400000, max: 30*86400000, default: 3*86400000, step: 86400000 },    // 1-30 days, default 3 days, step 1 day
    day:    { min: 7*86400000, max: 90*86400000, default: 30*86400000, step: 7*86400000 },// 7-90 days, default 30 days, step 7 days
    week:   { min: 4*7*86400000, max: 24*7*86400000, default: 8*7*86400000, step: 2*7*86400000 }, // 4-24 wks, default 8 wks, step 2 wks
    month:  { min: 3*30*86400000, max: 12*30*86400000, default: 6*30*86400000, step: 30*86400000 }, // 3-12 mo, default 6 mo, step 1 mo
};
let tsEndMs = loadState('tsEndMs', Date.now());
let tsWindowMs = loadState('tsWindowMs', null);

// If saved tsEndMs is stale (> 1 hour old), reset to now
if (Date.now() - tsEndMs > 3600000) tsEndMs = Date.now();

document.addEventListener('DOMContentLoaded', () => {
    restoreUIState();
    setupEventListeners();
    loadData();
    setInterval(loadData, 10000);
});

function restoreUIState() {
    document.getElementById('filterProvider').value = savedFilters.provider;
    document.getElementById('filterModel').value = savedFilters.model;
    document.getElementById('filterInterval').value = savedFilters.interval;
    document.getElementById('balanceSortSelect').value = savedFilters.balanceSort;
    document.getElementById('perPageSelect').value = callsPerPage;
    document.getElementById('utcToggle').checked = useUTC;
    document.getElementById('filterMinTokens').value = savedFilters.minTokens;
    document.getElementById('filterMaxTokens').value = savedFilters.maxTokens;
    document.getElementById('filterMinCost').value = savedFilters.minCost;
    document.getElementById('filterMaxCost').value = savedFilters.maxCost;
    document.getElementById('kpiRangeSelect').value = savedFilters.kpiRange;
    document.getElementById('kpiRollingToggle').checked = savedFilters.kpiRolling;
}

function getKpiRangeParams() {
    const range = document.getElementById('kpiRangeSelect').value;
    if (range === 'all') return '';
    const rolling = document.getElementById('kpiRollingToggle').checked;
    const now = new Date();
    let start;
    if (rolling) {
        // Rolling: last 24h, 7d, 30d, 365d from now
        if (range === 'today') start = new Date(now.getTime() - 24*3600000);
        else if (range === 'week') start = new Date(now.getTime() - 7*86400000);
        else if (range === 'month') start = new Date(now.getTime() - 30*86400000);
        else if (range === 'year') start = new Date(now.getTime() - 365*86400000);
        else return '';
    } else {
        // Calendar: since start of today/week/month/year
        if (range === 'today') {
            start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        } else if (range === 'week') {
            start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            const day = start.getDay();
            const diff = day === 0 ? 6 : day - 1;
            start.setDate(start.getDate() - diff);
        } else if (range === 'month') {
            start = new Date(now.getFullYear(), now.getMonth(), 1);
        } else if (range === 'year') {
            start = new Date(now.getFullYear(), 0, 1);
        } else {
            return '';
        }
    }
    return `&start=${start.getTime()}&end=${now.getTime()}`;
}

function getContainingKpiRangeInfo() {
    const range = document.getElementById('kpiRangeSelect').value;
    const rolling = document.getElementById('kpiRollingToggle').checked;
    const now = new Date();
    let start = null;
    let meta = '';

    if (rolling) {
        // Rolling: compare to rolling 7d, 30d, 365d windows
        if (range === 'today') {
            start = new Date(now.getTime() - 7*86400000);
            meta = 'Today of Week';
        } else if (range === 'week') {
            start = new Date(now.getTime() - 30*86400000);
            meta = 'Week of Month';
        } else if (range === 'month') {
            start = new Date(now.getTime() - 365*86400000);
            meta = 'Month of Year';
        } else {
            return { supported:false, params:'', meta:'—' };
        }
    } else {
        // Calendar: compare to calendar week (Sun-Sat), month, year
        if (range === 'today') {
            // Sunday-starting week
            start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            start.setDate(start.getDate() - start.getDay());
            meta = 'Today of Week';
        } else if (range === 'week') {
            start = new Date(now.getFullYear(), now.getMonth(), 1);
            meta = 'Week of Month';
        } else if (range === 'month') {
            start = new Date(now.getFullYear(), 0, 1);
            meta = 'Month of Year';
        } else {
            return { supported:false, params:'', meta:'—' };
        }
    }

    return {
        supported: true,
        params: `&start=${start.getTime()}&end=${now.getTime()}`,
        meta,
    };
}

function getKpiRangeLabel() {
    const range = document.getElementById('kpiRangeSelect').value;
    const rolling = document.getElementById('kpiRollingToggle').checked;
    if (rolling) {
        const labels = { all:'all time', today:'last 24h', week:'last 7 days', month:'last 30 days', year:'last 365 days' };
        return labels[range] || 'all time';
    }
    const labels = { all:'all time', today:'today', week:'this week', month:'this month', year:'this year' };
    return labels[range] || 'all time';
}

function setupEventListeners() {
    document.getElementById('refreshBtn').addEventListener('click', () => {
        const btn = document.getElementById('refreshBtn');
        btn.disabled = true; btn.textContent = 'Refreshing...';
        fetch('/api/refresh', {method:'POST'}).then(() => loadData()).finally(() => {
            btn.disabled = false; btn.textContent = 'Refresh';
        });
    });
    document.getElementById('utcToggle').addEventListener('change', e => {
        useUTC = e.target.checked;
        saveState('useUTC', useUTC);
        renderDashboard();
    });
    document.getElementById('kpiRangeSelect').addEventListener('change', () => {
        saveState('kpiRange', document.getElementById('kpiRangeSelect').value);
        loadFilteredData();
    });
    document.getElementById('kpiRollingToggle').addEventListener('change', () => {
        saveState('kpiRolling', document.getElementById('kpiRollingToggle').checked);
        loadFilteredData();
    });
    document.getElementById('filterProvider').addEventListener('change', () => {
        saveState('filterProvider', document.getElementById('filterProvider').value);
        currentPage = 1; saveState('page', 1);
        loadFilteredData();
    });
    document.getElementById('filterModel').addEventListener('change', () => {
        saveState('filterModel', document.getElementById('filterModel').value);
        currentPage = 1; saveState('page', 1);
        loadFilteredData();
    });
    document.getElementById('balanceSortSelect').addEventListener('change', () => {
        saveState('balanceSort', document.getElementById('balanceSortSelect').value);
        renderBalance();
    });
    document.getElementById('perPageSelect').addEventListener('change', e => {
        callsPerPage = parseInt(e.target.value);
        saveState('perPage', callsPerPage);
        currentPage = 1; saveState('page', 1);
        loadFilteredData();
    });
    document.getElementById('applyRangeFilter').addEventListener('click', () => {
        saveState('filterMinTokens', document.getElementById('filterMinTokens').value);
        saveState('filterMaxTokens', document.getElementById('filterMaxTokens').value);
        saveState('filterMinCost', document.getElementById('filterMinCost').value);
        saveState('filterMaxCost', document.getElementById('filterMaxCost').value);
        currentPage = 1; saveState('page', 1);
        loadFilteredData();
    });
    // Time series navigation
    document.getElementById('tsPrevTokens').addEventListener('click', () => tsNavigate(-1));
    document.getElementById('tsNextTokens').addEventListener('click', () => tsNavigate(1));
    document.getElementById('tsPrevCost').addEventListener('click', () => tsNavigate(-1));
    document.getElementById('tsNextCost').addEventListener('click', () => tsNavigate(1));
    document.getElementById('tsZoomOutTokens').addEventListener('click', () => tsZoom(1));
    document.getElementById('tsZoomInTokens').addEventListener('click', () => tsZoom(-1));
    document.getElementById('tsZoomOutCost').addEventListener('click', () => tsZoom(1));
    document.getElementById('tsZoomInCost').addEventListener('click', () => tsZoom(-1));
    document.getElementById('filterInterval').addEventListener('change', () => {
        saveState('filterInterval', document.getElementById('filterInterval').value);
        tsWindowMs = null; tsEndMs = Date.now();
        saveTsState();
        loadFilteredData();
    });
    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const field = th.dataset.field;
            if (currentSort.field === field) currentSort.order = currentSort.order === 'asc' ? 'desc' : 'asc';
            else { currentSort.field = field; currentSort.order = 'desc'; }
            saveState('sort', currentSort);
            renderCallLog();
        });
    });
    document.getElementById('pollResourcesBtn').addEventListener('click', async () => {
        const btn = document.getElementById('pollResourcesBtn');
        btn.disabled = true;
        btn.textContent = 'Polling...';
        try {
            const resp = await fetch('/api/resources/poll', { method: 'POST' });
            const data = await resp.json();
            if (data.error) {
                showToast(data.error, 'error');
            } else {
                allData.resources = { providers: data.providers || {} };
                renderResources();
                showToast('Resource poll complete', 'success');
            }
        } catch (e) {
            showToast('Poll failed: ' + e.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Poll Now';
        }
    });
    setupRateLimitListeners();
    setupSpendLimitListeners();
}

function saveTsState() {
    saveState('tsEndMs', tsEndMs);
    saveState('tsWindowMs', tsWindowMs);
}

function getFilterParams() {
    const provider = document.getElementById('filterProvider').value;
    const model = document.getElementById('filterModel').value;
    let qs = '';
    if (provider) qs += `&provider=${encodeURIComponent(provider)}`;
    if (model) qs += `&model=${encodeURIComponent(model)}`;
    return qs;
}

function getRangeParams() {
    let qs = '';
    const minT = document.getElementById('filterMinTokens').value;
    const maxT = document.getElementById('filterMaxTokens').value;
    const minC = document.getElementById('filterMinCost').value;
    const maxC = document.getElementById('filterMaxCost').value;
    if (minT) qs += `&min_tokens=${minT}`;
    if (maxT) qs += `&max_tokens=${maxT}`;
    if (minC) qs += `&min_cost=${minC}`;
    if (maxC) qs += `&max_cost=${maxC}`;
    return qs;
}

function getEarliestTimestamp() {
    if (absoluteEarliest > 0) return absoluteEarliest;
    if (allData.summary && allData.summary.earliest_timestamp) return allData.summary.earliest_timestamp;
    return 0;
}

function getTsRangeParams() {
    const interval = document.getElementById('filterInterval').value;
    const range = INTERVAL_RANGES[interval] || INTERVAL_RANGES.hour;
    if (tsWindowMs === null) tsWindowMs = range.default;
    // Clamp window to min/max of interval
    tsWindowMs = Math.max(range.min, Math.min(range.max, tsWindowMs));
    let startMs = tsEndMs - tsWindowMs;
    // Don't go earlier than the first data point
    const earliest = getEarliestTimestamp();
    if (earliest > 0 && startMs < earliest) {
        startMs = earliest;
    }
    return `&start=${Math.floor(startMs)}&end=${Math.floor(tsEndMs)}`;
}

function tsNavigate(direction) {
    const interval = document.getElementById('filterInterval').value;
    const range = INTERVAL_RANGES[interval] || INTERVAL_RANGES.hour;
    if (tsWindowMs === null) tsWindowMs = range.default;
    // Shift by step amount
    tsEndMs += direction * range.step;
    // Don't go past now
    if (tsEndMs > Date.now()) tsEndMs = Date.now();
    // Don't go before earliest data
    const earliest = getEarliestTimestamp();
    if (earliest > 0 && (tsEndMs - tsWindowMs) < earliest) {
        tsEndMs = earliest + tsWindowMs;
    }
    saveTsState();
    loadFilteredData();
}

function tsZoom(direction) {
    // direction: +1 = widen (zoom out), -1 = narrow (zoom in)
    const interval = document.getElementById('filterInterval').value;
    const range = INTERVAL_RANGES[interval] || INTERVAL_RANGES.hour;
    if (tsWindowMs === null) tsWindowMs = range.default;
    tsWindowMs += direction * range.step;
    tsWindowMs = Math.max(range.min, Math.min(range.max, tsWindowMs));
    // Don't widen beyond data range
    const earliest = getEarliestTimestamp();
    if (earliest > 0) {
        const maxWindow = tsEndMs - earliest;
        if (tsWindowMs > maxWindow && maxWindow > range.min) {
            tsWindowMs = maxWindow;
        }
    }
    saveTsState();
    loadFilteredData();
}

function updateTsRangeLabels() {
    const interval = document.getElementById('filterInterval').value;
    const range = INTERVAL_RANGES[interval] || INTERVAL_RANGES.hour;
    if (tsWindowMs === null) tsWindowMs = range.default;
    const startMs = tsEndMs - tsWindowMs;
    const startD = new Date(startMs);
    const endD = new Date(tsEndMs);
    const fmt = (d) => {
        if (interval === 'minute' || interval === 'hour') {
            return d.toLocaleString('en-US', {month:'short',day:'numeric',hour:'numeric',hour12:true});
        }
        return d.toLocaleString('en-US', {month:'short',day:'numeric',year:'2-digit'});
    };
    const label = `${fmt(startD)} — ${fmt(endD)}`;
    document.getElementById('tsRangeLabelTokens').textContent = label;
    document.getElementById('tsRangeLabelCost').textContent = label;
    // Disable "next" if we're at the present
    const atPresent = tsEndMs >= Date.now() - 60000;
    document.getElementById('tsNextTokens').disabled = atPresent;
    document.getElementById('tsNextCost').disabled = atPresent;
    // Disable "prev" if we're at the earliest data
    const earliest = getEarliestTimestamp();
    const atEarliest = earliest > 0 && startMs <= earliest;
    document.getElementById('tsPrevTokens').disabled = atEarliest;
    document.getElementById('tsPrevCost').disabled = atEarliest;
    // Disable zoom-out if window already covers all data
    const atMaxZoom = earliest > 0 && tsWindowMs >= (tsEndMs - earliest);
    document.getElementById('tsZoomOutTokens').disabled = atMaxZoom;
    document.getElementById('tsZoomOutCost').disabled = atMaxZoom;
}

// Store the absolute earliest timestamp (unfiltered) for time series navigation
let absoluteEarliest = 0;

async function loadData() {
    try {
        const interval = document.getElementById('filterInterval').value;
        const tsRange = getTsRangeParams();
        const kpiRange = getKpiRangeParams();
        const [summary,unfilteredSummary,timeseries,calls,models,tools,balance,resources,evals,costDaily,costProjection,ratelimits,spendlimits] = await Promise.all([
            fetch(`/api/summary?_=1${kpiRange}`).then(r=>r.json()),
            fetch('/api/summary?_=1').then(r=>r.json()),  // unfiltered for earliest timestamp
            fetch(`/api/timeseries?interval=${interval}${tsRange}`).then(r=>r.json()),
            fetch(`/api/calls?page=${currentPage}&per_page=${callsPerPage}${kpiRange}`).then(r=>r.json()),
            fetch(`/api/models?_=1${kpiRange}`).then(r=>r.json()),
            fetch(`/api/tools?_=1${kpiRange}`).then(r=>r.json()),
            fetch('/api/balance').then(r=>r.json()),
            fetch('/api/resources').then(r=>r.json()),
            fetch('/api/evals').then(r=>r.json()),
            fetch(`/api/cost/daily?_=1${kpiRange}`).then(r=>r.json()),
            fetch(`/api/cost/projection?_=1${kpiRange}`).then(r=>r.json()),
            fetch('/api/ratelimits').then(r=>r.json()),
            fetch('/api/spendlimits').then(r=>r.json()),
        ]);
        allData = {summary,timeseries,calls,models,tools,balance,resources,evals,costDaily,costProjection,ratelimits,spendlimits};
        absoluteEarliest = unfilteredSummary.earliest_timestamp || 0;
        unfilteredMeta.providers = (unfilteredSummary.by_provider||[]).map(p=>p.provider);
        unfilteredMeta.models = (unfilteredSummary.by_model||[]).map(m=>m.model);
        // Build stable color map from providers
        unfilteredMeta.providers.forEach(p => getProviderColor(p));
        await renderDashboard();
    } catch(e) { showToast('Load failed: '+e.message,'error'); }
}

async function loadFilteredData() {
    try {
        const fq = getFilterParams();
        const rq = getRangeParams();
        const interval = document.getElementById('filterInterval').value;
        const tsRange = getTsRangeParams();
        const kpiRange = getKpiRangeParams();
        const [summary,timeseries,calls,models,tools,costDaily,costProjection,ratelimits] = await Promise.all([
            fetch(`/api/summary?_=1${fq}${kpiRange}`).then(r=>r.json()),
            fetch(`/api/timeseries?interval=${interval}${fq}${tsRange}`).then(r=>r.json()),
            fetch(`/api/calls?page=${currentPage}&per_page=${callsPerPage}${fq}${rq}${kpiRange}`).then(r=>r.json()),
            fetch(`/api/models?_=1${fq}${kpiRange}`).then(r=>r.json()),
            fetch(`/api/tools?_=1${fq}${kpiRange}`).then(r=>r.json()),
            fetch(`/api/cost/daily?_=1${fq}${kpiRange}`).then(r=>r.json()),
            fetch(`/api/cost/projection?_=1${fq}${kpiRange}`).then(r=>r.json()),
            fetch('/api/ratelimits').then(r=>r.json()),
        ]);
        allData.summary=summary; allData.timeseries=timeseries; allData.calls=calls;
        allData.models=models; allData.tools=tools; allData.costDaily=costDaily; allData.costProjection=costProjection;
        allData.ratelimits=ratelimits;
        await renderDashboard();
    } catch(e) { showToast('Filter failed: '+e.message,'error'); }
}

async function renderDashboard() {
    populateFilters();
    renderBalance();
    renderResources();
    await renderKPIs();
    updateTsRangeLabels();
    renderCharts();
    renderRateLimits();
    renderSpendLimits();
    renderCallLog();
}

function renderResources() {
    const data = allData.resources;
    const container = document.getElementById('resourceAvailability');
    if (!container || !data || !data.providers) return;
    const providers = Object.values(data.providers);
    container.innerHTML = '';

    if (providers.length === 0) {
        container.innerHTML = '<div class="text-muted">No resource snapshots yet.</div>';
        return;
    }

    const order = ['elevenlabs', 'anthropic', 'codex_cli'];
    providers.sort((a, b) => order.indexOf(a.provider) - order.indexOf(b.provider));

    const formatExtraUsage = (item) => {
        const extra = item.extra_usage || {};
        const value = Number(extra.value || 0);
        if (extra.unit === 'usd') {
            const fixed = value.toFixed(2);
            const clipped = fixed.startsWith('0') ? fixed.slice(1) : fixed;
            return `$${clipped}`;
        }
        return `${Math.round(value).toLocaleString()} credits`;
    };

    for (const item of providers) {
        const w5h = item.windows?.five_hour || { used: 0, limit: 0, percent: 0 };
        const w1w = item.windows?.one_week || { used: 0, limit: 0, percent: 0 };
        const p5h = Math.max(0, Math.min(100, Number(w5h.percent || 0)));
        const p1w = Math.max(0, Math.min(100, Number(w1w.percent || 0)));
        const age = item.age_seconds !== null && item.age_seconds !== undefined ? `${Math.floor(item.age_seconds / 60)}m ago` : 'n/a';
        const error = item.error ? `<div class="resource-error">${item.error}</div>` : '';
        const statusClass5h = p5h >= 90 ? 'critical' : p5h >= 70 ? 'warn' : 'ok';
        const statusClass1w = p1w >= 90 ? 'critical' : p1w >= 70 ? 'warn' : 'ok';

        const card = document.createElement('div');
        card.className = 'resource-item';
        card.innerHTML = `
            <div class="resource-title">
                <span class="provider-dot" style="background:${getProviderColor(item.provider)}"></span>
                <span>${item.display_name || item.provider}</span>
            </div>
            <div class="resource-meter">
                <div class="resource-meter-head">
                    <span>5 hr</span>
                    <span>${p5h.toFixed(1)}%</span>
                </div>
                <div class="resource-meter-track">
                    <div class="resource-meter-fill status-${statusClass5h}" style="width:${p5h}%"></div>
                </div>
            </div>
            <div class="resource-meter">
                <div class="resource-meter-head">
                    <span>1 wk</span>
                    <span>${p1w.toFixed(1)}%</span>
                </div>
                <div class="resource-meter-track">
                    <div class="resource-meter-fill status-${statusClass1w}" style="width:${p1w}%"></div>
                </div>
            </div>
            <div class="resource-extra">${formatExtraUsage(item)}</div>
            <div class="resource-meta">Updated ${age}</div>
            ${error}
        `;
        container.appendChild(card);
    }
}

function populateFilters() {
    const ps = document.getElementById('filterProvider');
    const ms = document.getElementById('filterModel');
    const cp = ps.value, cm = ms.value;
    if (unfilteredMeta.providers.length > 0) {
        ps.innerHTML = '<option value="">All Providers</option>';
        unfilteredMeta.providers.forEach(p => { const o=document.createElement('option'); o.value=p; o.textContent=p; ps.appendChild(o); });
    }
    if (unfilteredMeta.models.length > 0) {
        ms.innerHTML = '<option value="">All Models</option>';
        unfilteredMeta.models.forEach(m => { const o=document.createElement('option'); o.value=m; o.textContent=m; ms.appendChild(o); });
    }
    ps.value=cp; ms.value=cm;
}

// ============================================================================
// BALANCE TRACKER (dynamic, sortable, toggleable)
// ============================================================================
function _captureBalanceState() {
    // Capture expanded ledgers, input values, and focused element
    const state = { expandedLedgers: new Set(), inputs: {}, focusId: null };
    document.querySelectorAll('.balance-ledger-entries.open').forEach(el => {
        state.expandedLedgers.add(el.id);
    });
    document.querySelectorAll('#balanceTracker input, #balanceTracker select').forEach(el => {
        if (el.id) state.inputs[el.id] = el.value;
    });
    const active = document.activeElement;
    if (active && active.id && document.getElementById('balanceTracker')?.contains(active)) {
        state.focusId = active.id;
    }
    return state;
}

function _restoreBalanceState(state) {
    // Restore expanded ledgers
    state.expandedLedgers.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.classList.add('open');
            // Update the toggle text to match
            const toggle = el.previousElementSibling;
            if (toggle && toggle.classList.contains('balance-ledger-toggle')) {
                toggle.textContent = toggle.textContent.replace('+ Deposits', '- Hide deposits');
            }
        }
    });
    // Restore input values
    for (const [id, val] of Object.entries(state.inputs)) {
        const el = document.getElementById(id);
        if (el) el.value = val;
    }
    // Restore focus
    if (state.focusId) {
        const el = document.getElementById(state.focusId);
        if (el) el.focus();
    }
}

function renderBalance() {
    const b = allData.balance;
    if (!b) return;
    const uiState = _captureBalanceState();
    const container = document.getElementById('balanceTracker');
    const togglesContainer = document.getElementById('balanceToggles');
    container.innerHTML = '';
    togglesContainer.innerHTML = '';

    // Build entries array with sort data
    let allEntries = Object.entries(b).map(([name, data]) => ({
        name, data,
        usage: data.usage_calls || 0,
        remaining: data.remaining !== undefined ? data.remaining : -999,
    }));

    // Sort based on selector
    const sortMode = document.getElementById('balanceSortSelect').value;
    if (sortMode === 'usage-desc') allEntries.sort((a,b) => b.usage - a.usage);
    else if (sortMode === 'usage-asc') allEntries.sort((a,b) => a.usage - b.usage);
    else if (sortMode === 'balance-desc') allEntries.sort((a,b) => b.remaining - a.remaining);
    else if (sortMode === 'balance-asc') allEntries.sort((a,b) => a.remaining - b.remaining);

    // Filter out providers with zero cost from toggles
    const toggleEntries = allEntries.filter(e => (e.data.usage_cost || 0) > 0);

    // Render toggle checkboxes above the panel for providers with cost
    for (const {name} of toggleEntries) {
        const isHidden = hiddenProviders.includes(name);
        const label = document.createElement('label');
        label.className = 'balance-toggle-label';
        label.innerHTML = `<input type="checkbox" ${isHidden ? '' : 'checked'} onchange="toggleProvider('${name}')">
            <span class="provider-dot" style="background:${getProviderColor(name)}"></span>
            <span>${name}</span>`;
        togglesContainer.appendChild(label);
    }

    // Only render visible (non-hidden) providers as cards
    const visibleEntries = allEntries.filter(e => !hiddenProviders.includes(e.name));

    for (const {name, data} of visibleEntries) {
        const card = document.createElement('div');
        card.className = 'balance-card';

        const hasProjects = data.projects && Object.keys(data.projects).length > 0;
        const curProj = selectedProject[name] || '__all__';

        // Determine what data to display based on project selection
        let displayData = data;
        let displayLedger = Array.isArray(data.ledger) ? data.ledger : [];
        let activeProject = null;
        if (hasProjects && curProj !== '__all__' && data.projects[curProj]) {
            activeProject = curProj;
            displayData = data.projects[curProj];
            displayLedger = Array.isArray(displayData.ledger) ? displayData.ledger : [];
        }

        const status = displayData.status || data.status || 'unknown';
        const statusLabel = status==='ok'?'OK':status==='warn'?'LOW':status==='critical'?'CRITICAL':status.replace(/_/g,' ').toUpperCase();
        const colorClass = ['ok','warn','critical'].includes(status) ? status : 'unknown';
        const ledgerConfigured =
            Object.prototype.hasOwnProperty.call(displayData, 'ledger') ||
            Object.prototype.hasOwnProperty.call(data, 'ledger');
        const hasLedgerEntries = Array.isArray(displayLedger) && displayLedger.length > 0;
        const hasApiKey = data.api_note || (!ledgerConfigured && data.remaining !== undefined && !hasProjects);

        let remainingText = '—', detailText = '';
        const rem = displayData.remaining !== undefined ? displayData.remaining : data.remaining;
        if (rem !== undefined) {
            remainingText = `$${rem.toFixed(2)}`;
            const deposits = displayData.total_deposits !== undefined ? displayData.total_deposits : data.total_deposits;
            const cost = displayData.cumulative_cost !== undefined ? displayData.cumulative_cost : data.cumulative_cost;
            const personal = displayData.personal_invested !== undefined ? displayData.personal_invested : data.personal_invested;
            if (deposits !== undefined) {
                detailText = `Invested: $${(personal || deposits).toFixed(2)} | Spent: $${(cost||0).toFixed(2)}`;
            }
        } else if (data.message) {
            remainingText = data.message;
        }

        // Usage stats
        const uCalls = displayData.usage_calls || data.usage_calls || 0;
        const uCost = displayData.usage_cost || data.usage_cost || 0;
        let usageLine = '';
        if (uCalls) usageLine = `${uCalls.toLocaleString()} calls | $${uCost.toFixed(2)} cost`;

        let html = `
            <div class="balance-card-header">
                <div style="display:flex;align-items:center;gap:6px;">
                    <span class="provider-dot" style="background:${getProviderColor(name)}"></span>
                    <span class="balance-provider-name">${name}</span>
                </div>
                <span class="balance-status-badge status-${status}">${statusLabel}</span>
            </div>`;

        // Project selector dropdown for multi-project providers
        if (hasProjects) {
            const projNames = Object.keys(data.projects);
            html += `<select class="project-selector" onchange="switchProject('${name}', this.value)">`;
            html += `<option value="__all__"${curProj==='__all__'?' selected':''}>All Projects ($${data.remaining.toFixed(2)})</option>`;
            for (const pn of projNames) {
                const pr = data.projects[pn];
                html += `<option value="${pn}"${curProj===pn?' selected':''}>${pn} ($${pr.remaining.toFixed(2)})</option>`;
            }
            html += `</select>`;
        }

        html += `<div class="balance-remaining color-${colorClass}">${remainingText}</div>`;
        if (detailText) html += `<div class="balance-detail">${detailText}</div>`;
        if (usageLine) html += `<div class="balance-detail">${usageLine}</div>`;

        if (ledgerConfigured) {
            const ledgerId = `ledger-${name}-${activeProject||'all'}`;
            html += `<div class="balance-ledger">
                <div class="balance-ledger-toggle" onclick="document.getElementById('${ledgerId}').classList.toggle('open');this.textContent=this.textContent.includes('+')?'- Hide deposits':'+ Deposits (${displayLedger.length})'">+ Deposits (${displayLedger.length})</div>
                <div class="balance-ledger-entries" id="${ledgerId}">`;
            if (hasLedgerEntries) {
                displayLedger.forEach((e, idx) => {
                    const cls = e.is_voucher ? 'balance-ledger-voucher' : '';
                    const projTag = (!activeProject && e.project) ? `<span style="color:var(--text-secondary);font-size:10px;">[${e.project}]</span> ` : '';
                    const deleteProject = activeProject || e.project || '';
                    html += `<div class="balance-ledger-entry ${cls}">
                        <span class="balance-ledger-date">${e.date}</span>
                        <span class="balance-ledger-note">${projTag}${e.note||''}</span>
                        <span class="balance-ledger-amount">${e.is_voucher?'':'+'}\$${(e.amount||0).toFixed(2)}${e.is_voucher?' (voucher)':''}</span>
                        <button class="ledger-delete-btn" onclick="deleteLedgerEntry('${name}',${idx},'${deleteProject}')" title="Remove this entry">&times;</button>
                    </div>`;
                });
            }
            html += `</div></div>`;

            // Top-up form — for multi-project, include project selector
            if (!hasApiKey) {
                if (hasProjects) {
                    const projNames = Object.keys(data.projects);
                    html += `<div class="balance-topup"><div class="topup-fields">
                        <select class="topup-input" id="topup-project-${name}" style="width:100px;">
                            ${projNames.map(pn => `<option value="${pn}"${pn===activeProject?' selected':''}>${pn}</option>`).join('')}
                        </select>
                        <input type="number" class="topup-input topup-amount" placeholder="$" step="0.01" min="0.01" id="topup-amount-${name}">
                        <input type="text" class="topup-input topup-note" placeholder="Note" id="topup-note-${name}">
                        <button class="btn primary topup-btn" onclick="submitTopup('${name}',this)">Add</button>
                    </div></div>`;
                } else {
                    html += `<div class="balance-topup"><div class="topup-fields">
                        <input type="number" class="topup-input topup-amount" placeholder="$" step="0.01" min="0.01" id="topup-amount-${name}">
                        <input type="text" class="topup-input topup-note" placeholder="Note" id="topup-note-${name}">
                        <button class="btn primary topup-btn" onclick="submitTopup('${name}',this)">Add</button>
                    </div></div>`;
                }
            }
        }

        card.innerHTML = html;
        container.appendChild(card);
    }

    // Restore UI state (expanded ledgers, input values, focus)
    _restoreBalanceState(uiState);

    // Setup horizontal wheel scrolling
    setupBalanceScroll();
}

function setupBalanceScroll() {
    const container = document.getElementById('balanceTracker');
    // Remove old listener if any
    container.onwheel = function(e) {
        if (container.scrollWidth > container.clientWidth) {
            e.preventDefault();
            container.scrollLeft += e.deltaY;
        }
    };
}

function toggleProvider(name) {
    const idx = hiddenProviders.indexOf(name);
    if (idx >= 0) hiddenProviders.splice(idx, 1);
    else hiddenProviders.push(name);
    saveState('hiddenProviders', hiddenProviders);
    renderBalance();
}

function switchProject(provider, projectName) {
    selectedProject[provider] = projectName;
    saveState('selectedProject', selectedProject);
    renderBalance();
}

async function submitTopup(provider, btn) {
    const amountInput = document.getElementById(`topup-amount-${provider}`);
    const noteInput = document.getElementById(`topup-note-${provider}`);
    const projectSelect = document.getElementById(`topup-project-${provider}`);
    const amount = parseFloat(amountInput.value);
    if (!amount || amount <= 0) { showToast('Enter a valid amount','error'); return; }
    const project = projectSelect ? projectSelect.value : undefined;
    btn.disabled=true; btn.textContent='...';
    try {
        const body = {provider, amount, note: noteInput.value || ''};
        if (project) body.project = project;
        const resp = await fetch('/api/balance/topup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const data = await resp.json();
        if (data.error) showToast(data.error,'error');
        else {
            showToast(`Added $${amount.toFixed(2)} to ${provider}${project ? '/'+project : ''}`,'success');
            amountInput.value=''; noteInput.value='';
            allData.balance = await fetch('/api/balance').then(r=>r.json());
            renderBalance();
        }
    } catch(e) { showToast('Failed: '+e.message,'error'); }
    finally { btn.disabled=false; btn.textContent='Add'; }
}

// ============================================================================
// KPIs
// ============================================================================
let kpiRenderSeq = 0;

async function fetchKpiSummaries() {
    const fq = getFilterParams();
    const currentParams = getKpiRangeParams();
    const containing = getContainingKpiRangeInfo();
    const currentReq = fetch(`/api/summary?_=1${fq}${currentParams}`).then(r => r.json());

    if (!containing.supported) {
        const currentSummary = await currentReq;
        return { currentSummary, containingSummary:null, containing };
    }

    const containingReq = fetch(`/api/summary?_=1${fq}${containing.params}`).then(r => r.json());
    const [currentSummary, containingSummary] = await Promise.all([currentReq, containingReq]);
    return { currentSummary, containingSummary, containing };
}

async function renderKPIs() {
    const renderSeq = ++kpiRenderSeq;
    const rangeLabel = getKpiRangeLabel();
    const fallbackSummary = allData.summary || { total_calls:0, session_count:0, total_cost:0, error_rate:0, error_count:0 };

    try {
        const { currentSummary, containingSummary, containing } = await fetchKpiSummaries();
        if (renderSeq !== kpiRenderSeq) return;

        const s = currentSummary || fallbackSummary;
        allData.summary = s;
        document.getElementById('kpiCalls').textContent = s.total_calls.toLocaleString();
        document.getElementById('kpiCallsMeta').textContent = `${s.session_count} sessions · ${rangeLabel}`;
        document.getElementById('kpiCost').textContent = `$${s.total_cost.toFixed(2)}`;
        document.getElementById('kpiCostMeta').textContent = s.total_calls > 0 ? `$${(s.total_cost/s.total_calls).toFixed(6)}/call` : '';
        const ep = (s.error_rate*100).toFixed(2);
        document.getElementById('kpiErrorRate').textContent = `${ep}%`;
        const eb = ep > 5 ? 'error' : ep > 2 ? 'warn' : 'success';
        document.getElementById('kpiErrorRateMeta').innerHTML = `<span class="badge ${eb}">${s.error_count} errors</span>`;
        const models = allData.models.models;
        const avgCH = models.length > 0 ? (models.reduce((sum,m)=>sum+m.avg_cache_hit_ratio,0)/models.length*100).toFixed(1) : '0.0';
        document.getElementById('kpiCacheHit').textContent = `${avgCH}%`;
        document.getElementById('kpiCacheHitMeta').textContent = `Avg across models · ${rangeLabel}`;
        document.getElementById('kpiSessions').textContent = s.session_count.toLocaleString();
        document.getElementById('kpiSessionsMeta').textContent = `${(s.total_calls/Math.max(s.session_count,1)).toFixed(1)} calls/session`;

        const ofPeriodValue = document.getElementById('kpiOfPeriod');
        const ofPeriodMeta = document.getElementById('kpiOfPeriodMeta');
        if (!containing.supported) {
            ofPeriodValue.textContent = '—';
            ofPeriodMeta.textContent = '—';
        } else {
            const containingCost = containingSummary ? containingSummary.total_cost : 0;
            const pct = containingCost > 0 ? (s.total_cost / containingCost) * 100 : 0;
            ofPeriodValue.textContent = `${Math.round(pct)}%`;
            ofPeriodMeta.textContent = containing.meta;
        }
    } catch {
        if (renderSeq !== kpiRenderSeq) return;
        const s = fallbackSummary;
        document.getElementById('kpiCalls').textContent = s.total_calls.toLocaleString();
        document.getElementById('kpiCallsMeta').textContent = `${s.session_count} sessions · ${rangeLabel}`;
        document.getElementById('kpiCost').textContent = `$${s.total_cost.toFixed(2)}`;
        document.getElementById('kpiCostMeta').textContent = s.total_calls > 0 ? `$${(s.total_cost/s.total_calls).toFixed(6)}/call` : '';
        const ep = (s.error_rate*100).toFixed(2);
        document.getElementById('kpiErrorRate').textContent = `${ep}%`;
        const eb = ep > 5 ? 'error' : ep > 2 ? 'warn' : 'success';
        document.getElementById('kpiErrorRateMeta').innerHTML = `<span class="badge ${eb}">${s.error_count} errors</span>`;
        const models = allData.models.models;
        const avgCH = models.length > 0 ? (models.reduce((sum,m)=>sum+m.avg_cache_hit_ratio,0)/models.length*100).toFixed(1) : '0.0';
        document.getElementById('kpiCacheHit').textContent = `${avgCH}%`;
        document.getElementById('kpiCacheHitMeta').textContent = `Avg across models · ${rangeLabel}`;
        document.getElementById('kpiSessions').textContent = s.session_count.toLocaleString();
        document.getElementById('kpiSessionsMeta').textContent = `${(s.total_calls/Math.max(s.session_count,1)).toFixed(1)} calls/session`;
        document.getElementById('kpiOfPeriod').textContent = '—';
        document.getElementById('kpiOfPeriodMeta').textContent = '—';
    }
}

// ============================================================================
// TIME FORMATTING — Hierarchical labels for Chart.js
// ============================================================================
// Returns arrays for multi-line labels. Chart.js renders each array element as a line.
// We group by day (for minute/hour) or by month (for day/week) to show hierarchical context.
function buildHierarchicalLabels(timestamps) {
    const interval = document.getElementById('filterInterval').value;
    let lastGroup = null;

    return timestamps.map(ms => {
        const d = new Date(ms);
        if (interval === 'minute') {
            const timeStr = d.toLocaleString('en-US', {hour:'numeric',minute:'2-digit',hour12:true}).replace(/\s/g,'');
            const dayStr = d.toLocaleString('en-US', {month:'short',day:'numeric'});
            if (dayStr !== lastGroup) { lastGroup = dayStr; return [timeStr, dayStr]; }
            return [timeStr, ''];
        }
        if (interval === 'hour') {
            const timeStr = d.toLocaleString('en-US', {hour:'numeric',hour12:true}).replace(/\s/g,'');
            const dayStr = d.toLocaleString('en-US', {weekday:'short',month:'short',day:'numeric'});
            if (dayStr !== lastGroup) { lastGroup = dayStr; return [timeStr, dayStr]; }
            return [timeStr, ''];
        }
        if (interval === 'day') {
            const dayStr = d.toLocaleString('en-US', {weekday:'short',day:'numeric'});
            const monthStr = d.toLocaleString('en-US', {month:'long',year:'2-digit'});
            if (monthStr !== lastGroup) { lastGroup = monthStr; return [dayStr, monthStr]; }
            return [dayStr, ''];
        }
        if (interval === 'week') {
            const wkStr = d.toLocaleString('en-US', {month:'short',day:'numeric'});
            const monthStr = d.toLocaleString('en-US', {month:'long',year:'2-digit'});
            if (monthStr !== lastGroup) { lastGroup = monthStr; return ['Wk ' + wkStr, monthStr]; }
            return ['Wk ' + wkStr, ''];
        }
        if (interval === 'month') {
            const yr = "'" + String(d.getFullYear()).slice(2);
            return [d.toLocaleString('en-US', {month:'short'}) + ' ' + yr];
        }
        return [d.toLocaleString('en-US', {hour:'numeric',hour12:true})];
    });
}

function formatTimeLabel(ms) {
    const d = new Date(ms);
    const interval = document.getElementById('filterInterval').value;
    if (interval === 'minute') return d.toLocaleString('en-US', {hour:'numeric',minute:'2-digit',hour12:true}).replace(/\s/g,'');
    if (interval === 'hour') return d.toLocaleString('en-US', {hour:'numeric',hour12:true}).replace(/\s/g,'');
    if (interval === 'day') return d.toLocaleString('en-US', {month:'short',day:'numeric'});
    if (interval === 'week') return 'Wk ' + d.toLocaleString('en-US', {month:'short',day:'numeric'});
    if (interval === 'month') { const yr = String(d.getFullYear()).slice(2); return d.toLocaleString('en-US', {month:'short'}) + " '" + yr; }
    return d.toLocaleString('en-US', {hour:'numeric',hour12:true});
}

function formatCallTime(ms) {
    const d = new Date(ms);
    if (useUTC) {
        const yr = String(d.getUTCFullYear()).slice(2);
        const mo = String(d.getUTCMonth()+1).padStart(2,'0');
        const dy = String(d.getUTCDate()).padStart(2,'0');
        const hr = d.getUTCHours(); const ampm = hr>=12?'PM':'AM';
        const h12 = hr%12||12; const mn = String(d.getUTCMinutes()).padStart(2,'0');
        return `${mo}/${dy}/${yr} ${h12}:${mn}${ampm}`;
    }
    const yr = String(d.getFullYear()).slice(2);
    const mo = String(d.getMonth()+1).padStart(2,'0');
    const dy = String(d.getDate()).padStart(2,'0');
    const hr = d.getHours(); const ampm = hr>=12?'PM':'AM';
    const h12 = hr%12||12; const mn = String(d.getMinutes()).padStart(2,'0');
    return `${mo}/${dy}/${yr} ${h12}:${mn}${ampm}`;
}

// ============================================================================
// CHARTS
// ============================================================================
function renderCharts() {
    renderTokenTimeSeries();
    renderCostTimeSeries();
    renderByProvider();
    renderByModel();
    renderStopReasons();
    renderEvalScores();
    renderTopTools();
    renderProjection();
}

const darkGrid = { color:'#30363d', drawBorder:false };
const darkTick = { color:'#8b949e' };
const darkLegend = { labels:{ color:'#c9d1d9', boxWidth:10, font:{size:11} } };

// Fill empty time buckets so charts show the full time window
function fillEmptyBuckets(data, interval) {
    if (data.length === 0) return data;
    const bucketMs = { minute:60000, hour:3600000, day:86400000, week:604800000, month:2592000000 }[interval] || 3600000;
    // Use the TS range window, not just the data range
    const rangeInfo = INTERVAL_RANGES[interval] || INTERVAL_RANGES.hour;
    const windowMs = tsWindowMs || rangeInfo.default;
    const startMs = tsEndMs - windowMs;
    const endMs = tsEndMs;
    // Build a map of existing data by bucket (align to bucket boundary like the backend does)
    const dataMap = {};
    const alignBucket = (ms) => Math.floor(Math.floor(ms / 1000) / (bucketMs / 1000)) * bucketMs;
    data.forEach(d => { dataMap[d.timestamp] = d; });
    // Generate all buckets in the window
    const filled = [];
    const firstBucket = alignBucket(startMs);
    for (let ts = firstBucket; ts <= endMs; ts += bucketMs) {
        if (dataMap[ts]) {
            filled.push(dataMap[ts]);
        } else {
            filled.push({ timestamp: ts, calls: 0, cost: 0, tokens: 0, errors: 0 });
        }
    }
    return filled;
}

// Legend state persistence — remembers which datasets the user toggled on/off
let legendState = loadState('legendState', {});

function saveLegendState(chartName) {
    const chart = chartInstances[chartName];
    if (!chart) return;
    const state = {};
    chart.data.datasets.forEach((ds, i) => {
        const meta = chart.getDatasetMeta(i);
        // meta.hidden is null if matching dataset.hidden, otherwise true/false from user toggle
        if (meta.hidden === true) state[ds.label] = true;
        else if (meta.hidden === false || meta.hidden === null && !ds.hidden) state[ds.label] = false;
        else state[ds.label] = true; // dataset.hidden and no user override
    });
    legendState[chartName] = state;
    saveState('legendState', legendState);
}

function restoreLegendHidden(chartName, label, defaultHidden) {
    const state = legendState[chartName];
    if (state && state[label] !== undefined) return state[label];
    return defaultHidden;
}

function renderTokenTimeSeries() {
    const interval = document.getElementById('filterInterval').value;
    const rawData = allData.timeseries.data;
    const data = fillEmptyBuckets(rawData, interval);
    const providerTokens = allData.timeseries.provider_tokens || {};
    const ctx = document.getElementById('tokenTimeSeriesChart');
    const timestamps = data.map(d => d.timestamp);
    const labels = buildHierarchicalLabels(timestamps);

    // Build cost lookup for tooltip
    const costByIdx = data.map(d => d.cost);

    // Main "Total" dataset
    const datasets = [{
        label: 'Total',
        data: data.map(d => d.tokens),
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.1)',
        tension: 0.3,
        fill: true,
        hidden: restoreLegendHidden('tokenTimeSeries', 'Total', false),
        order: 0,
    }];

    // Per-provider datasets (hidden by default) when "All Providers" is active
    const providerFilter = document.getElementById('filterProvider').value;
    if (!providerFilter) {
        // Only include providers that have accrued cost
        const providerNames = Object.keys(providerTokens).filter(prov => {
            const provData = providerTokens[prov];
            const totalCost = Object.values(provData).reduce((s, v) => s + (v.cost || 0), 0);
            return totalCost > 0;
        }).sort();
        providerNames.forEach((prov, i) => {
            const provData = providerTokens[prov];
            datasets.push({
                label: prov,
                data: timestamps.map(ts => (provData[ts] && provData[ts].tokens) || 0),
                borderColor: getProviderColor(prov),
                backgroundColor: 'transparent',
                tension: 0.3,
                fill: false,
                hidden: restoreLegendHidden('tokenTimeSeries', prov, true),
                borderDash: [4, 2],
                borderWidth: 2,
                pointRadius: 2,
                order: i + 1,
            });
        });
    }

    destroyChart('tokenTimeSeries');
    chartInstances['tokenTimeSeries'] = new Chart(ctx, {
        type:'line',
        data:{ labels, datasets },
        options:{
            responsive:true, maintainAspectRatio:false,
            plugins:{
                legend:{
                    labels:{
                        color:'#c9d1d9', boxWidth:10, font:{size:11},
                        // Inactive = unfilled dotted box + dimmed text; active = filled box + bright text
                        generateLabels: (chart) => {
                            return chart.data.datasets.map((ds, i) => {
                                const meta = chart.getDatasetMeta(i);
                                const isHidden = meta.hidden === true || (meta.hidden === null && ds.hidden);
                                const color = ds.borderColor || ds.backgroundColor;
                                return {
                                    text: ds.label,
                                    fillStyle: isHidden ? 'transparent' : color,
                                    strokeStyle: isHidden ? 'rgba(139,148,158,0.5)' : color,
                                    lineWidth: isHidden ? 1.5 : 2,
                                    lineDash: isHidden ? [3, 2] : [],
                                    hidden: false, // never use Chart.js built-in strikethrough
                                    fontColor: isHidden ? 'rgba(139,148,158,0.45)' : '#c9d1d9',
                                    datasetIndex: i,
                                };
                            });
                        },
                    },
                    onClick: (e, legendItem, legend) => {
                        const index = legendItem.datasetIndex;
                        const meta = legend.chart.getDatasetMeta(index);
                        const isCurrentlyHidden = meta.hidden === true || (meta.hidden === null && legend.chart.data.datasets[index].hidden);
                        meta.hidden = isCurrentlyHidden ? false : true;
                        legend.chart.update();
                        saveLegendState('tokenTimeSeries');
                    },
                },
                tooltip:{
                    mode: 'index',
                    intersect: false,
                    callbacks:{
                        label: function(ctx) {
                            const val = ctx.parsed.y;
                            if (val === 0 && ctx.datasetIndex > 0) return null;
                            const label = ctx.dataset.label;
                            // For the Total line, also show dollar cost
                            if (ctx.datasetIndex === 0) {
                                const cost = costByIdx[ctx.dataIndex] || 0;
                                return `${label}: ${val.toLocaleString()} tokens ($${cost.toFixed(4)})`;
                            }
                            // For per-provider lines, show tokens and cost
                            const ts = timestamps[ctx.dataIndex];
                            const provData = providerTokens[label];
                            const cost = (provData && provData[ts] && provData[ts].cost) || 0;
                            return `${label}: ${val.toLocaleString()} tokens ($${cost.toFixed(4)})`;
                        },
                    },
                },
            },
            scales:{
                y:{ticks:darkTick,grid:darkGrid},
                x:{ticks:{color:'#8b949e',maxRotation:0,autoSkip:true,maxTicksLimit:20,font:{size:10}},grid:darkGrid},
            },
        },
    });
}

function renderCostTimeSeries() {
    const interval = document.getElementById('filterInterval').value;
    const rawData = allData.timeseries.data;
    const data = fillEmptyBuckets(rawData, interval);
    const provCosts = allData.timeseries.provider_costs || {};
    const ctx = document.getElementById('costTimeSeriesChart');
    const timestamps = data.map(d => d.timestamp);
    const labels = buildHierarchicalLabels(timestamps);

    // Build stacked datasets per provider (exclude providers with no cost)
    const providers = Object.keys(provCosts).filter(prov => {
        const total = Object.values(provCosts[prov]).reduce((s,v) => s + v, 0);
        return total > 0;
    });
    const datasets = providers.map(prov => ({
        label: prov,
        data: timestamps.map(ts => provCosts[prov][ts] || 0),
        backgroundColor: getProviderColor(prov),
    }));

    // Fallback if no provider breakdown
    if (datasets.length === 0) {
        datasets.push({ label:'Cost', data:data.map(d=>d.cost), backgroundColor:'#3fb950' });
    }

    destroyChart('costTimeSeries');
    chartInstances['costTimeSeries'] = new Chart(ctx, {
        type:'bar',
        data:{ labels, datasets },
        options:{
            responsive:true, maintainAspectRatio:false,
            plugins:{ legend:darkLegend, tooltip:{ mode:'index', intersect:false, callbacks:{ label: ctx => ctx.parsed.y > 0 ? `${ctx.dataset.label}: $${ctx.parsed.y.toFixed(4)}` : null, footer: items => { const total = items.reduce((s,i) => s + i.parsed.y, 0); return `Total: $${total.toFixed(4)}`; } } } },
            scales:{
                y:{ stacked:true, ticks:darkTick, grid:darkGrid },
                x:{ stacked:true, ticks:{color:'#8b949e',maxRotation:0,autoSkip:true,maxTicksLimit:20,font:{size:10}}, grid:darkGrid },
            },
        },
    });
}

function renderByProvider() {
    const data = allData.summary.by_provider.filter(d => d.cost > 0 || d.tokens > 0);
    const ctx = document.getElementById('byProviderChart');
    destroyChart('byProvider');
    chartInstances['byProvider'] = new Chart(ctx, {
        type:'doughnut',
        data:{
            labels: data.map(d=>d.provider),
            datasets:[{ data:data.map(d=>d.calls), backgroundColor:data.map(d=>getProviderColor(d.provider)), borderColor:'#161b22', borderWidth:2 }],
        },
        options:{
            responsive:true, maintainAspectRatio:false,
            plugins:{
                legend:darkLegend,
                tooltip:{ callbacks:{ label: ctx => {
                    const d = data[ctx.dataIndex];
                    return `${d.provider}: ${d.calls} calls | $${d.cost.toFixed(2)} | ${(d.tokens||0).toLocaleString()} tokens`;
                }}}
            },
        },
    });
}

function renderByModel() {
    const data = allData.summary.by_model.filter(d => d.cost > 0 || d.tokens > 0).slice(0,8);
    const ctx = document.getElementById('byModelChart');
    destroyChart('byModel');
    chartInstances['byModel'] = new Chart(ctx, {
        type:'bar',
        data:{
            labels: data.map(d=>d.model),
            datasets:[{ label:'Calls', data:data.map(d=>d.calls), backgroundColor:data.map((_,i)=>COLORS_PALETTE[i%COLORS_PALETTE.length]) }],
        },
        options:{
            responsive:true, maintainAspectRatio:false, indexAxis:'y',
            plugins:{
                legend:darkLegend,
                tooltip:{
                    mode: 'index',
                    intersect: false,
                    axis: 'y',
                    callbacks:{ label: ctx => {
                        const d = data[ctx.dataIndex];
                        return `${d.calls} calls | $${d.cost.toFixed(2)} | ${(d.tokens||0).toLocaleString()} tokens`;
                    }}
                }
            },
            scales:{ x:{ticks:darkTick,grid:darkGrid}, y:{ticks:darkTick,grid:{drawBorder:false}} },
        },
    });
}

function renderStopReasons() {
    const ctx = document.getElementById('stopReasonsChart');
    const calls = allData.calls.calls;
    const rm = {};
    calls.forEach(c => { rm[c.stop_reason] = (rm[c.stop_reason]||0)+1; });
    destroyChart('stopReasons');
    chartInstances['stopReasons'] = new Chart(ctx, {
        type:'pie',
        data:{ labels:Object.keys(rm), datasets:[{ data:Object.values(rm), backgroundColor:COLORS_PALETTE, borderColor:'#161b22', borderWidth:2 }] },
        options:{ responsive:true, maintainAspectRatio:false, plugins:{ legend:darkLegend } },
    });
}

function renderEvalScores() {
    const evals = allData.evals.evals;
    const ctx = document.getElementById('evalScoresChart');
    destroyChart('evalScores');
    chartInstances['evalScores'] = new Chart(ctx, {
        type:'radar',
        data:{
            labels: evals.map(e=>e.eval_name),
            datasets:[{ label:'Score', data:evals.map(e=>e.score*100), borderColor:'#bc8ef6', backgroundColor:'rgba(188,142,246,0.15)', fill:true }],
        },
        options:{
            responsive:true, maintainAspectRatio:false,
            plugins:{ legend:{ display:false } },
            scales:{ r:{ ticks:darkTick, grid:{color:'#30363d'}, suggestedMin:0, suggestedMax:100 } },
        },
    });
}

function renderTopTools() {
    const tools = allData.tools.tools.slice(0,15);
    const ctx = document.getElementById('topToolsChart');
    // Dynamically size the container so each bar has enough height
    const chartContainer = ctx.parentElement;
    const minHeight = Math.max(220, tools.length * 24 + 40);
    chartContainer.style.height = minHeight + 'px';
    destroyChart('topTools');
    chartInstances['topTools'] = new Chart(ctx, {
        type:'bar',
        data:{
            labels: tools.map(t=>t.tool),
            datasets:[{ label:'Usage', data:tools.map(t=>t.count), backgroundColor:tools.map((_,i)=>COLORS_PALETTE[i%COLORS_PALETTE.length]) }],
        },
        options:{
            responsive:true, maintainAspectRatio:false, indexAxis:'y',
            layout:{ padding:{ left:0 } },
            plugins:{
                legend:{ display:false },
                datalabels: false,
                tooltip:{
                    mode: 'index',
                    intersect: false,
                    axis: 'y',
                    callbacks:{
                        label: ctx => `${ctx.parsed.x.toLocaleString()} uses`,
                    },
                },
            },
            scales:{
                x:{ ticks:darkTick, grid:darkGrid },
                y:{
                    ticks:{ color:'#c9d1d9', font:{size:11}, mirror:false, padding:8 },
                    grid:{drawBorder:false},
                    afterFit(axis) { axis.width = Math.max(axis.width, 130); },
                },
            },
        },
    });
}

function renderProjection() {
    const p = allData.costProjection;
    document.getElementById('projectionCost').textContent = `$${p.projected_monthly_cost.toFixed(2)}`;
    document.getElementById('projectionNote').textContent = `Based on ${p.days_of_data} days ($${p.avg_daily_cost.toFixed(2)}/day)`;
}

// ============================================================================
// LEDGER DELETE
// ============================================================================
async function deleteLedgerEntry(provider, index, project) {
    const label = project ? `${provider}/${project}` : provider;
    if (!confirm(`Remove this ledger entry from ${label}?`)) return;
    try {
        const body = { provider, index };
        if (project) body.project = project;
        const resp = await fetch('/api/balance/topup/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.error) { showToast(data.error, 'error'); return; }
        showToast(`Removed $${(data.removed.amount||0).toFixed(2)} from ${label}`, 'success');
        allData.balance = await fetch('/api/balance').then(r => r.json());
        renderBalance();
    } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

// ============================================================================
// RATE LIMITS
// ============================================================================
function renderRateLimits() {
    const rl = allData.ratelimits;
    if (!rl) return;
    const container = document.getElementById('rateLimitContainer');
    container.innerHTML = '';

    const families = rl.families || {};
    const familyNames = Object.keys(families).sort();

    if (familyNames.length === 0) {
        container.innerHTML = '<div style="color:var(--text-secondary);font-size:12px;padding:8px;">No rate limits configured. Click "+ Add Custom Limit" to set limits for a model family.</div>';
        return;
    }

    for (const family of familyNames) {
        const fam = families[family];
        const lim = fam.limits || {};
        const u1m = fam.usage_1m || { rpm: 0, tpm: 0 };
        const u5m = fam.usage_5m || { rpm: 0, tpm: 0 };
        const u1h = fam.usage_1h || { rph: 0, tph: 0 };
        const errors = fam.rate_limit_errors;
        const memberModels = fam.models || [];

        const card = document.createElement('div');
        card.className = 'rate-limit-model-card';

        const modelsSubtitle = memberModels.length > 0
            ? `<div class="rl-family-models">${memberModels.join(', ')}</div>`
            : '';
        const autoTag = fam.auto_detected
            ? '<span class="rl-auto-badge">auto-detected</span>'
            : '';

        // Rate limit error alert
        let errorBanner = '';
        if (errors && errors.error_count > 0) {
            const ago = Math.round((Date.now() - errors.last_error) / 1000);
            let agoStr;
            if (ago < 60) agoStr = `${ago}s ago`;
            else if (ago < 3600) agoStr = `${Math.round(ago/60)}m ago`;
            else agoStr = `${Math.round(ago/3600)}h ago`;
            errorBanner = `<div style="background:rgba(248,81,73,0.15);border:1px solid rgba(248,81,73,0.4);border-radius:4px;padding:4px 8px;margin-bottom:6px;font-size:11px;color:#f85149;">
                ⚠ Rate limited ${errors.error_count}× in last hour (last: ${agoStr})
            </div>`;
        }

        let html = `<div class="rl-model-name">${family} ${autoTag}</div>${modelsSubtitle}${errorBanner}<div class="rl-meters">`;

        // For per-minute meters: show the HIGHER of current 1m vs average-per-minute over 5m
        // This prevents bars from showing 0 immediately after a burst
        function effectivePerMin(current1m, total5m) {
            const avg5m = total5m / 5;
            return Math.max(current1m, avg5m);
        }

        // RPM meter
        if (lim.rpm) {
            const effective = effectivePerMin(u1m.rpm, u5m.rpm);
            const pct = Math.min(100, (effective / lim.rpm) * 100);
            const cls = errors ? 'rl-critical' : pct >= 90 ? 'rl-critical' : pct >= 70 ? 'rl-warn' : 'rl-ok';
            const label = u1m.rpm !== Math.round(effective) ? `RPM <span style="font-size:9px;opacity:0.6">(5m avg)</span>` : 'RPM';
            html += `<div class="rl-meter">
                <div class="rl-meter-label">${label}</div>
                <div class="rl-meter-bar"><div class="rl-meter-fill ${cls}" style="width:${pct}%"></div></div>
                <div class="rl-meter-value">${Math.round(effective)} / ${lim.rpm}</div>
            </div>`;
        }

        // TPM meter
        if (lim.tpm) {
            const effective = effectivePerMin(u1m.tpm, u5m.tpm);
            const pct = Math.min(100, (effective / lim.tpm) * 100);
            const cls = errors ? 'rl-critical' : pct >= 90 ? 'rl-critical' : pct >= 70 ? 'rl-warn' : 'rl-ok';
            const label = u1m.tpm !== Math.round(effective) ? `TPM <span style="font-size:9px;opacity:0.6">(5m avg)</span>` : 'TPM';
            html += `<div class="rl-meter">
                <div class="rl-meter-label">${label}</div>
                <div class="rl-meter-bar"><div class="rl-meter-fill ${cls}" style="width:${pct}%"></div></div>
                <div class="rl-meter-value">${Math.round(effective).toLocaleString()} / ${lim.tpm.toLocaleString()}</div>
            </div>`;
        }

        // Input TPM meter
        if (lim.input_tpm) {
            const current = u1m.input_tpm || 0;
            const total5 = u5m.input_tpm || 0;
            const effective = effectivePerMin(current, total5);
            const pct = Math.min(100, (effective / lim.input_tpm) * 100);
            const cls = errors ? 'rl-critical' : pct >= 90 ? 'rl-critical' : pct >= 70 ? 'rl-warn' : 'rl-ok';
            const label = current !== Math.round(effective) ? `IN TPM <span style="font-size:9px;opacity:0.6">(5m avg)</span>` : 'IN TPM';
            html += `<div class="rl-meter">
                <div class="rl-meter-label rl-sub">${label}</div>
                <div class="rl-meter-bar"><div class="rl-meter-fill ${cls}" style="width:${pct}%"></div></div>
                <div class="rl-meter-value">${Math.round(effective).toLocaleString()} / ${lim.input_tpm.toLocaleString()}</div>
            </div>`;
        }

        // Output TPM meter
        if (lim.output_tpm) {
            const current = u1m.output_tpm || 0;
            const total5 = u5m.output_tpm || 0;
            const effective = effectivePerMin(current, total5);
            const pct = Math.min(100, (effective / lim.output_tpm) * 100);
            const cls = errors ? 'rl-critical' : pct >= 90 ? 'rl-critical' : pct >= 70 ? 'rl-warn' : 'rl-ok';
            const label = current !== Math.round(effective) ? `OUT TPM <span style="font-size:9px;opacity:0.6">(5m avg)</span>` : 'OUT TPM';
            html += `<div class="rl-meter">
                <div class="rl-meter-label rl-sub">${label}</div>
                <div class="rl-meter-bar"><div class="rl-meter-fill ${cls}" style="width:${pct}%"></div></div>
                <div class="rl-meter-value">${Math.round(effective).toLocaleString()} / ${lim.output_tpm.toLocaleString()}</div>
            </div>`;
        }

        // RPH meter
        if (lim.rph) {
            const pct = Math.min(100, (u1h.rph / lim.rph) * 100);
            const cls = pct >= 90 ? 'rl-critical' : pct >= 70 ? 'rl-warn' : 'rl-ok';
            html += `<div class="rl-meter">
                <div class="rl-meter-label">RPH</div>
                <div class="rl-meter-bar"><div class="rl-meter-fill ${cls}" style="width:${pct}%"></div></div>
                <div class="rl-meter-value">${u1h.rph} / ${lim.rph}</div>
            </div>`;
        }

        // TPH meter
        if (lim.tph) {
            const pct = Math.min(100, (u1h.tph / lim.tph) * 100);
            const cls = pct >= 90 ? 'rl-critical' : pct >= 70 ? 'rl-warn' : 'rl-ok';
            html += `<div class="rl-meter">
                <div class="rl-meter-label">TPH</div>
                <div class="rl-meter-bar"><div class="rl-meter-fill ${cls}" style="width:${pct}%"></div></div>
                <div class="rl-meter-value">${u1h.tph.toLocaleString()} / ${lim.tph.toLocaleString()}</div>
            </div>`;
        }

        // If no limits configured at all, show usage only
        const hasAnyLimit = lim.rpm || lim.tpm || lim.rph || lim.tph;
        if (!hasAnyLimit) {
            html += `<div class="rl-meter">
                <div class="rl-meter-label" style="color:var(--text-secondary)">No limits set</div>
                <div class="rl-meter-value">${u1m.rpm} req/min | ${u1m.tpm.toLocaleString()} tok/min</div>
            </div>`;
        }

        html += '</div>';
        if (!fam.auto_detected) {
            html += `<button class="ledger-delete-btn rl-edit-btn" onclick="editRateLimit('${family}')" title="Edit limits">&#9998;</button>`;
        }
        card.innerHTML = html;
        container.appendChild(card);
    }

    // Populate the family select for the add form with existing families + option to create new
    const select = document.getElementById('rlModelSelect');
    select.innerHTML = '<option value="">Select family...</option>';
    familyNames.forEach(f => {
        const opt = document.createElement('option');
        opt.value = f; opt.textContent = f;
        select.appendChild(opt);
    });
    // Also add any models not yet in a family as potential new families
    const allFamilyModels = new Set(familyNames.flatMap(f => families[f].models || []));
    const ungrouped = (rl.all_models || []).filter(m => !allFamilyModels.has(m));
    if (ungrouped.length > 0) {
        const optGroup = document.createElement('optgroup');
        optGroup.label = 'New family from model';
        ungrouped.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m; opt.textContent = m;
            optGroup.appendChild(opt);
        });
        select.appendChild(optGroup);
    }
}

function editRateLimit(family) {
    const rl = allData.ratelimits;
    const fam = (rl.families || {})[family] || {};
    const lim = fam.limits || {};
    document.getElementById('rlModelSelect').value = family;
    document.getElementById('rlRpm').value = lim.rpm || '';
    document.getElementById('rlTpm').value = lim.tpm || '';
    document.getElementById('rlRph').value = lim.rph || '';
    document.getElementById('rlTph').value = lim.tph || '';
    document.getElementById('rateLimitForm').style.display = 'flex';
}

function setupRateLimitListeners() {
    document.getElementById('probeRateLimitsBtn').addEventListener('click', async () => {
        const btn = document.getElementById('probeRateLimitsBtn');
        btn.disabled = true; btn.textContent = 'Probing...';
        try {
            const resp = await fetch('/api/ratelimits/probe', { method: 'POST' });
            const data = await resp.json();
            if (data.error) { showToast(data.error, 'error'); }
            else {
                showToast('Rate limits updated from provider APIs', 'success');
                allData.ratelimits = await fetch('/api/ratelimits').then(r => r.json());
                renderRateLimits();
            }
        } catch(e) { showToast('Probe failed: ' + e.message, 'error'); }
        finally { btn.disabled = false; btn.textContent = 'Re-probe APIs'; }
    });
    document.getElementById('addRateLimitBtn').addEventListener('click', () => {
        document.getElementById('rlModelSelect').value = '';
        document.getElementById('rlRpm').value = '';
        document.getElementById('rlTpm').value = '';
        document.getElementById('rlRph').value = '';
        document.getElementById('rlTph').value = '';
        document.getElementById('rateLimitForm').style.display = 'flex';
    });
    document.getElementById('rlCancelBtn').addEventListener('click', () => {
        document.getElementById('rateLimitForm').style.display = 'none';
    });
    document.getElementById('rlSaveBtn').addEventListener('click', async () => {
        const family = document.getElementById('rlModelSelect').value;
        if (!family) { showToast('Select a family', 'error'); return; }
        const rpm = parseInt(document.getElementById('rlRpm').value) || 0;
        const tpm = parseInt(document.getElementById('rlTpm').value) || 0;
        const rph = parseInt(document.getElementById('rlRph').value) || 0;
        const tph = parseInt(document.getElementById('rlTph').value) || 0;

        // If this is a new family (from ungrouped model), set models list
        const rl = allData.ratelimits;
        const isExisting = rl.families && rl.families[family];
        const models = isExisting ? undefined : [family];

        try {
            const resp = await fetch('/api/ratelimits', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ family, rpm, tpm, rph, tph, models }),
            });
            const data = await resp.json();
            if (data.error) { showToast(data.error, 'error'); return; }
            showToast(`Rate limits updated for ${family}`, 'success');
            document.getElementById('rateLimitForm').style.display = 'none';
            allData.ratelimits = await fetch('/api/ratelimits').then(r => r.json());
            renderRateLimits();
        } catch(e) { showToast('Failed: ' + e.message, 'error'); }
    });
}

// ============================================================================
// SPEND LIMITS
// ============================================================================
function renderSpendLimits() {
    const sl = allData.spendlimits;
    if (!sl) return;
    const container = document.getElementById('spendLimitContainer');
    container.innerHTML = '';

    const entries = sl.providers || {};
    const keys = Object.keys(entries).sort();

    if (keys.length === 0) {
        container.innerHTML = '<div style="color:var(--text-secondary);font-size:12px;padding:8px;">No spend limits configured. Click "+ Add Spend Limit" to set provider cost caps.</div>';
        return;
    }

    // Group by provider for multi-project display
    const grouped = {};
    for (const key of keys) {
        const entry = entries[key];
        const providerName = entry.provider || key;
        if (!grouped[providerName]) grouped[providerName] = [];
        grouped[providerName].push({ key, entry });
    }

    for (const [providerName, items] of Object.entries(grouped).sort()) {
        // If multiple items under one provider, show a provider header
        if (items.length > 1 || items[0].entry.project) {
            const header = document.createElement('div');
            header.style.cssText = 'font-size:12px;font-weight:600;color:var(--text-secondary);padding:4px 0 2px;grid-column:1/-1;display:flex;align-items:center;gap:6px;';
            header.innerHTML = `<span class="provider-dot" style="background:${getProviderColor(providerName)};width:8px;height:8px;"></span>${providerName}`;
            container.appendChild(header);
        }

        for (const { key, entry } of items) {
            const card = document.createElement('div');
            card.className = 'rate-limit-model-card';

            const displayName = entry.project || providerName;
            const modelsList = (entry.models || []).join(', ');
            const modelsSubtitle = modelsList ? `<div class="rl-family-models">${modelsList}</div>` : '';

            let html = `<div class="rl-model-name">${displayName}</div>${modelsSubtitle}<div class="rl-meters">`;

            if (entry.daily_limit) {
                const used = entry.usage_daily || 0;
                const pct = Math.min(100, (used / entry.daily_limit) * 100);
                const cls = pct >= 90 ? 'rl-critical' : pct >= 70 ? 'rl-warn' : 'rl-ok';
                html += `<div class="rl-meter">
                    <div class="rl-meter-label">Daily</div>
                    <div class="rl-meter-bar"><div class="rl-meter-fill ${cls}" style="width:${pct}%"></div></div>
                    <div class="rl-meter-value">$${used.toFixed(2)} / $${entry.daily_limit.toFixed(2)}</div>
                </div>`;
            }

            if (entry.monthly_limit) {
                const used = entry.usage_period || 0;
                const pct = Math.min(100, (used / entry.monthly_limit) * 100);
                const cls = pct >= 90 ? 'rl-critical' : pct >= 70 ? 'rl-warn' : 'rl-ok';
                const resetNote = entry.next_reset ? ` (resets ${entry.next_reset})` : '';
                html += `<div class="rl-meter">
                    <div class="rl-meter-label">Monthly</div>
                    <div class="rl-meter-bar"><div class="rl-meter-fill ${cls}" style="width:${pct}%"></div></div>
                    <div class="rl-meter-value">$${used.toFixed(2)} / $${entry.monthly_limit.toFixed(2)}${resetNote}</div>
                </div>`;
            }

            if (!entry.daily_limit && !entry.monthly_limit) {
                html += `<div class="rl-meter">
                    <div class="rl-meter-label" style="color:var(--text-secondary)">No limits set</div>
                </div>`;
            }

            html += '</div>';
            // Pass both provider and project to the edit function
            const editArgs = entry.project ? `'${providerName}','${entry.project}'` : `'${key}'`;
            html += `<button class="ledger-delete-btn rl-edit-btn" onclick="editSpendLimit(${editArgs})" title="Edit spend limits">&#9998;</button>`;
            card.innerHTML = html;
            container.appendChild(card);
        }
    }
}

function editSpendLimit(provider, project) {
    const sl = allData.spendlimits;
    const key = project ? `${provider}/${project}` : provider;
    const entry = (sl.providers || {})[key] || {};
    document.getElementById('slProvider').value = provider;
    document.getElementById('slProject').value = project || '';
    document.getElementById('slDaily').value = entry.daily_limit || '';
    document.getElementById('slMonthly').value = entry.monthly_limit || '';
    document.getElementById('slResetDate').value = entry.reset_date || '';
    document.getElementById('spendLimitForm').style.display = 'flex';
}

function setupSpendLimitListeners() {
    document.getElementById('addSpendLimitBtn').addEventListener('click', () => {
        document.getElementById('slProvider').value = '';
        document.getElementById('slProject').value = '';
        document.getElementById('slDaily').value = '';
        document.getElementById('slMonthly').value = '';
        document.getElementById('slResetDate').value = '';
        document.getElementById('spendLimitForm').style.display = 'flex';
    });
    document.getElementById('slCancelBtn').addEventListener('click', () => {
        document.getElementById('spendLimitForm').style.display = 'none';
    });
    document.getElementById('slSaveBtn').addEventListener('click', async () => {
        const provider = document.getElementById('slProvider').value.trim();
        if (!provider) { showToast('Enter a provider name', 'error'); return; }
        const project = document.getElementById('slProject').value.trim() || undefined;
        const daily = parseFloat(document.getElementById('slDaily').value) || 0;
        const monthly = parseFloat(document.getElementById('slMonthly').value) || 0;
        const reset_date = document.getElementById('slResetDate').value || null;

        const body = { provider, daily, monthly, reset_date };
        if (project) body.project = project;
        const label = project ? `${provider}/${project}` : provider;

        try {
            const resp = await fetch('/api/spendlimits', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (data.error) { showToast(data.error, 'error'); return; }
            showToast(`Spend limits updated for ${label}`, 'success');
            document.getElementById('spendLimitForm').style.display = 'none';
            allData.spendlimits = await fetch('/api/spendlimits').then(r => r.json());
            renderSpendLimits();
        } catch(e) { showToast('Failed: ' + e.message, 'error'); }
    });
}

// ============================================================================
// CALL LOG
// ============================================================================
function renderCallLog() {
    let calls = allData.calls.calls;
    if (currentSort.field) {
        calls = [...calls].sort((a,b) => {
            const av=a[currentSort.field], bv=b[currentSort.field];
            const c = av<bv?-1:av>bv?1:0;
            return currentSort.order==='asc'?c:-c;
        });
    }

    const tbody = document.getElementById('callLogBody');
    tbody.innerHTML = '';
    calls.forEach(call => {
        const row = document.createElement('tr');
        const pColor = getProviderColor(call.provider);
        row.innerHTML = `
            <td>${formatCallTime(call.timestamp)}</td>
            <td><span class="provider-dot" style="background:${pColor}"></span>${call.provider}</td>
            <td>${call.model}</td>
            <td>${call.tokens_total.toLocaleString()}</td>
            <td>$${call.cost_total.toFixed(6)}</td>
            <td><span class="badge ${call.is_error?'error':'success'}">${call.stop_reason}</span></td>
            <td>${(call.tool_names||[]).join(', ')}</td>
        `;
        tbody.appendChild(row);
    });

    document.querySelectorAll('th.sortable').forEach(th => {
        th.classList.remove('sorted-asc','sorted-desc');
        if (th.dataset.field===currentSort.field) th.classList.add(`sorted-${currentSort.order}`);
    });

    const totalPages = Math.ceil(allData.calls.total / callsPerPage);
    const pagination = document.getElementById('paginationControls');
    pagination.innerHTML = '';

    // Show more page buttons
    const maxBtns = 10;
    const startP = Math.max(1, currentPage - Math.floor(maxBtns/2));
    const endP = Math.min(totalPages, startP + maxBtns - 1);

    if (currentPage > 1) {
        const prev = document.createElement('button');
        prev.textContent = '<'; prev.addEventListener('click', () => { currentPage--; saveState('page', currentPage); loadFilteredData(); });
        pagination.appendChild(prev);
    }
    for (let p = startP; p <= endP; p++) {
        const btn = document.createElement('button');
        btn.textContent = p; btn.className = p===currentPage?'active':'';
        btn.addEventListener('click', () => { currentPage=p; saveState('page', currentPage); loadFilteredData(); });
        pagination.appendChild(btn);
    }
    if (currentPage < totalPages) {
        const next = document.createElement('button');
        next.textContent = '>'; next.addEventListener('click', () => { currentPage++; saveState('page', currentPage); loadFilteredData(); });
        pagination.appendChild(next);
    }
}

// ============================================================================
// UTILITIES
// ============================================================================
function destroyChart(name) { if (chartInstances[name]) { chartInstances[name].destroy(); delete chartInstances[name]; } }

function showToast(message, type='info') {
    const c = document.getElementById('toastContainer');
    const t = document.createElement('div');
    t.className = `toast ${type}`; t.textContent = message;
    c.appendChild(t);
    setTimeout(() => t.remove(), 5000);
}

