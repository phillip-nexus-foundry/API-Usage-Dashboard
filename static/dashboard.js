// API Usage Dashboard - Vanilla JS

const COLORS_PALETTE = [
    '#58a6ff','#3fb950','#f85149','#d29922','#bc8ef6','#39d2c0','#f0e443','#ff6b9d',
];

// Stable provider-to-color map so colors are consistent across all charts
let providerColorMap = {};
function getProviderColor(provider) {
    if (!providerColorMap[provider]) {
        const idx = Object.keys(providerColorMap).length;
        providerColorMap[provider] = COLORS_PALETTE[idx % COLORS_PALETTE.length];
    }
    return providerColorMap[provider];
}

let allData = { summary:null, timeseries:null, calls:null, models:null, tools:null, balance:null, evals:null, costDaily:null, costProjection:null };
let unfilteredMeta = { providers:[], models:[] };
let chartInstances = {};
let currentSort = { field:null, order:'desc' };
let currentPage = 1;
let callsPerPage = 50;
let useUTC = localStorage.getItem('useUTC') === 'true';
let hiddenProviders = JSON.parse(localStorage.getItem('hiddenProviders') || '[]');

document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    loadData();
    setInterval(loadData, 60000);
});

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
        localStorage.setItem('useUTC', useUTC);
        renderDashboard();
    });
    document.getElementById('utcToggle').checked = useUTC;
    document.getElementById('filterInterval').addEventListener('change', () => loadFilteredData());
    document.getElementById('filterProvider').addEventListener('change', () => { currentPage = 1; loadFilteredData(); });
    document.getElementById('filterModel').addEventListener('change', () => { currentPage = 1; loadFilteredData(); });
    document.getElementById('balanceSortSelect').addEventListener('change', () => renderBalance());
    document.getElementById('perPageSelect').addEventListener('change', e => {
        callsPerPage = parseInt(e.target.value);
        currentPage = 1;
        loadFilteredData();
    });
    document.getElementById('applyRangeFilter').addEventListener('click', () => { currentPage = 1; loadFilteredData(); });
    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const field = th.dataset.field;
            if (currentSort.field === field) currentSort.order = currentSort.order === 'asc' ? 'desc' : 'asc';
            else { currentSort.field = field; currentSort.order = 'desc'; }
            renderCallLog();
        });
    });
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

async function loadData() {
    try {
        const interval = document.getElementById('filterInterval').value;
        const [summary,timeseries,calls,models,tools,balance,evals,costDaily,costProjection] = await Promise.all([
            fetch('/api/summary').then(r=>r.json()),
            fetch(`/api/timeseries?interval=${interval}`).then(r=>r.json()),
            fetch(`/api/calls?page=1&per_page=${callsPerPage}`).then(r=>r.json()),
            fetch('/api/models').then(r=>r.json()),
            fetch('/api/tools').then(r=>r.json()),
            fetch('/api/balance').then(r=>r.json()),
            fetch('/api/evals').then(r=>r.json()),
            fetch('/api/cost/daily').then(r=>r.json()),
            fetch('/api/cost/projection').then(r=>r.json()),
        ]);
        allData = {summary,timeseries,calls,models,tools,balance,evals,costDaily,costProjection};
        unfilteredMeta.providers = (summary.by_provider||[]).map(p=>p.provider);
        unfilteredMeta.models = (models.models||[]).map(m=>m.model);
        // Build stable color map from providers
        unfilteredMeta.providers.forEach(p => getProviderColor(p));
        renderDashboard();
    } catch(e) { showToast('Load failed: '+e.message,'error'); }
}

async function loadFilteredData() {
    try {
        const fq = getFilterParams();
        const rq = getRangeParams();
        const interval = document.getElementById('filterInterval').value;
        const [summary,timeseries,calls,models,tools,costDaily,costProjection] = await Promise.all([
            fetch(`/api/summary?_=1${fq}`).then(r=>r.json()),
            fetch(`/api/timeseries?interval=${interval}${fq}`).then(r=>r.json()),
            fetch(`/api/calls?page=${currentPage}&per_page=${callsPerPage}${fq}${rq}`).then(r=>r.json()),
            fetch(`/api/models?_=1${fq}`).then(r=>r.json()),
            fetch(`/api/tools?_=1${fq}`).then(r=>r.json()),
            fetch(`/api/cost/daily?_=1${fq}`).then(r=>r.json()),
            fetch(`/api/cost/projection?_=1${fq}`).then(r=>r.json()),
        ]);
        allData.summary=summary; allData.timeseries=timeseries; allData.calls=calls;
        allData.models=models; allData.tools=tools; allData.costDaily=costDaily; allData.costProjection=costProjection;
        renderDashboard();
    } catch(e) { showToast('Filter failed: '+e.message,'error'); }
}

function renderDashboard() {
    populateFilters();
    renderBalance();
    renderKPIs();
    renderCharts();
    renderCallLog();
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
function renderBalance() {
    const b = allData.balance;
    if (!b) return;
    const container = document.getElementById('balanceTracker');
    container.innerHTML = '';

    // Build entries array with sort data
    let entries = Object.entries(b).map(([name, data]) => ({
        name, data,
        usage: data.usage_calls || 0,
        remaining: data.remaining !== undefined ? data.remaining : -999,
    }));

    // Sort based on selector
    const sortMode = document.getElementById('balanceSortSelect').value;
    if (sortMode === 'usage-desc') entries.sort((a,b) => b.usage - a.usage);
    else if (sortMode === 'usage-asc') entries.sort((a,b) => a.usage - b.usage);
    else if (sortMode === 'balance-desc') entries.sort((a,b) => b.remaining - a.remaining);
    else if (sortMode === 'balance-asc') entries.sort((a,b) => a.remaining - b.remaining);

    for (const {name, data} of entries) {
        const isHidden = hiddenProviders.includes(name);
        const card = document.createElement('div');
        card.className = `balance-card ${isHidden ? 'balance-hidden' : ''}`;

        const status = data.status || 'unknown';
        const statusLabel = status==='ok'?'OK':status==='warn'?'LOW':status==='critical'?'CRITICAL':status.replace(/_/g,' ').toUpperCase();
        const colorClass = ['ok','warn','critical'].includes(status) ? status : 'unknown';
        const hasLedger = Array.isArray(data.ledger) && data.ledger.length > 0;
        const hasApiKey = data.api_note || (!hasLedger && data.remaining !== undefined);

        let remainingText = '—', detailText = '';
        if (data.remaining !== undefined) {
            remainingText = `$${data.remaining.toFixed(2)}`;
            if (data.total_deposits !== undefined) {
                const personal = data.personal_invested !== undefined ? data.personal_invested : data.total_deposits;
                detailText = `Invested: $${personal.toFixed(2)} | Spent: $${(data.cumulative_cost||0).toFixed(2)}`;
            }
        } else if (data.message) {
            remainingText = data.message;
        }

        // Usage stats line
        let usageLine = '';
        if (data.usage_calls) usageLine = `${data.usage_calls.toLocaleString()} calls | $${(data.usage_cost||0).toFixed(2)} cost`;

        let html = `
            <div class="balance-card-header">
                <div style="display:flex;align-items:center;gap:6px;">
                    <span class="provider-dot" style="background:${getProviderColor(name)}"></span>
                    <span class="balance-provider-name">${name}</span>
                </div>
                <div style="display:flex;align-items:center;gap:6px;">
                    <span class="balance-status-badge status-${status}">${statusLabel}</span>
                    <span class="balance-toggle" onclick="toggleProvider('${name}',this)" title="${isHidden?'Show':'Hide'}">${isHidden?'+':'-'}</span>
                </div>
            </div>`;

        if (!isHidden) {
            html += `<div class="balance-remaining color-${colorClass}">${remainingText}</div>`;
            if (detailText) html += `<div class="balance-detail">${detailText}</div>`;
            if (usageLine) html += `<div class="balance-detail">${usageLine}</div>`;

            if (hasLedger) {
                const ledgerId = `ledger-${name}`;
                html += `<div class="balance-ledger">
                    <div class="balance-ledger-toggle" onclick="document.getElementById('${ledgerId}').classList.toggle('open');this.textContent=this.textContent.includes('+')?'- Hide deposits':'+ Deposits (${data.ledger.length})'">+ Deposits (${data.ledger.length})</div>
                    <div class="balance-ledger-entries" id="${ledgerId}">`;
                for (const e of data.ledger) {
                    const cls = e.is_voucher ? 'balance-ledger-voucher' : '';
                    html += `<div class="balance-ledger-entry ${cls}">
                        <span class="balance-ledger-date">${e.date}</span>
                        <span class="balance-ledger-note">${e.note||''}</span>
                        <span class="balance-ledger-amount">${e.is_voucher?'':'+'}\$${(e.amount||0).toFixed(2)}${e.is_voucher?' (voucher)':''}</span>
                    </div>`;
                }
                html += `</div></div>`;
                // Only show topup form for ledger-only providers (not API-auto)
                if (!hasApiKey) {
                    html += `<div class="balance-topup"><div class="topup-fields">
                        <input type="number" class="topup-input" placeholder="$" step="0.01" min="0.01" id="topup-amount-${name}">
                        <input type="text" class="topup-input" placeholder="Note" id="topup-note-${name}">
                        <button class="btn primary topup-btn" onclick="submitTopup('${name}',this)">Add</button>
                    </div></div>`;
                }
            }
        }

        card.innerHTML = html;
        container.appendChild(card);
    }
}

function toggleProvider(name, el) {
    const idx = hiddenProviders.indexOf(name);
    if (idx >= 0) hiddenProviders.splice(idx, 1);
    else hiddenProviders.push(name);
    localStorage.setItem('hiddenProviders', JSON.stringify(hiddenProviders));
    renderBalance();
}

async function submitTopup(provider, btn) {
    const amountInput = document.getElementById(`topup-amount-${provider}`);
    const noteInput = document.getElementById(`topup-note-${provider}`);
    const amount = parseFloat(amountInput.value);
    if (!amount || amount <= 0) { showToast('Enter a valid amount','error'); return; }
    btn.disabled=true; btn.textContent='...';
    try {
        const resp = await fetch('/api/balance/topup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider,amount,note:noteInput.value||''})});
        const data = await resp.json();
        if (data.error) showToast(data.error,'error');
        else {
            showToast(`Added $${amount.toFixed(2)} to ${provider}`,'success');
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
function renderKPIs() {
    const s = allData.summary;
    document.getElementById('kpiCalls').textContent = s.total_calls.toLocaleString();
    document.getElementById('kpiCallsMeta').textContent = `${s.session_count} sessions`;
    document.getElementById('kpiCost').textContent = `$${s.total_cost.toFixed(2)}`;
    document.getElementById('kpiCostMeta').textContent = s.total_calls > 0 ? `$${(s.total_cost/s.total_calls).toFixed(6)}/call` : '';
    const ep = (s.error_rate*100).toFixed(2);
    document.getElementById('kpiErrorRate').textContent = `${ep}%`;
    const eb = ep > 5 ? 'error' : ep > 2 ? 'warn' : 'success';
    document.getElementById('kpiErrorRateMeta').innerHTML = `<span class="badge ${eb}">${s.error_count} errors</span>`;
    const models = allData.models.models;
    const avgCH = models.length > 0 ? (models.reduce((s,m)=>s+m.avg_cache_hit_ratio,0)/models.length*100).toFixed(1) : '0.0';
    document.getElementById('kpiCacheHit').textContent = `${avgCH}%`;
    document.getElementById('kpiCacheHitMeta').textContent = 'Avg across models';
    document.getElementById('kpiSessions').textContent = s.session_count.toLocaleString();
    document.getElementById('kpiSessionsMeta').textContent = `${(s.total_calls/Math.max(s.session_count,1)).toFixed(1)} calls/session`;
}

// ============================================================================
// TIME FORMATTING
// ============================================================================
function formatTimeLabel(ms) {
    const d = new Date(ms);
    const interval = document.getElementById('filterInterval').value;
    if (interval === 'minute') {
        return d.toLocaleString('en-US', {hour:'numeric',minute:'2-digit',hour12:true}).replace(/\s/g,'');
    }
    if (interval === 'hour') {
        return d.toLocaleString('en-US', {hour:'numeric',hour12:true}).replace(/\s/g,'');
    }
    if (interval === 'day') {
        return d.toLocaleString('en-US', {month:'short',day:'numeric'});
    }
    if (interval === 'week') {
        return 'Wk ' + d.toLocaleString('en-US', {month:'short',day:'numeric'});
    }
    if (interval === 'month') {
        const yr = String(d.getFullYear()).slice(2);
        return d.toLocaleString('en-US', {month:'short'}) + " '" + yr;
    }
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

function renderTokenTimeSeries() {
    const data = allData.timeseries.data;
    const ctx = document.getElementById('tokenTimeSeriesChart');
    destroyChart('tokenTimeSeries');
    chartInstances['tokenTimeSeries'] = new Chart(ctx, {
        type:'line',
        data:{
            labels: data.map(d => formatTimeLabel(d.timestamp)),
            datasets:[{ label:'Tokens', data:data.map(d=>d.tokens), borderColor:'#58a6ff', backgroundColor:'rgba(88,166,255,0.1)', tension:0.3, fill:true }],
        },
        options:{
            responsive:true, maintainAspectRatio:false,
            plugins:{ legend:darkLegend },
            scales:{ y:{ticks:darkTick,grid:darkGrid}, x:{ticks:{color:'#8b949e',maxRotation:45,autoSkip:true,maxTicksLimit:20},grid:darkGrid} },
        },
    });
}

function renderCostTimeSeries() {
    const data = allData.timeseries.data;
    const provCosts = allData.timeseries.provider_costs || {};
    const ctx = document.getElementById('costTimeSeriesChart');
    const labels = data.map(d => formatTimeLabel(d.timestamp));
    const timestamps = data.map(d => d.timestamp);

    // Build stacked datasets per provider
    const providers = Object.keys(provCosts);
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
            plugins:{ legend:darkLegend, tooltip:{ callbacks:{ label: ctx => `${ctx.dataset.label}: $${ctx.parsed.y.toFixed(4)}` } } },
            scales:{
                y:{ stacked:true, ticks:darkTick, grid:darkGrid },
                x:{ stacked:true, ticks:{color:'#8b949e',maxRotation:45,autoSkip:true,maxTicksLimit:20}, grid:darkGrid },
            },
        },
    });
}

function renderByProvider() {
    const data = allData.summary.by_provider;
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
    const data = allData.summary.by_model.slice(0,8);
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
                tooltip:{ callbacks:{ label: ctx => {
                    const d = data[ctx.dataIndex];
                    return `${d.calls} calls | $${d.cost.toFixed(2)} | ${(d.tokens||0).toLocaleString()} tokens`;
                }}}
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
            plugins:{ legend:darkLegend },
            scales:{ r:{ ticks:darkTick, grid:{color:'#30363d'}, suggestedMin:0, suggestedMax:100 } },
        },
    });
}

function renderTopTools() {
    const tools = allData.tools.tools.slice(0,15);
    const ctx = document.getElementById('topToolsChart');
    destroyChart('topTools');
    chartInstances['topTools'] = new Chart(ctx, {
        type:'bar',
        data:{
            labels: tools.map(t=>t.tool),
            datasets:[{ label:'Usage', data:tools.map(t=>t.count), backgroundColor:tools.map((_,i)=>COLORS_PALETTE[i%COLORS_PALETTE.length]) }],
        },
        options:{
            responsive:true, maintainAspectRatio:false, indexAxis:'y',
            plugins:{
                legend:{ display:false },
                datalabels: false,
            },
            scales:{
                x:{ ticks:darkTick, grid:darkGrid },
                y:{ ticks:{ color:'#c9d1d9', font:{size:11} }, grid:{drawBorder:false} },
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
        prev.textContent = '<'; prev.addEventListener('click', () => { currentPage--; loadFilteredData(); });
        pagination.appendChild(prev);
    }
    for (let p = startP; p <= endP; p++) {
        const btn = document.createElement('button');
        btn.textContent = p; btn.className = p===currentPage?'active':'';
        btn.addEventListener('click', () => { currentPage=p; loadFilteredData(); });
        pagination.appendChild(btn);
    }
    if (currentPage < totalPages) {
        const next = document.createElement('button');
        next.textContent = '>'; next.addEventListener('click', () => { currentPage++; loadFilteredData(); });
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
