// ローカル リサーチ画面のロジック（app.py の /api/* と通信する）
// 画面構成: 条件選択（4ステップ） → 調査中 → 結果

const STORAGE_KEY = 'rieki-research-conditions-v1';   // 保存形式は従来のまま（前回の選択をそのまま復元）
const SOURCE_LABELS = { yahoo_auctions: 'ヤフオク', mercari_cheap: 'メルカリ安値', sekaist: 'セカスト', vector_park: 'ベクトルパーク', trefac: 'トレファク', rakuma: 'ラクマ', yahoo_flea: 'Yahoo!フリマ' };
const SOURCE_ICONS = { yahoo_auctions: '🔨', mercari_cheap: '🛍️', sekaist: '👕', vector_park: '💼', trefac: '♻️', rakuma: '📦', yahoo_flea: '🏪' };
const SOURCE_DESC = { yahoo_auctions: '入札形式・即決の両方', mercari_cheap: 'メルカリの安い出品', sekaist: '', vector_park: 'ブランド買取店の販売', trefac: 'リユース店の通販', rakuma: 'フリマアプリ', yahoo_flea: 'フリマアプリ' };
const GENRE_ICONS = { '時計': '⌚', 'ダウン': '🧥', 'サングラス': '🕶️', 'アクセサリー': '💍', 'ジュエリー': '💍', 'スカーフ': '🧣', 'バッグ': '👜', '財布': '👛', 'その他': '🏷️' };
const HOUR_OPTIONS = [['', '指定なし'], ['3', '3時間'], ['6', '6時間'], ['12', '12時間'], ['24', '24時間'], ['72', '3日']];
const PER_SOURCE_PRESETS = [[20, '少なめ'], [50, '普通'], [80, '多め']];

// 旧ジャンル名 → 現在のジャンル名（localStorage や前回結果に旧名が残っていても新しい名前で扱う）
const CATEGORY_RENAMES = { 'ジュエリー': 'アクセサリー' };
const catName = c => CATEGORY_RENAMES[c] || c;

let OPTIONS = null;
const state = {
  categories: new Set(),
  brands: new Set(),
  models: new Set(),         // "ブランド|モデル名"
  model_numbers: new Set(),  // "ブランド|型番"
  sources: new Set(),
};
const cond = { min_profit: 3000, min_roi: 0, auction_hours: '', auction_only: false, per_source: 20, free_numbers: '' };

let currentStep = 1;
let RESULTS = [];
let LAST = null;               // 直近のステータス（結果つき）
let currentSort = 'profit';
let filterSource = '';
let filterGenre = '';
let pollTimer = null;
let tickTimer = null;
let lastRunning = null;        // 実行中ステータス（経過時間の毎秒更新用）

// ── ユーティリティ ───────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function $(id) { return document.getElementById(id); }
const yen = n => '¥' + Math.round(Number(n) || 0).toLocaleString();
const num = n => Math.round(Number(n) || 0).toLocaleString();
function joinShort(arr, max = 3) {
  if (!arr.length) return '';
  return arr.length > max ? `${arr.slice(0, max).join('・')} ほか${arr.length - max}` : arr.join('・');
}
function brandOfKeyword(kw) {
  const k = OPTIONS && OPTIONS.keywords.find(x => x.name === kw);
  if (k) return k.brand;
  return String(kw || '').split(/\s+/)[0] || '';
}
// ブランドの頭文字バッジ（公式ロゴは使わない）
function initialOf(name) {
  const s = String(name || '').trim();
  if (/^[A-Za-z]/.test(s)) return s.length <= 3 && s === s.toUpperCase() ? s : s[0].toUpperCase();
  return s[0] || '?';
}
function hueOf(name) {
  let h = 0;
  for (const ch of String(name)) h = (h * 31 + ch.codePointAt(0)) % 360;
  return h;
}
function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${h}時間${m}分`;
  return `${m}分${s}秒`;
}

// ── 表示中の選択肢 ───────────────────────────────────────
function visibleBrands() {
  return OPTIONS.brands.filter(b => b.categories.some(c => state.categories.has(c)));
}
function selectedVisibleBrands() {
  return visibleBrands().filter(b => state.brands.has(b.name)).map(b => b.name);
}
function brandKeywordCount(brand) {
  return OPTIONS.keywords.filter(k => k.brand === brand && state.categories.has(k.category)).length;
}

// ── 入力チェック（必須） ─────────────────────────────────
function stepError(step) {
  if (step >= 1 && !state.categories.size) return 'ジャンルを1つ以上選んでください';
  if (step >= 2 && !selectedVisibleBrands().length) return 'ブランドを1つ以上選んでください';
  if (step >= 4 && !state.sources.size) return '仕入れ先を1つ以上選んでください';
  return '';
}
function canReach(step) {
  for (let i = 1; i < step; i++) if (stepError(i)) return false;
  return true;
}

// ── 画面切り替え ─────────────────────────────────────────
function showView(name) {
  ['setup', 'progress', 'result'].forEach(v => { $(v + 'View').hidden = v !== name; });
  $('navSetup').classList.toggle('on', name === 'setup');
  $('navResults').classList.toggle('on', name === 'result');
  $('navProgress').classList.toggle('on', name === 'progress');
  window.scrollTo(0, 0);
}
function showSetup() { goStep(currentStep, true); }

function goStep(n, fromOutside) {
  n = Math.max(1, Math.min(4, n));
  if (n > currentStep || fromOutside) {
    // 先に進むときは手前の必須項目を確認（足りなければ足りないステップで止める）
    while (n > 1 && !canReach(n)) n--;
  }
  currentStep = n;
  showView('setup');
  document.querySelectorAll('#setupView .step').forEach(s => { s.hidden = Number(s.dataset.step) !== n; });
  renderStep();
}

function renderStep() {
  document.querySelectorAll('#stepper .stp').forEach(li => {
    const s = Number(li.dataset.step);
    li.classList.toggle('current', s === currentStep);
    li.classList.toggle('done', s < currentStep);
    li.classList.toggle('locked', !canReach(s));
    li.querySelector('button').disabled = !canReach(s);
  });
  document.querySelectorAll('#stepper .stp-line').forEach((l, i) => l.classList.toggle('done', i + 1 < currentStep));
  $('stepCount').textContent = `ステップ ${currentStep} / 4`;

  if (currentStep === 1) renderGenres();
  if (currentStep === 2) renderBrands();
  if (currentStep === 3) renderModels();
  if (currentStep === 4) renderStep4();
  renderSelBar();
  renderNav();
}

function renderNav() {
  $('backBtn').style.visibility = currentStep > 1 ? 'visible' : 'hidden';
  const err = currentStep < 4 ? stepError(currentStep) : '';
  const next = $('nextBtn');
  next.hidden = currentStep === 4;
  next.disabled = !!err;
  next.textContent = currentStep === 3 && !hasModelPick() ? 'スキップして次へ →' : '次へ →';
  $('navReason').textContent = err ? `⚠ ${err}` : '';
  $('navReason').classList.toggle('warn', !!err);
}

// ── 選択中の要約（常に表示） ─────────────────────────────
function hasModelPick() {
  const vis = new Set(selectedVisibleBrands());
  return [...state.models, ...activeNumbers()].some(v => vis.has(v.split('|')[0])) || !!cond.free_numbers.trim();
}
function selectionParts() {
  const cats = OPTIONS.categories.filter(c => state.categories.has(c));
  const brands = selectedVisibleBrands();
  const vis = new Set(brands);
  const picks = [...state.models, ...activeNumbers()].filter(v => vis.has(v.split('|')[0])).map(v => v.split('|')[1]);
  const free = cond.free_numbers.split(/[,、，\s]+/).filter(Boolean);
  const srcs = OPTIONS.sources.filter(s => state.sources.has(s.key)).map(s => s.label);
  return { cats, brands, picks: picks.concat(free), srcs };
}
function profitText() {
  let t = `利益${num(cond.min_profit)}円以上`;
  if (Number(cond.min_roi) > 0) t += `・利益率${cond.min_roi}%以上`;
  return t;
}
function renderSelBar() {
  const p = selectionParts();
  const item = (label, val, empty) => `<span class="sel-item ${val ? '' : 'sel-empty'}"><b>${label}</b>${esc(val || empty)}</span>`;
  $('selBar').innerHTML = `<span class="sel-title">選択中</span>` +
    item('ジャンル', joinShort(p.cats), '未選択') +
    item('ブランド', joinShort(p.brands), '未選択') +
    (p.picks.length ? item('モデル・型番', joinShort(p.picks, 2), '') : '') +
    (currentStep >= 4 || state.sources.size ? item('仕入れ先', joinShort(p.srcs), '未選択') : '') +
    (currentStep >= 4 ? item('利益', profitText().replace('利益', ''), '') : '');
}

// ── ステップ1: ジャンル ─────────────────────────────────
function renderGenres() {
  $('genreGrid').innerHTML = OPTIONS.categories.map(c => {
    const on = state.categories.has(c);
    const brands = OPTIONS.brands.filter(b => b.categories.includes(c));
    return `
    <button type="button" class="pick-card genre-card ${on ? 'on' : ''}" aria-pressed="${on}" data-toggle="categories" data-value="${esc(c)}">
      <span class="check" aria-hidden="true"></span>
      <span class="genre-icon" aria-hidden="true">${GENRE_ICONS[c] || '🏷️'}</span>
      <span class="genre-name">${esc(c)}</span>
      <span class="genre-sub">${brands.length}ブランド</span>
    </button>`;
  }).join('');
}

// ── ステップ2: ブランド ─────────────────────────────────
function renderBrands() {
  const cats = OPTIONS.categories.filter(c => state.categories.has(c));
  $('brandGroups').innerHTML = cats.map(c => {
    const brands = OPTIONS.brands.filter(b => b.categories.includes(c));
    const allOn = brands.every(b => state.brands.has(b.name));
    return `
    <div class="brand-group">
      <div class="group-title">
        <h3><span aria-hidden="true">${GENRE_ICONS[c] || '🏷️'}</span> ${esc(c)} <small>${brands.length}ブランド</small></h3>
        <button type="button" class="link-btn" data-genre-all="${esc(c)}" data-on="${allOn ? '0' : '1'}">${allOn ? 'このジャンルを解除' : 'このジャンルをすべて選択'}</button>
      </div>
      <div class="brand-grid">
        ${brands.map(b => {
          const on = state.brands.has(b.name);
          const others = b.categories.filter(x => x !== c);
          return `
          <button type="button" class="pick-card brand-card ${on ? 'on' : ''}" aria-pressed="${on}" data-toggle="brands" data-value="${esc(b.name)}">
            <span class="check" aria-hidden="true"></span>
            <span class="initial" style="--h:${hueOf(b.name)}" aria-hidden="true">${esc(initialOf(b.name))}</span>
            <span class="brand-text">
              <span class="brand-name">${esc(b.name)}</span>
              <span class="brand-sub">${others.length ? `${others.map(o => (GENRE_ICONS[o] || '') + o).join('・')}にも` : `${b.keywords.length}キーワード`}</span>
            </span>
          </button>`;
        }).join('')}
      </div>
    </div>`;
  }).join('') || '<div class="empty-note">ステップ1でジャンルを選ぶと、ブランドが表示されます</div>';
  const n = selectedVisibleBrands().length;
  $('brandCountNote').textContent = `${n} / ${visibleBrands().length} ブランドを選択中`;
}

// ── ステップ3: モデル名・型番 ───────────────────────────
const openDetails = new Set();
// ジャンル専用の型番（グッチの YA126402 → 時計）は、そのジャンルを選んでいるときだけ出す
function numbersFor(b) {
  const cats = (OPTIONS.model_number_categories || {})[b] || {};
  return (OPTIONS.model_numbers[b] || []).filter(m => !cats[m] || state.categories.has(cats[m]));
}
// 選択中の型番のうち、いま選んでいるジャンルで使えるもの（"ブランド|型番"）
function activeNumbers() {
  return [...state.model_numbers].filter(v => {
    const [b, m] = v.split('|');
    const c = ((OPTIONS.model_number_categories || {})[b] || {})[m];
    return !c || state.categories.has(c);
  });
}
function renderModels() {
  const brands = selectedVisibleBrands();
  const withOpts = brands.filter(b => (OPTIONS.models[b] || []).length || numbersFor(b).length);
  const without = brands.filter(b => !withOpts.includes(b));
  const labels = OPTIONS.model_number_labels || {};
  const chip = (group, b, m) => {
    const v = `${b}|${m}`;
    const on = state[group].has(v);
    // 型番に愛称があれば「RB2140 ウェイファーラー」のように添える（値は型番のまま）
    const nick = group === 'model_numbers' ? ((labels[b] || {})[m] || '') : '';
    return `<button type="button" class="chip ${on ? 'on' : ''}" aria-pressed="${on}" data-toggle="${group}" data-value="${esc(v)}">${on ? '✓ ' : ''}${esc(m)}${nick ? ` <small>${esc(nick)}</small>` : ''}</button>`;
  };
  let html = withOpts.map(b => {
    const ms = OPTIONS.models[b] || [], ns = numbersFor(b);
    const cnt = [...state.models, ...activeNumbers()].filter(v => v.startsWith(b + '|')).length;
    return `
    <details class="model-group" data-brand="${esc(b)}" ${openDetails.has(b) ? 'open' : ''}>
      <summary>
        <span class="initial small" style="--h:${hueOf(b)}" aria-hidden="true">${esc(initialOf(b))}</span>
        <span class="mg-name">${esc(b)}</span>
        <span class="mg-count ${cnt ? 'on' : ''}">${cnt ? `${cnt}件選択中` : 'ブランド全体を検索'}</span>
        <span class="mg-arrow" aria-hidden="true"></span>
      </summary>
      <div class="mg-body">
        ${ms.length ? `<div class="mg-label">モデル名</div><div class="chips">${ms.map(m => chip('models', b, m)).join('')}</div>` : ''}
        ${ns.length ? `<div class="mg-label">型番の候補</div><div class="chips">${ns.map(m => chip('model_numbers', b, m)).join('')}</div>` : ''}
      </div>
    </details>`;
  }).join('');
  if (without.length) {
    html += `<div class="no-model-note">登録済みのモデル名・型番の候補がないブランド：${esc(without.join('・'))}（ブランド全体を検索します。型番は上の欄に直接入力できます）</div>`;
  }
  $('modelGroups').innerHTML = html || '<div class="empty-note">ブランドを選ぶと表示されます</div>';
  $('freeNumbers').value = cond.free_numbers;
}
function setAllDetails(open) {
  document.querySelectorAll('#modelGroups details').forEach(d => {
    d.open = open;
    if (open) openDetails.add(d.dataset.brand); else openDetails.delete(d.dataset.brand);
  });
}
function clearModels() {
  state.models.clear();
  state.model_numbers.clear();
  cond.free_numbers = '';
  saveState();
  renderStep();
}

// ── ステップ4: 条件と仕入れ先 ───────────────────────────
function renderStep4() {
  syncNumber('minProfit', cond.min_profit);
  syncNumber('minRoi', cond.min_roi);
  $('hoursSeg').innerHTML = HOUR_OPTIONS.map(([v, l]) =>
    `<button type="button" class="${String(cond.auction_hours) === v ? 'on' : ''}" data-hours="${v}">${l}${v ? '以内' : ''}</button>`).join('');
  $('auctionOnly').checked = !!cond.auction_only;
  $('perSourceSeg').innerHTML = PER_SOURCE_PRESETS.map(([v, l]) =>
    `<button type="button" class="${Number(cond.per_source) === v ? 'on' : ''}" data-per="${v}"><b>${l}</b><span>${v}件</span></button>`).join('');
  $('sourceGrid').innerHTML = OPTIONS.sources.map(s => {
    const on = state.sources.has(s.key);
    return `
    <button type="button" class="pick-card source-card ${on ? 'on' : ''}" aria-pressed="${on}" data-toggle="sources" data-value="${esc(s.key)}">
      <span class="check" aria-hidden="true"></span>
      <span class="src-icon" aria-hidden="true">${SOURCE_ICONS[s.key] || '🏬'}</span>
      <span class="src-text">
        <span class="src-name">${esc(s.label)}${s.auction ? ' <span class="auction-badge">オークション</span>' : ''}</span>
        <span class="src-desc">${esc(SOURCE_DESC[s.key] || '')}</span>
      </span>
    </button>`;
  }).join('');
  renderFinal();
}

function syncNumber(id, v) {
  const n = $(id), r = $(id + 'Range');
  if (document.activeElement !== n) n.value = v;
  r.value = Math.min(Number(r.max), Number(v) || 0);
  r.style.setProperty('--fill', `${(Math.min(Number(r.max), Number(v) || 0) / Number(r.max)) * 100}%`);
}

function estimatePlan() {
  const brands = new Set(selectedVisibleBrands());
  const free = cond.free_numbers.split(/[,、，\s]+/).filter(Boolean);
  const en = OPTIONS.model_en || {};
  let n = 0, q = 0;   // n: キーワード数（進捗の分母）、q: 仕入れ先での検索語の本数
  OPTIONS.keywords.forEach(k => {
    if (!state.categories.has(k.category) || !brands.has(k.brand)) return;
    const picked = [...state.models, ...activeNumbers()].filter(v => v.startsWith(k.brand + '|')).map(v => v.split('|')[1]);
    const targets = picked.concat(free);
    if (!targets.length) { n += 1; q += 1; return; }
    const extra = targets.filter(m => !k.name.toUpperCase().includes(m.toUpperCase()));
    const base = extra.length < targets.length ? 1 : 0;
    n += extra.length + base;
    // モデル名はカタカナ名・英字名の2本で検索する（英字名があるもの）
    q += base + extra.reduce((a, m) => a + ((en[k.brand] || {})[m] ? 2 : 1), 0);
  });
  return { n, q };
}
// 所要時間の目安（分）。1キーワードあたり: 相場取得 + 仕入れ先ごとの検索・型番確認
// （英字名でも検索するモデルは検索語の本数ぶん仕入れ先の検索が増える）
function estimateMinutes(perSource) {
  const { n, q } = estimatePlan();
  const src = Math.max(1, state.sources.size);
  const total = n * 0.4 + q * src * (0.15 + perSource / 100);
  return { n, q, lo: Math.max(1, Math.round(total * 0.6)), hi: Math.max(2, Math.round(total * 1.4)) };
}
function etaText(perSource) {
  const e = estimateMinutes(perSource);
  if (!e.n) return '';
  const f = m => (m >= 60 ? `${Math.floor(m / 60)}時間${m % 60 ? (m % 60) + '分' : ''}` : `${m}分`);
  return `約${f(e.lo)}〜${f(e.hi)}`;
}

function renderFinal() {
  const p = selectionParts();
  const hours = HOUR_OPTIONS.find(([v]) => v === String(cond.auction_hours));
  const preset = PER_SOURCE_PRESETS.find(([v]) => v === Number(cond.per_source));
  const rows = [
    ['ジャンル', p.cats.join('・')],
    ['ブランド', joinShort(p.brands, 6)],
    ['モデル・型番', p.picks.length ? joinShort(p.picks, 6) : '指定なし（ブランド全体）'],
    ['仕入れ先', p.srcs.join('・')],
    ['利益', profitText()],
    ['残り時間', (hours && hours[0] ? hours[1] + '以内' : '指定なし') + (cond.auction_only ? '・オークションのみ' : '')],
    ['取得件数', `${preset ? preset[1] : ''} ${cond.per_source}件`],
  ];
  $('finalSummary').innerHTML = rows.map(([k, v]) =>
    `<div class="fs-row"><dt>${k}</dt><dd class="${v ? '' : 'missing'}">${esc(v || '未選択')}</dd></div>`).join('');
  const e = estimateMinutes(Number(cond.per_source));
  const err = stepError(4);
  $('finalLine').innerHTML = err
    ? `<span class="warn">⚠ ${esc(err)}</span>`
    : `検索キーワード 約<b>${e.n}</b>件${e.q > e.n ? `（検索語 ${e.q} 本）` : ''} × 仕入れ先 <b>${state.sources.size}</b>か所・所要時間の目安 <b>${etaText(Number(cond.per_source))}</b>`;
  $('startBtn').disabled = !!err || isRunning();
  $('etaLine').innerHTML = PER_SOURCE_PRESETS.map(([v, l]) =>
    `<span class="${Number(cond.per_source) === v ? 'on' : ''}">${l}: ${etaText(v) || '—'}</span>`).join('') +
    '<div class="hint">多いほど見つかりやすくなりますが、時間がかかります（選択中の条件での目安）</div>';
}

// ── 操作（クリック・入力） ───────────────────────────────
document.addEventListener('click', e => {
  const t = e.target.closest('[data-toggle]');
  if (t) {
    const g = t.dataset.toggle, v = t.dataset.value;
    if (state[g].has(v)) state[g].delete(v); else state[g].add(v);
    saveState();
    renderStep();
    return;
  }
  const ga = e.target.closest('[data-genre-all]');
  if (ga) {
    const on = ga.dataset.on === '1';
    OPTIONS.brands.filter(b => b.categories.includes(ga.dataset.genreAll)).forEach(b => (on ? state.brands.add(b.name) : state.brands.delete(b.name)));
    saveState();
    renderStep();
    return;
  }
  const h = e.target.closest('[data-hours]');
  if (h) { cond.auction_hours = h.dataset.hours; saveState(); renderStep(); return; }
  const ps = e.target.closest('[data-per]');
  if (ps) { cond.per_source = Number(ps.dataset.per); saveState(); renderStep(); return; }
  const so = e.target.closest('#sortSeg [data-sort]');
  if (so) { currentSort = so.dataset.sort; renderResults(); return; }
  const fs = e.target.closest('[data-fsource]');
  if (fs) { filterSource = fs.dataset.fsource; renderResults(); return; }
  const fg = e.target.closest('[data-fgenre]');
  if (fg) { filterGenre = fg.dataset.fgenre; renderResults(); return; }
});

document.addEventListener('toggle', e => {
  const d = e.target;
  if (d.matches && d.matches('#modelGroups details')) {
    if (d.open) openDetails.add(d.dataset.brand); else openDetails.delete(d.dataset.brand);
  }
}, true);

document.addEventListener('input', e => {
  const id = e.target.id;
  if (id === 'freeNumbers') { cond.free_numbers = e.target.value; saveState(); renderSelBar(); renderNav(); return; }
  if (id === 'minProfit' || id === 'minProfitRange') { cond.min_profit = Math.max(0, Number(e.target.value) || 0); }
  else if (id === 'minRoi' || id === 'minRoiRange') { cond.min_roi = Math.max(0, Number(e.target.value) || 0); }
  else if (id === 'auctionOnly') { cond.auction_only = e.target.checked; }
  else return;
  saveState();
  syncNumber('minProfit', cond.min_profit);
  syncNumber('minRoi', cond.min_roi);
  renderSelBar();
  renderFinal();
});
document.addEventListener('change', e => {
  if (e.target.id === 'auctionOnly') { cond.auction_only = e.target.checked; saveState(); renderFinal(); }
  if (e.target.id === 'minProfit' || e.target.id === 'minRoi') { syncNumber(e.target.id, e.target.id === 'minProfit' ? cond.min_profit : cond.min_roi); e.target.value = e.target.id === 'minProfit' ? cond.min_profit : cond.min_roi; }
});

function toggleGroup(group, on) {
  if (!on) {
    if (group === 'brands') visibleBrands().forEach(b => state.brands.delete(b.name));
    else state[group].clear();
  }
  else if (group === 'categories') OPTIONS.categories.forEach(c => state.categories.add(c));
  else if (group === 'brands') visibleBrands().forEach(b => state.brands.add(b.name));
  else if (group === 'sources') OPTIONS.sources.forEach(s => state.sources.add(s.key));
  saveState();
  renderStep();
}

// ── 前回の選択を保存/復元（localStorage） ───────────────────
function collectConditions() {
  const visible = new Set(selectedVisibleBrands());
  return {
    categories: [...state.categories],
    brands: [...state.brands].filter(b => visible.has(b)),
    models: [...state.models].filter(v => visible.has(v.split('|')[0])),
    model_numbers: activeNumbers().filter(v => visible.has(v.split('|')[0])),
    free_numbers: cond.free_numbers.trim(),
    min_profit: String(cond.min_profit),
    min_roi: String(cond.min_roi),
    auction_hours: String(cond.auction_hours),
    auction_only: !!cond.auction_only,
    per_source: String(cond.per_source),
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

// 残り時間・取得件数は、保存値がボタンにない（旧形式の値）ときは近いボタンに合わせる
function snapHours(v) {
  const n = Number(v);
  if (v === '' || v == null || !isFinite(n) || n <= 0) return '';
  const opts = HOUR_OPTIONS.filter(([x]) => x).map(([x]) => Number(x));
  const hit = opts.find(x => x >= n) || opts[opts.length - 1];
  return String(hit);
}
function snapPerSource(v) {
  const n = Number(v);
  if (!isFinite(n) || n <= 0) return OPTIONS.defaults.per_source || 20;
  return PER_SOURCE_PRESETS.map(p => p[0]).reduce((a, b) => (Math.abs(b - n) < Math.abs(a - n) ? b : a));
}

function restoreState() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch (e) { saved = null; }
  if (!saved || typeof saved !== 'object' || Array.isArray(saved)) saved = null;
  const d = OPTIONS.defaults;
  const knownCats = new Set(OPTIONS.categories);
  const knownSrc = new Set(OPTIONS.sources.map(s => s.key));
  if (saved) {
    ['categories', 'brands', 'models', 'model_numbers', 'sources'].forEach(g => {
      (Array.isArray(saved[g]) ? saved[g] : []).forEach(v => {
        if (typeof v !== 'string' || !v) return;
        if (g === 'categories') { v = catName(v); if (!knownCats.has(v)) return; }
        if (g === 'sources' && !knownSrc.has(v)) return;
        if (g === 'models' || g === 'model_numbers') {
          // 今の候補にないモデル・型番（古い保存データ）は復元しない
          const [br, m] = v.split('|');
          if (!m || !((OPTIONS[g] || {})[br] || []).includes(m)) return;
        }
        state[g].add(v);
      });
    });
  } else {
    OPTIONS.sources.forEach(s => state.sources.add(s.key));
  }
  const n = (v, def) => (v === '' || v == null || !isFinite(Number(v)) ? def : Math.max(0, Number(v)));
  cond.free_numbers = typeof saved?.free_numbers === 'string' ? saved.free_numbers : '';
  cond.min_profit = n(saved?.min_profit, d.min_profit);
  cond.min_roi = n(saved?.min_roi, d.min_roi);
  cond.auction_hours = snapHours(saved?.auction_hours);
  cond.auction_only = !!saved?.auction_only;
  cond.per_source = snapPerSource(saved?.per_source ?? d.per_source);
}

// ── リサーチ実行 ──────────────────────────────────────────
function isRunning() { return !!(LAST && LAST.state === 'running'); }

async function startResearch() {
  $('errorLine').textContent = '';
  const c = collectConditions();
  const err = stepError(4);
  if (err) { $('errorLine').textContent = err; return; }
  saveState();
  $('startBtn').disabled = true;
  try {
    const res = await fetch('/api/research', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(c) });
    const data = await res.json();
    if (!data.ok) { $('errorLine').textContent = data.error || '開始できませんでした'; $('startBtn').disabled = false; return; }
    RESULTS = [];
    filterSource = ''; filterGenre = '';
    LAST = { state: 'running', index: 0, total: 0, keyword: '', started_at: new Date().toISOString(), logs: [], conditions: c, found_count: 0 };
    showProgress(LAST);
    showView('progress');
    startPolling();
  } catch (e) {
    $('errorLine').textContent = 'サーバーに接続できません（start.command で起動しているか確認してください）';
    $('startBtn').disabled = false;
  }
}

async function stopResearch() {
  if (!confirm('リサーチを中止しますか？（ここまでに見つかった分は結果に表示されます）')) return;
  $('stopBtn').disabled = true;
  $('stopBtn').textContent = '中止しています…';
  try { await fetch('/api/stop', { method: 'POST' }); } catch (e) { /* 次のポーリングで反映 */ }
}

function startPolling() {
  clearInterval(pollTimer);
  clearInterval(tickTimer);
  pollStatus();
  pollTimer = setInterval(pollStatus, 1500);
  tickTimer = setInterval(() => { if (lastRunning) updateTimes(lastRunning); }, 1000);
}

async function pollStatus() {
  let s;
  try { s = await (await fetch('/api/status')).json(); } catch (e) { return; }
  if (s.state === 'running') {
    LAST = s;
    showProgress(s);
    return;
  }
  clearInterval(pollTimer);
  clearInterval(tickTimer);
  lastRunning = null;
  let full = s;
  try { full = await (await fetch('/api/status?results=1')).json(); } catch (e) { /* 結果なしで続行 */ }
  LAST = full;
  RESULTS = full.results || [];
  showProgress(full);
  updateNav();
  if (full.state === 'error') { showView('progress'); return; }
  renderResults();
  showView('result');
}

function updateNav() {
  const running = isRunning();
  $('navProgress').hidden = !running;
  $('navResults').hidden = running || !LAST || LAST.state === 'idle' || LAST.state === 'error';
  if (!$('navResults').hidden) $('navResults').textContent = `結果を見る（${(LAST.results || RESULTS).length}件）`;
  if (currentStep === 4) renderFinal();
  renderLastBanner();
}

function renderLastBanner() {
  const b = $('lastBanner');
  if (isRunning()) {
    b.hidden = false;
    b.innerHTML = `<span>⏳ リサーチを実行中です</span><button type="button" class="link-btn" onclick="showView('progress')">進み具合を見る →</button>`;
    return;
  }
  if (!LAST || !['done', 'stopped'].includes(LAST.state)) { b.hidden = true; return; }
  const when = (LAST.finished_at || '').replace('T', ' ').slice(0, 16);
  b.hidden = false;
  b.innerHTML = `<span>📋 前回の結果：<b>${RESULTS.length}件</b>${when ? `（${esc(when)}）` : ''}</span><button type="button" class="link-btn" onclick="showResultsView()">前回の結果を見る →</button>`;
}

function condSummary(c) {
  if (!c || typeof c !== 'object') return '';
  const arr = v => (Array.isArray(v) ? v : []);
  const cats = arr(c.categories).map(catName);
  const brands = arr(c.brands);
  const srcs = arr(c.sources).map(k => SOURCE_LABELS[k] || k);
  const parts = [joinShort(cats), joinShort(brands), joinShort(srcs)].filter(Boolean);
  if (c.min_profit !== undefined && c.min_profit !== '') parts.push(`利益${num(c.min_profit)}円以上`);
  return parts.join(' / ');
}

function showProgress(s) {
  const running = s.state === 'running';
  if (running) lastRunning = s;
  $('stopBtn').hidden = !running;
  if (running && $('stopBtn').textContent !== '中止しています…') { $('stopBtn').disabled = false; }
  if (running && s.stop_requested) { $('stopBtn').disabled = true; }
  $('progressActions').hidden = running;
  const total = s.total || 0;
  const done = running ? s.index : (s.state === 'done' ? total : s.index);
  const pct = total ? Math.round(done / total * 100) : 0;
  $('barFill').style.width = (running ? Math.max(pct, 2) : pct) + '%';
  $('bigBar').setAttribute('aria-valuenow', pct);
  $('bigBar').classList.toggle('running', running);
  $('pctText').textContent = `${pct}%`;
  $('kwCount').textContent = total ? `${running ? Math.min(s.index + 1, total) : done} / ${total} キーワード` : '準備中…';
  const titles = { running: '🔍 リサーチ中…', done: '✅ リサーチ完了', stopped: '⏹ リサーチを中止しました', error: '⚠ エラーで停止しました' };
  $('progressTitle').textContent = titles[s.state] || s.state;
  $('progressCond').textContent = condSummary(s.conditions);
  if (running) {
    $('nowBrand').textContent = s.keyword ? brandOfKeyword(s.keyword) : '準備中…';
    $('nowKeyword').textContent = s.keyword ? `検索キーワード：${s.keyword}` : 'ブラウザを起動しています';
    $('foundText').textContent = `${s.found_count || 0}件`;
    $('progressNote').textContent = '候補の数は途中経過です。最後に条件（モデル・利益率・重複）で絞り込んだ件数が結果になります。';
  } else if (s.state === 'error') {
    $('nowBrand').textContent = 'エラー';
    $('nowKeyword').textContent = s.error || '';
    $('progressNote').textContent = '条件を見直して、もう一度お試しください。';
  } else {
    $('nowBrand').textContent = `該当 ${s.result_count ?? RESULTS.length}件`;
    $('nowKeyword').textContent = s.finished_at ? `${s.finished_at.replace('T', ' ')} 完了` : '';
    $('foundText').textContent = `${s.result_count ?? 0}件`;
    $('progressNote').textContent = '';
  }
  updateTimes(s);
  const box = $('logBox');
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 10;
  box.textContent = (s.logs || []).join('\n');
  if (atBottom) box.scrollTop = box.scrollHeight;
  updateNav();
}

function updateTimes(s) {
  if (!s.started_at) { $('elapsedText').textContent = '—'; $('remainText').textContent = '—'; return; }
  const start = new Date(s.started_at).getTime();
  const end = s.state === 'running' ? Date.now() : (s.finished_at ? new Date(s.finished_at).getTime() : Date.now());
  const sec = Math.max(0, (end - start) / 1000);
  $('elapsedText').textContent = fmtDuration(sec);
  if (s.state !== 'running') { $('remainText').textContent = s.state === 'done' ? '完了' : '—'; return; }
  const total = s.total || 0;
  if (!total) { $('remainText').textContent = '計算中…'; return; }
  if (s.index < 1) { $('remainText').textContent = '1件目の完了後に表示'; return; }
  // ここまでの1キーワードあたりの平均時間から残りを見積もる
  const rest = sec / s.index * (total - s.index);
  const m = Math.round(rest / 60);
  $('remainText').textContent = m < 1 ? 'あと1分未満' : m >= 60 ? `あと約${Math.floor(m / 60)}時間${m % 60}分` : `あと約${m}分`;
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

function copyDealText(btnEl, text) {
  const done = () => {
    const original = btnEl.innerHTML;
    btnEl.textContent = 'コピーしました';
    setTimeout(() => { btnEl.innerHTML = original; }, 1500);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => {});
  }
}

function showResultsView() {
  renderResults();
  showView('result');
}

function profitTier(p) { return p >= 10000 ? 'high' : p >= 5000 ? 'mid' : 'low'; }

function renderResults() {
  const s = LAST || {};
  const metaParts = [];
  const stateLabel = { done: '完了', stopped: '途中で中止（ここまでの結果）', error: 'エラー' }[s.state];
  if (s.finished_at) metaParts.push(`${s.finished_at.replace('T', ' ').slice(0, 16)} ${stateLabel || ''}`);
  const cs = condSummary(s.conditions);
  if (cs) metaParts.push(`条件：${cs}`);
  $('resultMeta').textContent = metaParts.join('　');

  const all = RESULTS.map(r => ({ ...r, category: catName(r.category || '') }));
  // サマリー
  const total = all.reduce((a, r) => a + (Number(r.estimated_profit) || 0), 0);
  const best = all.slice().sort((a, b) => b.estimated_profit - a.estimated_profit)[0];
  $('summaryTiles').innerHTML = `
    <div class="tile"><div class="tile-label">該当件数</div><div class="tile-value">${all.length}<small>件</small></div></div>
    <div class="tile"><div class="tile-label">合計見込み利益</div><div class="tile-value profit">${yen(total)}</div></div>
    <a class="tile tile-best ${best ? '' : 'disabled'}" ${best ? `href="${esc(best.url)}" target="_blank" rel="noopener"` : ''}>
      <div class="tile-label">最高利益の商品</div>
      ${best ? `<div class="tile-value profit">${yen(best.estimated_profit)}</div><div class="tile-sub">${esc(best.brand || '')} ${esc(best.title)}</div>` : '<div class="tile-value">—</div>'}
    </a>`;

  // 絞り込みチップ（件数つき）
  const countBy = key => all.reduce((m, r) => (m[r[key]] = (m[r[key]] || 0) + 1, m), {});
  const srcCounts = countBy('source'), genCounts = countBy('category');
  if (filterSource && !srcCounts[filterSource]) filterSource = '';
  if (filterGenre && !genCounts[filterGenre]) filterGenre = '';
  const chip = (attr, val, label, n, cur) => `<button type="button" class="chip ${cur === val ? 'on' : ''}" ${attr}="${esc(val)}">${esc(label)} <span class="chip-n">${n}</span></button>`;
  $('sourceFilter').innerHTML = chip('data-fsource', '', 'すべて', all.length, filterSource) +
    Object.keys(srcCounts).map(k => chip('data-fsource', k, `${SOURCE_ICONS[k] || ''} ${SOURCE_LABELS[k] || k}`, srcCounts[k], filterSource)).join('');
  $('genreFilter').innerHTML = chip('data-fgenre', '', 'すべて', all.length, filterGenre) +
    Object.keys(genCounts).map(k => chip('data-fgenre', k, `${GENRE_ICONS[k] || ''} ${k || 'その他'}`, genCounts[k], filterGenre)).join('');
  document.querySelectorAll('#sortSeg [data-sort]').forEach(b => b.classList.toggle('on', b.dataset.sort === currentSort));

  const items = all
    .filter(r => (!filterSource || r.source === filterSource) && (!filterGenre || r.category === filterGenre))
    .sort((a, b) => {
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
  $('countLine').textContent = items.length === all.length ? `${items.length}件を表示中` : `${all.length}件中 ${items.length}件を表示中`;

  const container = $('cardsContainer');
  if (!all.length) {
    container.innerHTML = `<div class="empty">
      <div class="empty-icon">🔎</div>
      <div class="empty-title">条件に合う商品は見つかりませんでした</div>
      <div class="empty-text">最低利益額を下げる・仕入れ先を増やす・取得件数を増やすと見つかりやすくなります。</div>
      <button type="button" class="primary-btn" onclick="goStep(4, true)">条件を変えて再検索</button></div>`;
    return;
  }
  if (!items.length) {
    container.innerHTML = '<div class="empty"><div class="empty-title">この絞り込みに該当する商品はありません</div></div>';
    return;
  }
  container.innerHTML = items.map(d => {
    const srcLabel = SOURCE_LABELS[d.source] || d.source;
    const tier = profitTier(d.estimated_profit);
    const img = d.image_url
      ? `<img class="card-img" src="${esc(d.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'card-img noimg',textContent:'🏷️'}))">`
      : `<div class="card-img noimg">🏷️</div>`;
    const rs = remainSeconds(d);
    let remainBadge = '';
    if (rs !== null) {
      const urgent = rs <= 86400;
      remainBadge = `<span class="badge ${urgent ? 'b-urgent' : 'b-remain'}">⏱ ${d.is_auction ? '' : '即決 '}${formatRemain(rs)}</span>`;
    }
    const searchText = [d.brand, d.model].filter(Boolean).join(' ') || d.keyword;
    const searchTextAttr = esc(JSON.stringify(searchText));
    const mercariSearchUrl = `https://jp.mercari.com/search?keyword=${encodeURIComponent(searchText)}&status=sold_out&sort=created_time&order=desc&item_types=1`;
    return `
    <article class="card">
      <a class="card-media" href="${esc(d.url)}" target="_blank" rel="noopener">
        ${img}
        <span class="media-src">${SOURCE_ICONS[d.source] || ''} ${esc(srcLabel)}</span>
      </a>
      <div class="card-body">
        <div class="profit-box tier-${tier}">
          <div class="profit-label">見込み利益</div>
          <div class="profit-value">+${yen(d.estimated_profit)}</div>
          <div class="profit-roi">利益率 ${esc(d.roi_percent)}%</div>
        </div>
        <div class="price-flow">
          <div class="pf-box"><div class="pf-label">仕入れ</div><div class="pf-value">${yen(d.purchase_price)}</div></div>
          <div class="pf-arrow" aria-hidden="true">→</div>
          <div class="pf-box"><div class="pf-label">相場</div><div class="pf-value">${yen(d.reference_price)}</div></div>
        </div>
        <div class="card-badges">
          <span class="badge b-src">${esc(srcLabel)}</span>
          ${d.category ? `<span class="badge b-genre">${GENRE_ICONS[d.category] || ''} ${esc(d.category)}</span>` : ''}
          ${d.model ? `<span class="badge b-model">${esc(d.model)}</span>` : ''}
          ${remainBadge}
          ${d.condition_label && d.condition_label !== '状態不明' ? `<span class="badge b-cond">${esc(d.condition_label)}</span>` : ''}
        </div>
        <div class="card-brand">${esc(d.brand || '')}</div>
        <div class="card-title" title="${esc(d.title)}">${esc(d.title)}</div>
        <div class="card-actions">
          <button type="button" class="btn-sub" onclick="copyDealText(this, ${searchTextAttr})">📋 コピー</button>
          <a class="btn-sub btn-mercari" href="${esc(mercariSearchUrl)}" target="_blank" rel="noopener noreferrer">メルカリ相場を見る</a>
        </div>
        <a class="card-btn" href="${esc(d.url)}" target="_blank" rel="noopener">商品を見る →</a>
      </div>
    </article>`;
  }).join('');
}

// ── 初期化 ───────────────────────────────────────────────
async function init() {
  try {
    OPTIONS = await (await fetch('/api/options')).json();
  } catch (e) {
    document.querySelector('main').innerHTML = '<div class="empty"><div class="empty-title">サーバーに接続できません</div><div class="empty-text">start.command をダブルクリックして起動してください。</div></div>';
    return;
  }
  try { restoreState(); } catch (e) { console.warn('前回の選択を復元できませんでした', e); }
  // 最初に開くステップ: 必須が揃っているところまで（前回の選択が揃っていればステップ4は開かず1から）
  goStep(1);
  let s = null;
  try { s = await (await fetch('/api/status?results=1')).json(); } catch (e) { s = null; }
  if (!s) return;
  LAST = s;
  if (s.state === 'running') {
    showProgress(s);
    showView('progress');
    startPolling();
  } else if (s.state !== 'idle') {
    RESULTS = s.results || [];
    showProgress(s);
  }
  updateNav();
}
init();
