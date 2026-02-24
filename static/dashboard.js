// API Usage Dashboard - Vanilla JS
// Fetches all endpoints and renders Chart.js panels + table

const CHART_COLORS = {
    blue: '#58a6ff',
    green: '#3fb950',
    red: '#f85149',
    orange: '#d29922',
    purple: '#bc8ef6',
    cyan: '#39d2c0',
    yellow: '#f0e443',
    pink: '#ff6b9d',
};

// High-contrast palette — each color is visually distinct on dark backgrounds
const COLORS_PALETTE = [
    '#58a6ff',  // blue
    '#3fb950',  // green
    '#f85149',  // red
    '#d29922',  // orange
    '#bc8ef6',  // purple
    '#39d2c0',  // teal
    '#f0e443',  // yellow
    '#ff6b9d',  // pink
];

let allData = {
    summary: null,
    timeseries: null,
    calls: null,
    models: null,
    tools: null,
    balance: null,
    evals: null,
    costDaily: null,
    costProjection: null,
};

// Unfiltered data for populating filter dropdowns (always shows all options)
let unfilteredMeta = { providers: [], models: [] };

let chartInstances = {};
let currentSort = { field: null, order: 'desc' };
let currentPage = 1;
let callsPerPage = 50;
let useUTC = localStorage.getItem('useUTC') === 'true' || false;

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    loadData();
    setInterval(() => {
        loadData();
    }, 60000); // Auto-refresh every 60 seconds
});

function setupEventListeners() {
    document.getElementById('refreshBtn').addEventListener('click', () => {
        document.getElementById('refreshBtn').disabled = true;
        document.getElementById('refreshBtn').textContent = '⏳ Refreshing...';
        fetch('/api/refresh', { method: 'POST' })
            .then(() => loadData())
            .finally(() => {
                document.getElementById('refreshBtn').disabled = false;
                document.getElementById('refreshBtn').textContent = '🔄 Refresh Now';
            });
    });

    document.getElementById('utcToggle').addEventListener('change', (e) => {
        useUTC = e.target.checked;
        localStorage.setItem('useUTC', useUTC);
        location.reload();
    });
    document.getElementById('utcToggle').checked = useUTC;

    document.getElementById('filterInterval').addEventListener('change', () => {
        loadFilteredData();
    });

    document.getElementById('filterProvider').addEventListener('change', () => {
        currentPage = 1;
        loadFilteredData();
    });

    document.getElementById('filterModel').addEventListener('change', () => {
        currentPage = 1;
        loadFilteredData();
    });

    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const field = th.dataset.field;
            if (currentSort.field === field) {
                currentSort.order = currentSort.order === 'asc' ? 'desc' : 'asc';
            } else {
                currentSort.field = field;
                currentSort.order = 'desc';
            }
            renderCallLog();
        });
    });
}

async function loadData() {
    try {
        const [summary, timeseries, calls, models, tools, balance, evals, costDaily, costProjection] = await Promise.all([
            fetch('/api/summary').then(r => r.json()),
            fetch('/api/timeseries?interval=hour').then(r => r.json()),
            fetch('/api/calls?page=1&per_page=50').then(r => r.json()),
            fetch('/api/models').then(r => r.json()),
            fetch('/api/tools').then(r => r.json()),
            fetch('/api/balance').then(r => r.json()),
            fetch('/api/evals').then(r => r.json()),
            fetch('/api/cost/daily').then(r => r.json()),
            fetch('/api/cost/projection').then(r => r.json()),
        ]);

        allData.summary = summary;
        allData.timeseries = timeseries;
        allData.calls = calls;
        allData.models = models;
        allData.tools = tools;
        allData.balance = balance;
        allData.evals = evals;
        allData.costDaily = costDaily;
        allData.costProjection = costProjection;

        // Save unfiltered provider/model lists for dropdown population
        unfilteredMeta.providers = (summary.by_provider || []).map(p => p.provider);
        unfilteredMeta.models = (models.models || []).map(m => m.model);

        renderDashboard();
    } catch (error) {
        showToast('Failed to load dashboard data: ' + error.message, 'error');
    }
}

function getFilterParams() {
    const provider = document.getElementById('filterProvider').value;
    const model = document.getElementById('filterModel').value;
    let qs = '';
    if (provider) qs += `&provider=${encodeURIComponent(provider)}`;
    if (model) qs += `&model=${encodeURIComponent(model)}`;
    return qs;
}

async function loadFilteredData() {
    try {
        const fq = getFilterParams();
        const interval = document.getElementById('filterInterval').value;

        const [summary, timeseries, calls, models, tools, costDaily, costProjection] = await Promise.all([
            fetch(`/api/summary?_=1${fq}`).then(r => r.json()),
            fetch(`/api/timeseries?interval=${interval}${fq}`).then(r => r.json()),
            fetch(`/api/calls?page=${currentPage}&per_page=${callsPerPage}${fq}`).then(r => r.json()),
            fetch(`/api/models?_=1${fq}`).then(r => r.json()),
            fetch(`/api/tools?_=1${fq}`).then(r => r.json()),
            fetch(`/api/cost/daily?_=1${fq}`).then(r => r.json()),
            fetch(`/api/cost/projection?_=1${fq}`).then(r => r.json()),
        ]);

        allData.summary = summary;
        allData.timeseries = timeseries;
        allData.calls = calls;
        allData.models = models;
        allData.tools = tools;
        allData.costDaily = costDaily;
        allData.costProjection = costProjection;

        renderDashboard();
    } catch (error) {
        showToast('Failed to load filtered data: ' + error.message, 'error');
    }
}

async function loadTimeseries(interval) {
    try {
        const data = await fetch(`/api/timeseries?interval=${interval}`).then(r => r.json());
        allData.timeseries = data;
        renderCharts();
    } catch (error) {
        showToast('Failed to load timeseries: ' + error.message, 'error');
    }
}

function renderDashboard() {
    populateFilters();
    renderKPIs();
    renderCharts();
    renderCallLog();
}

function populateFilters() {
    const providerSelect = document.getElementById('filterProvider');
    const modelSelect = document.getElementById('filterModel');

    // Preserve current selections
    const currentProvider = providerSelect.value;
    const currentModel = modelSelect.value;

    // Use unfiltered lists so dropdown always shows all options
    const providers = unfilteredMeta.providers;
    const models = unfilteredMeta.models;

    if (providers.length > 0) {
        providerSelect.innerHTML = '<option value="">All Providers</option>';
        providers.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p;
            opt.textContent = p;
            providerSelect.appendChild(opt);
        });
    }

    if (models.length > 0) {
        modelSelect.innerHTML = '<option value="">All Models</option>';
        models.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = m;
            modelSelect.appendChild(opt);
        });
    }

    // Restore selections
    providerSelect.value = currentProvider;
    modelSelect.value = currentModel;
}

function renderKPIs() {
    const s = allData.summary;
    const c = allData.calls;

    // Total Calls
    document.getElementById('kpiCalls').textContent = s.total_calls.toLocaleString();
    document.getElementById('kpiCallsMeta').textContent = `${s.session_count} sessions`;

    // Total Cost
    document.getElementById('kpiCost').textContent = `$${s.total_cost.toFixed(2)}`;
    document.getElementById('kpiCostMeta').textContent = `${(s.total_cost / s.total_calls).toFixed(6)}/call`;

    // Error Rate
    const errorPct = (s.error_rate * 100).toFixed(2);
    document.getElementById('kpiErrorRate').textContent = `${errorPct}%`;
    const errorBadge = errorPct > 5 ? 'error' : errorPct > 2 ? 'warn' : 'success';
    document.getElementById('kpiErrorRateMeta').innerHTML = `<span class="badge ${errorBadge}">${s.error_count} errors</span>`;

    // Cache Hit Ratio
    const avgCacheHit = (allData.models.models.reduce((sum, m) => sum + m.avg_cache_hit_ratio, 0) / Math.max(allData.models.models.length, 1) * 100).toFixed(1);
    document.getElementById('kpiCacheHit').textContent = `${avgCacheHit}%`;
    document.getElementById('kpiCacheHitMeta').textContent = 'Average across models';

    // Sessions
    document.getElementById('kpiSessions').textContent = s.session_count.toLocaleString();
    document.getElementById('kpiSessionsMeta').textContent = `${(s.total_calls / Math.max(s.session_count, 1)).toFixed(1)} calls/session`;
}

function renderCharts() {
    renderTokenTimeSeries();
    renderCostTimeSeries();
    renderByProvider();
    renderByModel();
    renderStopReasons();
    renderBalance();
    renderEvalScores();
    renderTopTools();
    renderProjection();
}

function renderTokenTimeSeries() {
    const data = allData.timeseries.data;
    const ctx = document.getElementById('tokenTimeSeriesChart');

    const labels = data.map(d => formatTime(d.timestamp));
    const tokenData = data.map(d => d.tokens);

    destroyChart('tokenTimeSeries');
    chartInstances['tokenTimeSeries'] = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Tokens',
                    data: tokenData,
                    borderColor: CHART_COLORS.blue,
                    backgroundColor: 'rgba(88, 166, 255, 0.1)',
                    tension: 0.3,
                    fill: true,
                }
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        color: '#c9d1d9',
                        boxWidth: 12,
                    },
                },
            },
            scales: {
                y: {
                    ticks: { color: '#8b949e' },
                    grid: { color: '#30363d', drawBorder: false },
                },
                x: {
                    ticks: { color: '#8b949e' },
                    grid: { color: '#30363d', drawBorder: false },
                },
            },
        },
    });
}

function renderCostTimeSeries() {
    const data = allData.timeseries.data;
    const ctx = document.getElementById('costTimeSeriesChart');

    const labels = data.map(d => formatTime(d.timestamp));
    const costData = data.map(d => d.cost);

    destroyChart('costTimeSeries');
    chartInstances['costTimeSeries'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Cost',
                    data: costData,
                    backgroundColor: CHART_COLORS.green,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#c9d1d9' },
                },
            },
            scales: {
                y: {
                    ticks: { color: '#8b949e' },
                    grid: { color: '#30363d' },
                },
                x: {
                    ticks: { color: '#8b949e' },
                    grid: { color: '#30363d', drawBorder: false },
                },
            },
        },
    });
}

function renderByProvider() {
    const data = allData.summary.by_provider;
    const ctx = document.getElementById('byProviderChart');

    destroyChart('byProvider');
    chartInstances['byProvider'] = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.map(d => d.provider),
            datasets: [{
                data: data.map(d => d.calls),
                backgroundColor: COLORS_PALETTE,
                borderColor: '#161b22',
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#c9d1d9' },
                },
            },
        },
    });
}

function renderByModel() {
    const data = allData.summary.by_model.slice(0, 8);
    const ctx = document.getElementById('byModelChart');

    destroyChart('byModel');
    chartInstances['byModel'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => d.model),
            datasets: [{
                label: 'Calls',
                data: data.map(d => d.calls),
                backgroundColor: data.map((_, i) => COLORS_PALETTE[i % COLORS_PALETTE.length]),
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: {
                    labels: { color: '#c9d1d9' },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#8b949e' },
                    grid: { color: '#30363d' },
                },
                y: {
                    ticks: { color: '#8b949e' },
                    grid: { drawBorder: false },
                },
            },
        },
    });
}

function renderStopReasons() {
    const ctx = document.getElementById('stopReasonsChart');
    const calls = allData.calls.calls;
    
    const reasonMap = {};
    calls.forEach(c => {
        reasonMap[c.stop_reason] = (reasonMap[c.stop_reason] || 0) + 1;
    });

    destroyChart('stopReasons');
    chartInstances['stopReasons'] = new Chart(ctx, {
        type: 'pie',
        data: {
            labels: Object.keys(reasonMap),
            datasets: [{
                data: Object.values(reasonMap),
                backgroundColor: COLORS_PALETTE,
                borderColor: '#161b22',
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#c9d1d9' },
                },
            },
        },
    });
}

function renderBalance() {
    const b = allData.balance;
    const container = document.getElementById('balanceTracker');
    container.innerHTML = '';

    // Dynamically generate a card for each provider returned by the API
    for (const [providerName, data] of Object.entries(b)) {
        const card = document.createElement('div');
        card.className = 'balance-card';

        const status = data.status || 'unknown';
        const statusLabel = status === 'ok' ? 'OK' : status === 'warn' ? 'LOW' : status === 'critical' ? 'CRITICAL' : status.replace(/_/g, ' ').toUpperCase();
        const colorClass = (status === 'ok' || status === 'warn' || status === 'critical') ? status : 'unknown';

        // Has a ledger?
        const hasLedger = Array.isArray(data.ledger) && data.ledger.length > 0;
        // Determine tracking method
        const method = data.api_note ? `Ledger (API: ${data.api_note})` : hasLedger ? 'Ledger' : data.remaining !== undefined ? 'API' : '—';

        // Remaining display
        let remainingText = '—';
        let detailText = '';
        if (data.remaining !== undefined) {
            remainingText = `$${data.remaining.toFixed(2)}`;
            if (data.total_deposits !== undefined) {
                detailText = `Deposited: $${data.total_deposits.toFixed(2)} | Spent: $${(data.cumulative_cost || 0).toFixed(2)}`;
            } else if (data.warn_threshold !== undefined) {
                detailText = `Warn < $${data.warn_threshold} | Critical < $${data.critical_threshold}`;
            }
        } else if (data.message) {
            remainingText = data.message;
        }

        let html = `
            <div class="balance-card-header">
                <div class="balance-provider-name">${providerName}</div>
                <span class="balance-status-badge status-${status}">${statusLabel}</span>
            </div>
            <div class="balance-remaining color-${colorClass}">${remainingText}</div>
            <div class="balance-detail">${detailText}</div>
            <div class="balance-method">${method}</div>
        `;

        // Collapsible ledger history
        if (hasLedger) {
            const ledgerId = `ledger-${providerName}`;
            html += `<div class="balance-ledger">
                <div class="balance-ledger-toggle" onclick="document.getElementById('${ledgerId}').classList.toggle('open'); this.textContent = this.textContent.includes('+') ? '- Hide deposits' : '+ Show deposits (${data.ledger.length})'">+ Show deposits (${data.ledger.length})</div>
                <div class="balance-ledger-entries" id="${ledgerId}">`;
            for (const entry of data.ledger) {
                html += `<div class="balance-ledger-entry">
                    <span class="balance-ledger-date">${entry.date}</span>
                    <span class="balance-ledger-note">${entry.note || ''}</span>
                    <span class="balance-ledger-amount">+$${(entry.amount || 0).toFixed(2)}</span>
                </div>`;
            }
            html += `</div></div>`;

            // Top-up form
            html += `<div class="balance-topup">
                <div class="topup-fields">
                    <input type="number" class="topup-input" placeholder="$" step="0.01" min="0.01" data-provider="${providerName}" id="topup-amount-${providerName}">
                    <input type="text" class="topup-input" placeholder="Note" id="topup-note-${providerName}">
                    <button class="btn primary topup-btn" onclick="submitTopup('${providerName}', this)">Add</button>
                </div>
            </div>`;
        }

        card.innerHTML = html;
        container.appendChild(card);
    }
}

async function submitTopup(provider, btn) {
    const amountInput = document.getElementById(`topup-amount-${provider}`);
    const noteInput = document.getElementById(`topup-note-${provider}`);
    const amount = parseFloat(amountInput.value);

    if (!amount || amount <= 0) {
        showToast('Enter a valid amount', 'error');
        return;
    }

    btn.disabled = true;
    btn.textContent = '...';

    try {
        const resp = await fetch('/api/balance/topup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, amount, note: noteInput.value || '' }),
        });
        const data = await resp.json();

        if (data.error) {
            showToast(data.error, 'error');
        } else {
            showToast(`Added $${amount.toFixed(2)} to ${provider}`, 'success');
            amountInput.value = '';
            noteInput.value = '';
            const balanceData = await fetch('/api/balance').then(r => r.json());
            allData.balance = balanceData;
            renderBalance();
        }
    } catch (err) {
        showToast('Failed: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Add';
    }
}

function renderEvalScores() {
    const evals = allData.evals.evals;
    const ctx = document.getElementById('evalScoresChart');

    const labels = evals.map(e => e.eval_name);
    const scores = evals.map(e => e.score * 100);

    destroyChart('evalScores');
    chartInstances['evalScores'] = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Score',
                data: scores,
                borderColor: CHART_COLORS.purple,
                backgroundColor: 'rgba(188, 142, 246, 0.15)',
                fill: true,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#c9d1d9' },
                },
            },
            scales: {
                r: {
                    ticks: { color: '#8b949e' },
                    grid: { color: '#30363d' },
                },
            },
        },
    });
}

function renderTopTools() {
    const tools = allData.tools.tools.slice(0, 10);
    const ctx = document.getElementById('topToolsChart');

    destroyChart('topTools');
    chartInstances['topTools'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: tools.map(t => t.tool),
            datasets: [{
                label: 'Usage',
                data: tools.map(t => t.count),
                backgroundColor: tools.map((_, i) => COLORS_PALETTE[i % COLORS_PALETTE.length]),
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: {
                    labels: { color: '#c9d1d9' },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#8b949e' },
                    grid: { color: '#30363d' },
                },
                y: {
                    ticks: { color: '#8b949e' },
                    grid: { drawBorder: false },
                },
            },
        },
    });
}

function renderProjection() {
    const p = allData.costProjection;
    document.getElementById('projectionCost').textContent = `$${p.projected_monthly_cost.toFixed(2)}`;
    document.getElementById('projectionNote').textContent = `Based on ${p.days_of_data} days of data (${p.avg_daily_cost.toFixed(2)}/day)`;
}

function renderCallLog() {
    let calls = allData.calls.calls;

    // Sort
    if (currentSort.field) {
        calls = [...calls].sort((a, b) => {
            const aVal = a[currentSort.field];
            const bVal = b[currentSort.field];
            const cmp = aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
            return currentSort.order === 'asc' ? cmp : -cmp;
        });
    }

    // Paginate
    const start = (currentPage - 1) * callsPerPage;
    const paged = calls.slice(start, start + callsPerPage);

    // Render table
    const tbody = document.getElementById('callLogBody');
    tbody.innerHTML = '';

    paged.forEach(call => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${formatTime(call.timestamp)}</td>
            <td>${call.provider}</td>
            <td>${call.model}</td>
            <td>${call.tokens_total.toLocaleString()}</td>
            <td>$${call.cost_total.toFixed(6)}</td>
            <td><span class="badge ${call.is_error ? 'error' : 'success'}">${call.stop_reason}</span></td>
            <td>${call.tool_names.join(', ')}</td>
        `;
        tbody.appendChild(row);
    });

    // Update sort indicators
    document.querySelectorAll('th.sortable').forEach(th => {
        th.classList.remove('sorted-asc', 'sorted-desc');
        if (th.dataset.field === currentSort.field) {
            th.classList.add(`sorted-${currentSort.order}`);
        }
    });

    // Render pagination
    const totalPages = Math.ceil(allData.calls.total / callsPerPage);
    const pagination = document.getElementById('paginationControls');
    pagination.innerHTML = '';

    for (let p = 1; p <= Math.min(totalPages, 5); p++) {
        const btn = document.createElement('button');
        btn.textContent = p;
        btn.className = p === currentPage ? 'active' : '';
        btn.addEventListener('click', () => {
            currentPage = p;
            loadFilteredData();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
        pagination.appendChild(btn);
    }
}

function formatTime(ms) {
    const date = new Date(ms);
    if (useUTC) {
        return date.toISOString().substring(0, 19);
    } else {
        return date.toLocaleString('en-US', { timeZone: undefined });
    }
}

function destroyChart(name) {
    if (chartInstances[name]) {
        chartInstances[name].destroy();
        delete chartInstances[name];
    }
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 5000);
}
