// ローカル リサーチ画面のロジック（app.py の /api/* と通信する）

const STORAGE_KEY = 'rieki-research-conditions-v1';
const SOURCE_LABELS = { yahoo_auctions: 'ヤフオク', mercari_cheap: 'メルカリ安値', sekaist: 'セカスト', vector_park: 'ベクトルパーク', trefac: 'トレファク', rakuma: 'ラクマ', yahoo_flea: 'Yahoo!フリマ' };
const SOURCE_BADGE_CLASS = { yahoo_auctions: 'badge-yahoo', mercari_cheap: 'badge-mercari', sekaist: 'badge-sekaist', vector_park: 'badge-vectorpark', trefac: 'badge-trefac', rakuma: 'badge-rakuma', yahoo_flea: 'badge-yahoofuri' };

// 旧ジャンル名 → 現在のジャンル名（localStorage に旧名が残っていても新しい名前で復元する）
const CATEGORY_RENAMES = { 'ジュエリー': 'アクセサリー' };

let OPTIONS = null;
const state = {
  categories: new Set(),
  brands: new Set(),
  models: new Set(),         // "ブランド|モデル名"
  model_numbers: new Set(),  // "ブランド|型番"
  sources: new Set(),
};
let RESULTS = [];
let currentSort = 'profit';
let pollTimer = null;

// ── ユーティリティ ───────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function $(id) { return document.getElementById(id); }

function checkbox(group, value, label, tag) {
  const checked = state[group].has(value) ? 'checked' : '';
  return `<label class="chk"><input type="checkbox" data-group="${group}" value="${esc(value)}" ${checked}>${esc(label)}${tag ? ` <span class="tag">${esc(tag)}</span>` : ''}</label>`;
}

// ── 表示中の選択肢 ───────────────────────────────────────
function visibleBrands() {
  return OPTIONS.brands.filter(b => b.categories.some(c => state.categories.has(c)));
}
function selectedVisibleBrands() {
  return visibleBrands().filter(b => state.brands.has(b.name)).map(b => b.name);
}

// ── 描画 ────────────────────────────────────────────────
function renderConditions() {
  $('categoryChecks').innerHTML = OPTIONS.categories.map(c => {
    const n = OPTIONS.keywords.filter(k => k.category === c).length;
    return checkbox('categories', c, c, `${n}`);
  }).join('');

  const brands = visibleBrands();
  $('brandChecks').innerHTML = brands.length
    ? `<div class="checks">${brands.map(b => checkbox('brands', b.name, b.name, b.categories.length > 1 ? b.categories.join('/') : '')).join('')}</div>`
    : '<div class="empty-note">ジャンルを選ぶとブランドが表示されます</div>';

  const selBrands = selectedVisibleBrands();
  const modelHtml = selBrands
    .filter(b => (OPTIONS.models[b] || []).length)
    .map(b => `<div class="sub-title">${esc(b)}</div><div class="checks">${OPTIONS.models[b].map(m => checkbox('models', `${b}|${m}`, m)).join('')}</div>`)
    .join('');
  $('modelChecks').innerHTML = modelHtml || '<div class="empty-note">選択中のブランドに登録済みのモデル名はありません（型番欄に自由入力できます）</div>';

  const numHtml = selBrands
    .filter(b => (OPTIONS.model_numbers[b] || []).length)
    .map(b => `<div class="sub-title">${esc(b)}（型番の候補）</div><div class="checks">${OPTIONS.model_numbers[b].map(m => checkbox('model_numbers', `${b}|${m}`, m)).join('')}</div>`)
    .join('');
  $('numberChecks').innerHTML = numHtml;

  $('sourceChecks').innerHTML = OPTIONS.sources.map(s => checkbox('sources', s.key, s.label, s.auction ? 'オークション' : '')).join('');
  updatePlanLine();
}

function estimatePlan() {
  const brands = new Set(selectedVisibleBrands());
  const free = ($('freeNumbers').value || '').split(/[,、，\s]+/).filter(Boolean);
  let n = 0;
  OPTIONS.keywords.forEach(k => {
    if (!state.categories.has(k.category) || !brands.has(k.brand)) return;
    const picked = [...state.models, ...state.model_numbers].filter(v => v.startsWith(k.brand + '|')).map(v => v.split('|')[1]);
    const targets = picked.concat(free);
    if (!targets.length) { n += 1; return; }
    const extra = targets.filter(m => !k.name.toUpperCase().includes(m.toUpperCase())).length;
    n += extra + (extra < targets.length ? 1 : 0);
  });
  return n;
}

function updatePlanLine() {
  const n = estimatePlan();
  const src = state.sources.size;
  $('planLine').textContent = n
    ? `検索キーワード 約${n}件 × 仕入れ先 ${src}か所`
    : 'ジャンルとブランドを選んでください';
  $('perSourceHint').textContent = `設定ファイルの値は ${OPTIONS.defaults.config_per_source} 件。多いほど時間がかかります（目安: 1キーワード1〜3分）`;
}

// ── 選択操作 ─────────────────────────────────────────────
document.addEventListener('change', e => {
  const g = e.target.dataset && e.target.dataset.group;
  if (!g) { saveState(); updatePlanLine(); return; }
  if (e.target.checked) state[g].add(e.target.value); else state[g].delete(e.target.value);
  saveState();
  if (g === 'categories' || g === 'brands') renderConditions(); else updatePlanLine();
});
document.addEventListener('input', e => {
  if (e.target.id === 'freeNumbers') { saveState(); updatePlanLine(); }
});

function toggleGroup(group, on) {
  if (!on) { state[group].clear(); }
  else if (group === 'categories') OPTIONS.categories.forEach(c => state.categories.add(c));
  else if (group === 'brands') visibleBrands().forEach(b => state.brands.add(b.name));
  else if (group === 'sources') OPTIONS.sources.forEach(s => state.sources.add(s.key));
  saveState();
  renderConditions();
}

function selectAll(on) {
  if (on) {
    toggleGroup('categories', true);
    toggleGroup('brands', true);
    toggleGroup('sources', true);
  } else {
    ['categories', 'brands', 'models', 'model_numbers', 'sources'].forEach(g => state[g].clear());
    $('freeNumbers').value = '';
    saveState();
    renderConditions();
  }
}

// ── 前回の選択を保存/復元（localStorage） ───────────────────
function collectConditions() {
  const visible = new Set(selectedVisibleBrands());
  return {
    categories: [...state.categories],
    brands: [...state.brands].filter(b => visible.has(b)),
    models: [...state.models].filter(v => visible.has(v.split('|')[0])),
    model_numbers: [...state.model_numbers].filter(v => visible.has(v.split('|')[0])),
    free_numbers: $('freeNumbers').value.trim(),
    min_profit: $('minProfit').value,
    min_roi: $('minRoi').value,
    auction_hours: $('auctionHours').value,
    auction_only: $('auctionOnly').checked,
    per_source: $('perSource').value,
    sources: [...state.sources],
  };
}

function saveState() {
  try {
    const c = collectConditions();
    c.brands = [...state.brands];   // 非表示中のブランド選択も覚えておく
    c.models = [...state.models];
    c.model_numbers = [...state.model_numbers];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(c));
  } catch (e) { /* 保存できなくても動作には影響なし */ }
}

function restoreState() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch (e) { saved = null; }
  const d = OPTIONS.defaults;
  if (saved) {
    ['categories', 'brands', 'models', 'model_numbers', 'sources'].forEach(g => (Array.isArray(saved[g]) ? saved[g] : []).forEach(v => state[g].add(g === 'categories' ? (CATEGORY_RENAMES[v] || v) : v)));
  } else {
    OPTIONS.sources.forEach(s => state.sources.add(s.key));
  }
  $('freeNumbers').value = saved?.free_numbers ?? '';
  $('minProfit').value = saved?.min_profit ?? d.min_profit;
  $('minRoi').value = saved?.min_roi ?? d.min_roi;
  $('auctionHours').value = saved?.auction_hours ?? '';
  $('auctionOnly').checked = !!saved?.auction_only;
  $('perSource').value = saved?.per_source || d.per_source;
}

// ── リサーチ実行 ──────────────────────────────────────────
async function startResearch() {
  $('errorLine').textContent = '';
  const cond = collectConditions();
  if (!cond.categories.length || !cond.brands.length) { $('errorLine').textContent = 'ジャンルとブランドを1つ以上選んでください'; return; }
  if (!cond.sources.length) { $('errorLine').textContent = '仕入れ先を1つ以上選んでください'; return; }
  saveState();
  $('startBtn').disabled = true;
  try {
    const res = await fetch('/api/research', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cond) });
    const data = await res.json();
    if (!data.ok) { $('errorLine').textContent = data.error || '開始できませんでした'; $('startBtn').disabled = false; return; }
    RESULTS = [];
    $('resultPanel').hidden = true;
    startPolling();
  } catch (e) {
    $('errorLine').textContent = 'サーバーに接続できません（app.py が起動しているか確認してください）';
    $('startBtn').disabled = false;
  }
}

async function stopResearch() {
  $('stopBtn').disabled = true;
  $('stopBtn').textContent = '中止しています...';
  await fetch('/api/stop', { method: 'POST' });
}

function startPolling() {
  clearInterval(pollTimer);
  pollStatus();
  pollTimer = setInterval(pollStatus, 1500);
}

async function pollStatus() {
  let s;
  try { s = await (await fetch('/api/status')).json(); } catch (e) { return; }
  showProgress(s);
  if (s.state !== 'running') {
    clearInterval(pollTimer);
    $('startBtn').disabled = false;
    const full = await (await fetch('/api/status?results=1')).json();
    RESULTS = full.results || [];
    showProgress(full);
    renderResults();
  }
}

function showProgress(s) {
  if (s.state === 'idle') { $('progressPanel').hidden = true; return; }
  $('progressPanel').hidden = false;
  const running = s.state === 'running';
  $('stopBtn').hidden = !running;
  if (running) { $('stopBtn').disabled = false; $('stopBtn').textContent = '中止'; }
  const total = s.total || 0;
  const done = running ? s.index : (s.state === 'done' ? total : s.index);
  const pct = total ? Math.round(done / total * 100) : 0;
  $('barFill').style.width = (running ? Math.max(pct, 3) : pct) + '%';
  const titles = { running: 'リサーチ中...', done: 'リサーチ完了', stopped: 'リサーチを中止しました', error: 'エラーで停止しました' };
  $('progressTitle').textContent = titles[s.state] || s.state;
  let text = '';
  if (running) {
    text = total ? `${total}キーワード中 ${s.index + 1}件目を処理中：${s.keyword}` : '準備中...';
    if (s.started_at) text += `（経過 ${elapsed(s.started_at)}）`;
  } else if (s.state === 'error') {
    text = `エラー: ${s.error}`;
  } else {
    text = `${total}キーワード中 ${done}件 処理済み・該当 ${s.result_count}件` + (s.finished_at ? `（${s.finished_at.replace('T', ' ')} 完了）` : '');
  }
  $('progressText').textContent = text;
  const box = $('logBox');
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 10;
  box.textContent = (s.logs || []).join('\n');
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function elapsed(iso) {
  const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return `${Math.floor(sec / 60)}分${sec % 60}秒`;
}

// ── 結果表示 ─────────────────────────────────────────────
function remainSeconds(d) {
  return d.end_time ? d.end_time - Date.now() / 1000 : null;
}
function formatRemain(sec) {
  if (sec <= 0) return '終了';
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  if (h >= 48) return `残り${Math.floor(h / 24)}日${h % 24}時間`;
  if (h >= 1) return `残り${h}時間${m}分`;
  return `残り${m}分`;
}

function setSort(s, btnEl) {
  currentSort = s;
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  btnEl.classList.add('active');
  renderResults();
}

function copyDealText(btnEl, text) {
  if (!(navigator.clipboard && navigator.clipboard.writeText)) return;
  const original = btnEl.textContent;
  navigator.clipboard.writeText(text).then(() => {
    btnEl.textContent = 'コピーしました';
    setTimeout(() => { btnEl.textContent = original; }, 1500);
  });
}

function renderResults() {
  $('resultPanel').hidden = false;
  const items = RESULTS.slice().sort((a, b) => {
    if (currentSort === 'roi') return b.roi_percent - a.roi_percent;
    if (currentSort === 'remain') {
      const ra = remainSeconds(a), rb = remainSeconds(b);
      if (ra === null && rb === null) return b.estimated_profit - a.estimated_profit;
      if (ra === null) return 1;
      if (rb === null) return -1;
      return ra - rb;
    }
    return b.estimated_profit - a.estimated_profit;
  });
  $('countLine').textContent = `該当 ${items.length}件`;
  const container = $('cardsContainer');
  if (!items.length) {
    container.innerHTML = '<div class="empty">条件に合う案件がありませんでした。条件をゆるめて再度お試しください</div>';
    return;
  }
  container.innerHTML = items.map(d => {
    const srcLabel = SOURCE_LABELS[d.source] || d.source;
    const srcClass = SOURCE_BADGE_CLASS[d.source] || 'badge-mercari';
    const profitClass = d.estimated_profit >= 10000 ? 'profit-high' : d.estimated_profit >= 5000 ? 'profit-mid' : 'profit-low';
    const img = d.image_url
      ? `<img class="card-img" src="${esc(d.image_url)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">`
      : `<div class="card-img-placeholder">🏷️</div>`;
    const rs = remainSeconds(d);
    let remainBadge = '';
    if (rs !== null) {
      const kind = d.is_auction ? '' : '即決 ';
      remainBadge = `<span class="badge badge-remain ${rs > 86400 ? 'later' : ''}">${kind}${formatRemain(rs)}</span>`;
    }
    const searchText = [d.brand, d.model].filter(Boolean).join(' ') || d.keyword;
    const searchTextAttr = esc(JSON.stringify(searchText));
    const mercariSearchUrl = `https://jp.mercari.com/search?keyword=${encodeURIComponent(searchText)}&status=sold_out&sort=created_time&order=desc&item_types=1`;
    return `
    <div class="card">
      ${img}
      <div class="card-body">
        <div class="card-badges">
          ${d.brand ? `<span class="badge badge-brand">${esc(d.brand)}</span>` : ''}
          ${d.category ? `<span class="badge badge-category">${esc(CATEGORY_RENAMES[d.category] || d.category)}</span>` : ''}
          <span class="badge ${srcClass}">${esc(srcLabel)}</span>
          ${remainBadge}
          ${d.model ? `<span class="badge badge-cond">${esc(d.model)}</span>` : ''}
          ${d.condition_label !== '状態不明' ? `<span class="badge badge-cond">${esc(d.condition_label)}</span>` : ''}
        </div>
        <div class="card-title">${esc(d.title)}</div>
        <div class="card-prices">仕入れ <span>¥${Number(d.purchase_price).toLocaleString()}</span> → 相場 <span>¥${Number(d.reference_price).toLocaleString()}</span></div>
        <div class="card-profit ${profitClass}">¥${Number(d.estimated_profit).toLocaleString()} 利益 <span style="font-size:13px;font-weight:400;color:#888">利益率 ${d.roi_percent}%</span></div>
        <div class="card-actions-secondary">
          <button type="button" class="btn-secondary btn-copy" onclick="copyDealText(this, ${searchTextAttr})">📋 コピー</button>
          <a class="btn-secondary btn-mercari" href="${esc(mercariSearchUrl)}" target="_blank" rel="noopener noreferrer">メルカリ相場を見る</a>
        </div>
        <a class="card-btn" href="${esc(d.url)}" target="_blank" rel="noopener">商品を見る →</a>
      </div>
    </div>`;
  }).join('');
}

// ── 初期化 ───────────────────────────────────────────────
async function init() {
  OPTIONS = await (await fetch('/api/options')).json();
  restoreState();
  renderConditions();
  const s = await (await fetch('/api/status?results=1')).json();
  if (s.state === 'running') {
    $('startBtn').disabled = true;
    startPolling();
  } else if (s.state !== 'idle') {
    showProgress(s);
    RESULTS = s.results || [];
    renderResults();
  }
}
init();
