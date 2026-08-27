const state = {
  payload: null,
  qualified: [],
  installPrompt: null,
};

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

const escapeHTML = value => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#039;');

const number = (value, digits = 2) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return parsed.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

const signed = (value, digits = 2, suffix = '') => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return `${parsed >= 0 ? '+' : ''}${number(parsed, digits)}${suffix}`;
};

const ratio = value => Number.isFinite(Number(value)) ? Number(value).toFixed(3) : '—';

function extensionLabel(row) {
  if (Number(row.cur_ret_pct) >= 25) return { text: 'Very extended', className: 'warning' };
  if (Number(row.close_vs_sma50_pct) >= 12) return { text: 'Extended vs SMA50', className: 'warning' };
  if (Number(row.cur_ret_pct) >= 15) return { text: 'Momentum extended', className: 'warning' };
  return { text: 'Controlled extension', className: '' };
}

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('visible');
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove('visible'), 2800);
}

function setLoading(loading) {
  $('#loading-overlay').classList.toggle('done', !loading);
}

function candidateCard(row, rank) {
  const extension = extensionLabel(row);
  const ticker = escapeHTML(row.ticker);
  const tvSymbol = encodeURIComponent(row.ticker);
  return `
    <article class="candidate-card">
      <div class="card-top">
        <div>
          <div class="ticker-line"><span class="rank">${rank}</span><strong class="ticker">${ticker}</strong></div>
          <p class="company">${escapeHTML(row.name)}</p>
        </div>
        <div class="conviction"><span>Conviction</span><strong>${ratio(row.conviction)}</strong></div>
      </div>
      <div class="metric-grid">
        <div class="metric"><span>20d signal</span><strong class="positive">${signed(row.signal, 3)}</strong></div>
        <div class="metric"><span>25d check</span><strong class="positive">${signed(row.signal25, 3)}</strong></div>
        <div class="metric"><span>20d return</span><strong>${signed(row.cur_ret_pct, 1, '%')}</strong></div>
        <div class="metric"><span>Transitions</span><strong>${number(row.n, 0)}</strong></div>
      </div>
      <div class="card-foot">
        <div class="badges">
          <span class="badge">Bull ${number(row.bars_in_regime, 0)} bars</span>
          <span class="badge">SMA spread ${signed(row.sma_spread_pct, 1, '%')}</span>
          <span class="badge ${extension.className}">${extension.text}</span>
        </div>
        <div class="card-actions">
          <button type="button" data-detail="${ticker}">Details</button>
          <a href="https://www.tradingview.com/chart/?symbol=${tvSymbol}" target="_blank" rel="noopener">TradingView ↗</a>
        </div>
      </div>
    </article>`;
}

function renderQualified() {
  const query = $('#candidate-search').value.trim().toLowerCase();
  const sort = $('#candidate-sort').value;
  let rows = [...state.qualified].filter(row =>
    String(row.ticker).toLowerCase().includes(query) ||
    String(row.name).toLowerCase().includes(query)
  );

  const sorters = {
    conviction: (a, b) => b.conviction - a.conviction,
    signal25: (a, b) => b.signal25 - a.signal25,
    return: (a, b) => b.cur_ret_pct - a.cur_ret_pct,
    sample: (a, b) => b.n - a.n,
    extension: (a, b) => a.close_vs_sma50_pct - b.close_vs_sma50_pct,
  };
  rows.sort(sorters[sort] || sorters.conviction);

  const list = $('#qualified-list');
  if (!rows.length) {
    list.innerHTML = `<div class="empty-state"><strong>${state.qualified.length ? 'No matching ticker' : 'No qualified candidates'}</strong><span>${state.qualified.length ? 'Try another search.' : 'Remaining in cash is a valid systematic outcome.'}</span></div>`;
    return;
  }
  list.innerHTML = rows.map((row, index) => candidateCard(row, index + 1)).join('');
  $$('[data-detail]').forEach(button => button.addEventListener('click', () => openDetail(button.dataset.detail)));
}

function renderNearMisses(rows) {
  const list = $('#near-list');
  if (!rows?.length) {
    list.innerHTML = '<div class="empty-state"><strong>No near-miss data</strong></div>';
    return;
  }
  list.innerHTML = rows.map(row => `
    <article class="near-row">
      <div class="near-symbol"><strong>${escapeHTML(row.ticker)}</strong><span>${escapeHTML(row.name)}</span></div>
      <div class="near-stat"><span>Passed</span><strong>${number(row.pass_count, 0)} / 8</strong></div>
      <div class="near-stat"><span>Conviction</span><strong>${ratio(row.conviction)}</strong></div>
      <div class="failed-badges">${(row.failed_rules || []).map(rule => `<span class="failed-badge">${escapeHTML(rule)}</span>`).join('')}</div>
    </article>`).join('');
}

function renderFunnel(rows, analyzed) {
  $('#funnel-list').innerHTML = (rows || []).map(row => `
    <div class="funnel-row">
      <div class="funnel-label">${escapeHTML(row.label)}</div>
      <div class="funnel-track"><div class="funnel-fill" style="width:${Math.max(1, Math.min(100, row.percent))}%"></div></div>
      <div class="funnel-value"><strong>${number(row.count, 0)}</strong><span>${number(row.percent, 1)}%</span></div>
    </div>`).join('') + `
    <div class="funnel-row">
      <div class="funnel-label">All eight simultaneously</div>
      <div class="funnel-track"><div class="funnel-fill" style="width:${Math.max(1, (state.payload.summary.qualified / analyzed) * 100)}%"></div></div>
      <div class="funnel-value"><strong>${number(state.payload.summary.qualified, 0)}</strong><span>${number((state.payload.summary.qualified / analyzed) * 100, 1)}%</span></div>
    </div>`;
}

function renderRules(rules) {
  $('#rule-grid').innerHTML = (rules || []).map(rule => `
    <div class="rule-item"><span class="rule-number">${rule.number}</span><span>${escapeHTML(rule.label)}</span></div>
  `).join('');
}

function renderPayload(payload) {
  state.payload = payload;
  state.qualified = payload.qualified || [];
  const meta = payload.meta || {};
  const summary = payload.summary || {};

  $('#session-date').textContent = meta.completed_session || '—';
  $('#source-label').textContent = meta.constituent_source || '—';
  const generated = meta.generated_at_utc ? new Date(meta.generated_at_utc) : null;
  $('#generated-time').textContent = generated && !Number.isNaN(generated.valueOf())
    ? generated.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
    : '—';
  $('#scan-status').innerHTML = '<span></span> Scan completed';
  $('#scan-status').classList.remove('error');

  $('#qualified-count').textContent = number(summary.qualified, 0);
  $('#analyzed-count').textContent = number(summary.analyzed, 0);
  $('#coverage-label').textContent = `of ${number(summary.universe, 0)} securities`;
  $('#bull-count').textContent = number(summary.bull_regime, 0);
  $('#sma-count').textContent = number(summary.sma_uptrend, 0);

  const notice = $('#qualified-notice');
  if (summary.unavailable) {
    notice.textContent = `${summary.unavailable} symbols were unavailable or had insufficient history: ${(payload.unavailable_symbols || []).join(', ')}`;
    notice.classList.add('visible');
  } else {
    notice.classList.remove('visible');
  }

  renderQualified();
  renderNearMisses(payload.near_misses || []);
  renderFunnel(payload.funnel || [], summary.analyzed || 1);
  renderRules(payload.rules || []);
}

function openDetail(ticker) {
  const row = state.qualified.find(item => item.ticker === ticker);
  if (!row) return;
  const extension = extensionLabel(row);
  $('#dialog-content').innerHTML = `
    <div class="detail-title"><h2>${escapeHTML(row.ticker)}</h2><p>${escapeHTML(row.name)}</p></div>
    <div class="detail-score"><span>Markov conviction<br>Ranked signal quality, not certainty</span><strong>${ratio(row.conviction)}</strong></div>
    <div class="detail-grid">
      <div class="detail-cell"><span>Close</span><strong>${number(row.close, 2)}</strong></div>
      <div class="detail-cell"><span>20-day return</span><strong>${signed(row.cur_ret_pct, 2, '%')}</strong></div>
      <div class="detail-cell"><span>P(Bull)</span><strong>${ratio(row.p_bull)}</strong></div>
      <div class="detail-cell"><span>P(Bear)</span><strong>${ratio(row.p_bear)}</strong></div>
      <div class="detail-cell"><span>25-day signal</span><strong>${signed(row.signal25, 3)}</strong></div>
      <div class="detail-cell"><span>Transitions</span><strong>${number(row.n, 0)}</strong></div>
      <div class="detail-cell"><span>SMA50</span><strong>${number(row.sma50, 2)}</strong></div>
      <div class="detail-cell"><span>SMA200</span><strong>${number(row.sma200, 2)}</strong></div>
      <div class="detail-cell"><span>SMA spread</span><strong>${signed(row.sma_spread_pct, 2, '%')}</strong></div>
      <div class="detail-cell"><span>Above SMA50</span><strong>${signed(row.close_vs_sma50_pct, 2, '%')}</strong></div>
      <div class="detail-cell"><span>Reference stop</span><strong>${number(row.reference_stop, 2)}</strong></div>
      <div class="detail-cell"><span>Reference target</span><strong>${number(row.reference_target, 2)}</strong></div>
      <div class="detail-cell"><span>Reward/risk</span><strong>${number(row.reward_risk, 2)}R</strong></div>
      <div class="detail-cell"><span>Extension</span><strong>${escapeHTML(extension.text)}</strong></div>
    </div>
    <p class="detail-disclaimer">Reference levels use 2×ATR from the completed close. They are not executable orders. Confirm the current daily chart and recalculate risk from the actual entry.</p>`;
  $('#detail-dialog').showModal();
}

async function fetchJSON(path) {
  const response = await fetch(`${path}${path.includes('?') ? '&' : '?'}v=${Date.now()}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

async function loadHistoryIndex() {
  try {
    const history = await fetchJSON('data/history/index.json');
    const select = $('#history-select');
    select.innerHTML = '<option value="latest">Latest scan</option>' + history.map(item =>
      `<option value="${escapeHTML(item.file)}">${escapeHTML(item.date)} · ${number(item.qualified_count, 0)} qualified</option>`
    ).join('');
  } catch (error) {
    console.warn('History unavailable', error);
  }
}

async function loadData(source = 'latest') {
  setLoading(true);
  try {
    const path = source === 'latest' ? 'data/latest.json' : `data/history/${source}`;
    const payload = await fetchJSON(path);
    renderPayload(payload);
    localStorage.setItem('northstar-last-scan', JSON.stringify(payload));
    showToast(source === 'latest' ? 'Latest completed scan loaded' : `Loaded scan ${payload.meta.completed_session}`);
  } catch (error) {
    console.error(error);
    const cached = localStorage.getItem('northstar-last-scan');
    if (cached) {
      renderPayload(JSON.parse(cached));
      $('#scan-status').innerHTML = '<span></span> Offline cached result';
      $('#scan-status').classList.add('error');
      showToast('Network unavailable — showing the last saved scan');
    } else {
      $('#scan-status').innerHTML = '<span></span> Unable to load scan';
      $('#scan-status').classList.add('error');
      $('#qualified-list').innerHTML = '<div class="empty-state"><strong>Scan data unavailable</strong><span>Run the GitHub workflow or refresh this page.</span></div>';
    }
  } finally {
    setLoading(false);
  }
}

function bindUI() {
  $$('.tab').forEach(tab => tab.addEventListener('click', () => {
    $$('.tab').forEach(item => item.classList.toggle('active', item === tab));
    $$('.panel').forEach(panel => panel.classList.toggle('active', panel.id === `panel-${tab.dataset.tab}`));
  }));
  $('#candidate-search').addEventListener('input', renderQualified);
  $('#candidate-sort').addEventListener('change', renderQualified);
  $('#reload-button').addEventListener('click', () => loadData($('#history-select').value));
  $('#history-select').addEventListener('change', event => loadData(event.target.value));
  $('#dialog-close').addEventListener('click', () => $('#detail-dialog').close());
  $('#detail-dialog').addEventListener('click', event => {
    if (event.target === $('#detail-dialog')) $('#detail-dialog').close();
  });

  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    state.installPrompt = event;
    $('#install-button').classList.remove('hidden');
  });
  $('#install-button').addEventListener('click', async () => {
    if (!state.installPrompt) return;
    state.installPrompt.prompt();
    await state.installPrompt.userChoice;
    state.installPrompt = null;
    $('#install-button').classList.add('hidden');
  });
}

async function init() {
  bindUI();
  await Promise.all([loadData(), loadHistoryIndex()]);
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(error => console.warn('Service worker failed', error));
  }
}

document.addEventListener('DOMContentLoaded', init);
