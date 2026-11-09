// ローカル リサーチ画面のロジック（app.py の /api/* と通信する）
// 画面構成: 条件選択（4ステップ） → 調査中 → 結果

const STORAGE_KEY = 'rieki-research-conditions-v1';   // 保存形式は従来のまま（前回の選択をそのまま復元）
const PHOTO_LOCAL_DEFAULT_VERSION = 2;   // 「写真で傷・汚れ確認（無料・Mac内AI）」を既定オンにした版（古い保存値の移行用）
const SOURCE_LABELS = { yahoo_auctions: 'ヤフオク', mercari_cheap: 'メルカリ安値', sekaist: 'セカスト', vector_park: 'ベクトルパーク', trefac: 'トレファク', rakuma: 'ラクマ', yahoo_flea: 'Yahoo!フリマ', yahoo_shopping: 'Yahoo!ショッピング' };
const SOURCE_ICONS = { yahoo_auctions: '🔨', mercari_cheap: '🛍️', sekaist: '👕', vector_park: '💼', trefac: '♻️', rakuma: '📦', yahoo_flea: '🏪', yahoo_shopping: '🛒' };
const SOURCE_DESC = { yahoo_auctions: '入札形式・即決の両方', mercari_cheap: 'メルカリの安い出品', sekaist: '', vector_park: 'ブランド買取店の販売', trefac: 'リユース店の通販', rakuma: 'フリマアプリ', yahoo_flea: 'フリマアプリ', yahoo_shopping: 'ストアの中古（送料込みで計算）' };
const GENRE_ICONS = { '時計': '⌚', 'ダウン': '🧥', 'サングラス': '🕶️', 'アクセサリー': '💍', 'ジュエリー': '💍', 'スカーフ': '🧣', 'バッグ': '👜', '財布': '👛', 'その他': '🏷️', 'セットアップ・スーツ': '🤵', 'ジャケット': '🧥', 'コート': '🧥', 'ダウン・中綿': '🧥', 'トップス': '👕', 'ボトムス': '👖', 'ワンピース': '👗', '靴': '👞', '小物': '🧣' };
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
const cond = { min_profit: 3000, min_roi: 0, auction_hours: '', auction_only: false, per_source: 20, free_numbers: '', photo_check: false, photo_ocr: true, photo_local: true, yahoo_shopping_new: false };

let currentStep = 1;
let RESULTS = [];
let LAST = null;               // 直近のステータス（結果つき）
let currentSort = 'profit';
let filterSource = '';
let filterGenre = '';
let filterConf = '';
let pollTimer = null;
let tickTimer = null;
let lastRunning = null;        // 実行中ステータス（経過時間の毎秒更新用）
// 実行中の途中経過: 見つかった案件はすぐ結果の画面に出す。結果の画面を見ている間に届いた分は
// PENDING に置き、「新しい案件 N件 — クリックで表示」を押したときに反映する（読んでいる途中で画面が動かないように）
let PENDING = null;
let liveVersionSeen = -1;

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
  ['setup', 'free', 'progress', 'result'].forEach(v => { $(v + 'View').hidden = v !== name; });
  $('navSetup').classList.toggle('on', name === 'setup');
  $('navFree').classList.toggle('on', name === 'free');
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
// ジャンル専用のモデル名（ルイヴィトンの スピーディ30 → バッグ）も、そのジャンルを選んでいるときだけ出す
function modelsFor(b) {
  const cats = (OPTIONS.model_categories || {})[b] || {};
  return (OPTIONS.models[b] || []).filter(m => !cats[m] || state.categories.has(cats[m]));
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
  const withOpts = brands.filter(b => modelsFor(b).length || numbersFor(b).length);
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
    const ms = modelsFor(b), ns = numbersFor(b);
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
  // 写真のタグ読み取り（無料・Mac内OCR）のチェック（この Mac で使えるときだけ表示）
  $('photoOcrCard').hidden = !OPTIONS.photo_ocr_available;
  $('photoOcr').checked = !!cond.photo_ocr;
  // 写真で傷・汚れ確認（無料・Mac内AI）: Ollama が入っている Mac だけ表示。起動していない・モデルが無いときは理由を出して押せなくする
  $('photoLocalCard').hidden = !OPTIONS.photo_local_installed;
  $('photoLocal').disabled = !OPTIONS.photo_local_available;
  $('photoLocal').checked = !!(OPTIONS.photo_local_available && cond.photo_local);
  $('photoLocalReason').hidden = !!OPTIONS.photo_local_available || !OPTIONS.photo_local_installed;
  $('photoLocalReason').textContent = OPTIONS.photo_local_reason ? `今は使えません: ${OPTIONS.photo_local_reason}` : '';
  // 写真AI確認（有料・Claude API）のチェック（この Mac で API キーが使えるときだけ表示。既定オフ）
  $('photoCheckCard').hidden = !OPTIONS.photo_check_available;
  $('photoCheck').checked = !!cond.photo_check;
  $('shoppingNew').checked = !!cond.yahoo_shopping_new;
  $('shoppingNewLine').hidden = !state.sources.has('yahoo_shopping');
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
    // モデル名展開の検索（search_all_models。タトラス・カナダグース）は全モデルの検索語も足す
    if (!targets.length) { n += 1; q += 1 + (k.model_queries || 0); return; }
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
  if (OPTIONS.photo_ocr_available) rows.push(['写真のタグ読み取り', cond.photo_ocr ? 'する（無料・Mac内OCR）' : 'しない']);
  if (OPTIONS.photo_local_available) rows.push(['写真で傷・汚れ確認', cond.photo_local ? 'する（無料・Mac内AI）' : 'しない']);
  if (OPTIONS.photo_check_available) rows.push(['写真AI確認（有料）', cond.photo_check ? 'する（Claude API・型番タグ・傷汚れ）' : 'しない（料金なし）']);
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
    if (isFreeView()) renderFree(); else renderStep();
    return;
  }
  if (handleFreeClick(e)) return;
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
  const fc = e.target.closest('[data-fconf]');
  if (fc) { filterConf = fc.dataset.fconf; renderResults(); return; }
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
  if (e.target.matches && e.target.matches('[data-shopping-new]')) { cond.yahoo_shopping_new = e.target.checked; saveState(); if (isFreeView()) renderFreeFinal(); else renderFinal(); }
  if (handleFreeChange(e)) return;
  if (e.target.id === 'auctionOnly') { cond.auction_only = e.target.checked; saveState(); renderFinal(); }
  if (e.target.id === 'photoCheck') { cond.photo_check = e.target.checked; saveState(); renderFinal(); }
  if (e.target.id === 'photoLocal') { cond.photo_local = e.target.checked; saveState(); renderFinal(); }
  if (e.target.id === 'photoOcr') { cond.photo_ocr = e.target.checked; saveState(); renderFinal(); }
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
  if (isFreeView()) renderFree(); else renderStep();
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
    photo_check: !!(OPTIONS.photo_check_available && cond.photo_check),
    photo_ocr: !!(OPTIONS.photo_ocr_available && cond.photo_ocr),
    photo_local: !!(OPTIONS.photo_local_available && cond.photo_local),
    yahoo_shopping_new: !!cond.yahoo_shopping_new,
  };
}

function saveState() {
  try {
    const c = collectConditions();
    c.brands = [...state.brands];   // 非表示中のブランド選択も覚えておく
    c.models = [...state.models];
    c.model_numbers = [...state.model_numbers];
    // 写真で傷・汚れ確認（無料・Mac内AI）: 一時的に Ollama が使えないときでも選択そのものを覚えておく。
    // photo_local_v は「既定オンになった後の保存」の印（これが無い古い保存値は既定オンに移行する）
    c.photo_local = !!cond.photo_local;
    c.photo_local_v = PHOTO_LOCAL_DEFAULT_VERSION;
    c.sources_v2 = 1;   // Yahoo!ショッピング追加後の保存の印
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
  // 写真AI確認（有料）: 料金がかかるため前回の選択は復元せず、毎回オフから始める（使うときだけチェックを入れる）
  cond.photo_check = !!d.photo_check;
  // 写真のタグ読み取り（無料）: 前回オフにしていればオフのまま。保存が無ければ既定（使える Mac ならオン）
  cond.photo_ocr = typeof saved?.photo_ocr === 'boolean' && OPTIONS.photo_ocr_available ? saved.photo_ocr : !!d.photo_ocr;
  // 写真で傷・汚れ確認（無料・Mac内AI）: 既定オンになった後に保存した選択だけ復元する。
  // それより前の保存値（既定オフ時代に自動で保存された false）は一度だけ既定（オン）に移行する
  const localSaved = saved && saved.photo_local_v === PHOTO_LOCAL_DEFAULT_VERSION && typeof saved.photo_local === 'boolean';
  cond.photo_local = localSaved ? saved.photo_local : !!d.photo_local;
  cond.yahoo_shopping_new = !!saved?.yahoo_shopping_new;
  // 前回の保存に Yahoo!ショッピング（新しい仕入れ先）が無ければ、他の仕入れ先を全部選んでいたときだけ足す
  if (saved && Array.isArray(saved.sources) && !saved.sources_v2) {
    const old = OPTIONS.sources.filter(s => s.key !== 'yahoo_shopping').every(s => saved.sources.includes(s.key));
    if (old) state.sources.add('yahoo_shopping');
  }
  restoreFree();
}

// ── リサーチ実行 ──────────────────────────────────────────
function isRunning() { return !!(LAST && LAST.state === 'running'); }

async function startResearch() {
  $('errorLine').textContent = '';
  const c = collectConditions();
  const err = stepError(4);
  if (err) { $('errorLine').textContent = err; return; }
  saveState();
  await postResearch(c, 'errorLine', 'startBtn');
}

async function postResearch(c, errId, btnId) {
  $(btnId).disabled = true;
  try {
    const res = await fetch('/api/research', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(c) });
    const data = await res.json();
    if (!data.ok) { $(errId).textContent = data.error || '開始できませんでした'; $(btnId).disabled = false; return; }
    RESULTS = [];
    PENDING = null;
    liveVersionSeen = -1;
    filterSource = ''; filterGenre = '';
    LAST = { state: 'running', index: 0, total: 0, keyword: '', started_at: new Date().toISOString(), logs: [], conditions: c, found_count: 0 };
    showProgress(LAST);
    showView('progress');
    startPolling();
  } catch (e) {
    $(errId).textContent = 'サーバーに接続できません（start.command で起動しているか確認してください）';
    $(btnId).disabled = false;
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
  tickTimer = setInterval(() => { if (lastRunning) { updateTimes(lastRunning); renderLiveBanner(); } }, 1000);
}

let polling = false;
async function pollStatus() {
  if (polling) return;   // 前回の問い合わせが終わっていなければ重ねない
  polling = true;
  try { await pollStatusOnce(); } finally { polling = false; }
}

async function pollStatusOnce() {
  let s;
  try { s = await (await fetch('/api/status')).json(); } catch (e) { return; }
  if (s.state === 'running') {
    LAST = s;
    // 見つかった案件が増えた・写真確認で変わったときだけ一覧を取り直す
    if (s.live && s.live.version !== liveVersionSeen) await fetchLive();
    showProgress(LAST);
    renderLiveBanner();
    return;
  }
  clearInterval(pollTimer);
  clearInterval(tickTimer);
  lastRunning = null;
  PENDING = null;
  liveVersionSeen = -1;
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

async function fetchLive() {
  let full;
  try { full = await (await fetch('/api/status?results=1')).json(); } catch (e) { return; }
  if (full.state !== 'running' || !full.live) return;
  liveVersionSeen = full.live.version;
  LAST = full;
  receiveLive(full.results || []);
}

// 途中経過の一覧を受け取る。結果の画面を見ていて、もう案件が表示されているときは差し替えずに「新しい案件」ボタンを出す
function receiveLive(rows) {
  const onResults = !$('resultView').hidden;
  if (!onResults || !RESULTS.length) {
    RESULTS = rows;
    PENDING = null;
    if (onResults) renderResults();
    return;
  }
  PENDING = liveDiff(RESULTS, rows).any ? rows : null;
}

function liveSig(r) {
  return [r.estimated_profit, r.photo_pending ? 1 : 0, r.photo_condition ? r.photo_condition.rank : '', r.condition_label].join('|');
}
function liveDiff(cur, next) {
  const before = new Map(cur.map(r => [r.url, liveSig(r)]));
  const after = new Set(next.map(r => r.url));
  let added = 0, changed = 0, removed = 0;
  next.forEach(r => {
    if (!before.has(r.url)) added++;
    else if (before.get(r.url) !== liveSig(r)) changed++;
  });
  cur.forEach(r => { if (!after.has(r.url)) removed++; });
  return { added, changed, removed, any: added + changed + removed > 0 };
}

function applyPending() {
  if (PENDING) RESULTS = PENDING;
  PENDING = null;
  renderResults();
}

// 進み具合の一文（例: 12 / 114 キーワード・検索 3 / 20 本）
function liveProgressText(s) {
  const total = s.total || 0;
  if (!total) return '準備中…';
  const qText = s.q_total > 1 ? `・検索 ${Math.min((s.q_index || 0) + 1, s.q_total)} / ${s.q_total} 本` : '';
  return `${Math.min((s.index || 0) + 1, total)} / ${total} キーワード${qText}`;
}

// 結果の画面の上の「リサーチ中」の帯（実行中だけ）
function renderLiveBanner() {
  const b = $('liveBanner');
  if (!b) return;
  const s = LAST;
  if (!s || s.state !== 'running') { b.hidden = true; return; }
  b.hidden = false;
  const lv = s.live || {};
  const n = s.result_count ?? RESULTS.length;
  const sec = s.started_at ? Math.max(0, (Date.now() - new Date(s.started_at).getTime()) / 1000) : 0;
  const what = lv.photo_phase
    ? `写真で傷・汚れを確認中：${Math.min(lv.photo_done || 0, lv.photo_total || 0)} / ${lv.photo_total || 0}件`
    : `検索中：${liveProgressText(s)}`;
  $('liveText').textContent = `🔍 ${what} ・ 見つかった案件 ${n}件 ・ 経過 ${fmtDuration(sec)}`;
  const sub = [];
  if (lv.photo_check && lv.pending) {
    sub.push(lv.photo_phase
      ? `📷 写真確認中の案件 ${lv.pending}件：確認が終わると状態と利益を計算し直します（利益が条件を下回った案件は一覧から外します）`
      : `📷「写真確認待ち」の案件 ${lv.pending}件：検索がすべて終わったあとに写真で状態を確認し、利益を計算し直します（利益が条件を下回った案件は一覧から外します）`);
  }
  if (lv.photo_dropped) sub.push(`写真確認で利益が条件を下回り、一覧から外した案件 ${lv.photo_dropped}件`);
  sub.push('途中経過です。リサーチが終わると最終の結果に置き換わります。');
  $('liveSub').textContent = sub.join('　');
  const btn = $('liveNewBtn');
  if (PENDING) {
    const d = liveDiff(RESULTS, PENDING);
    const parts = [];
    if (d.added) parts.push(`新しい案件 ${d.added}件`);
    if (d.changed) parts.push(`写真確認などで更新 ${d.changed}件`);
    if (d.removed) parts.push(`除外 ${d.removed}件`);
    btn.textContent = `${parts.join('・')} — クリックで表示`;
    btn.hidden = false;
  } else {
    btn.hidden = true;
  }
}

function updateNav() {
  const running = isRunning();
  $('navProgress').hidden = !running;
  const liveN = running ? (LAST.result_count || 0) : 0;
  $('navResults').hidden = running ? !liveN : (!LAST || LAST.state === 'idle' || LAST.state === 'error');
  if (!$('navResults').hidden) $('navResults').textContent = running ? `途中結果を見る（${liveN}件）` : `結果を見る（${(LAST.results || RESULTS).length}件）`;
  if (currentStep === 4) renderFinal();
  if (isFreeView()) renderFreeFinal();
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
  if (c.mode === 'free') return freeCondSummary(c);
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
  const liveN = running && s.live ? (s.result_count || 0) : 0;
  $('liveJump').hidden = !liveN;
  if (liveN) $('liveJumpBtn').textContent = `見つかった案件を今すぐ見る（${liveN}件）`;
  const total = s.total || 0;
  // キーワード内の何本目の検索か（q_index / q_total）も進み具合に含める（1キーワードで検索語が多いとき用）
  const qFrac = running ? searchFrac(s) : 0;
  const done = running ? s.index + qFrac : (s.state === 'done' ? total : s.index);
  const pct = total ? Math.round(done / total * 100) : 0;
  $('barFill').style.width = (running ? Math.max(pct, 2) : pct) + '%';
  $('bigBar').setAttribute('aria-valuenow', pct);
  $('bigBar').classList.toggle('running', running);
  $('pctText').textContent = `${pct}%`;
  const qText = running && s.q_total > 1 ? `・検索 ${Math.min((s.q_index || 0) + 1, s.q_total)} / ${s.q_total} 本` : '';
  $('kwCount').textContent = total ? `${running ? Math.min(s.index + 1, total) : done} / ${total} キーワード${qText}` : '準備中…';
  const titles = { running: '🔍 リサーチ中…', done: '✅ リサーチ完了', stopped: '⏹ リサーチを中止しました', error: '⚠ エラーで停止しました' };
  $('progressTitle').textContent = titles[s.state] || s.state;
  $('progressCond').textContent = condSummary(s.conditions);
  if (running) {
    $('nowBrand').textContent = s.keyword ? brandOfKeyword(s.keyword) : '準備中…';
    $('nowKeyword').textContent = s.keyword ? `検索キーワード：${s.keyword}` : 'ブラウザを起動しています';
    // 見つかった案件（条件・重複で絞り込み済みの途中経過）。古いサーバーの応答ではログの候補数
    $('foundText').textContent = `${s.live ? (s.result_count || 0) : (s.found_count || 0)}件`;
    $('progressNote').textContent = s.live
      ? '見つかった案件は、終わるのを待たずに結果の画面で見られます（最後に写真確認などで件数が変わることがあります）。'
      : '候補の数は途中経過です。最後に条件（モデル・利益率・重複）で絞り込んだ件数が結果になります。';
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
  renderPhotoRead(s.photo_read, s.photo_ocr, s.photo_local);
  const box = $('logBox');
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 10;
  box.textContent = (s.logs || []).join('\n');
  if (atBottom) box.scrollTop = box.scrollHeight;
  updateNav();
}

// キーワード内の検索の進み具合を、1キーワードの所要時間に占める割合で返す。
// 検索の後に説明文・相場の確認があり（タトラス全モデル・ラクマ+トレファクの実測で検索は全体の2割程度）、
// 検索が終わっても1キーワードの2割までしか進めない
const SEARCH_SHARE = 0.2;
function searchFrac(s) {
  return s.q_total ? Math.min(1, (s.q_index || 0) / s.q_total) * SEARCH_SHARE : 0;
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
  // キーワード内の検索の進み具合（q_index / q_total）も含める。検索語が多いキーワード（タトラスの全モデル）用。
  // 検索の後の説明文・相場の確認の時間は含まないので目安
  const done = s.index + searchFrac(s);
  if (done < 0.005) { $('remainText').textContent = s.q_total ? '数本の検索の後に表示' : '1件目の完了後に表示'; return; }
  // ここまでの1キーワードあたりの平均時間から残りを見積もる
  const rest = sec / done * (total - done);
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
  if (PENDING) { RESULTS = PENDING; PENDING = null; }
  renderResults();
  showView('result');
}

function profitTier(p) { return p >= 10000 ? 'high' : p >= 5000 ? 'mid' : 'low'; }

function renderResults() {
  const s = LAST || {};
  const running = s.state === 'running';
  renderLiveBanner();
  const metaParts = [];
  const stateLabel = { done: '完了', stopped: '途中で中止（ここまでの結果）', error: 'エラー' }[s.state];
  if (running) metaParts.push('リサーチ実行中（ここまでに見つかった案件）');
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
  // chip は下の信頼度の絞り込みでも使うので先に定義する（後に置くと ReferenceError で結果が表示されない）
  const chip = (attr, val, label, n, cur) => `<button type="button" class="chip ${cur === val ? 'on' : ''}" ${attr}="${esc(val)}">${esc(label)} <span class="chip-n">${n}</span></button>`;
  // フリーリサーチの結果だけ: 相場の信頼度で絞り込む
  const confCounts = all.reduce((m, r) => { const c = r.free_info && r.free_info.confidence; if (c) m[c] = (m[c] || 0) + 1; return m; }, {});
  $('confFilterRow').hidden = !Object.keys(confCounts).length;
  if (filterConf && !confCounts[filterConf]) filterConf = '';
  $('confFilter').innerHTML = chip('data-fconf', '', 'すべて', all.length, filterConf) +
    ['高', '中', '低'].filter(k => confCounts[k]).map(k => chip('data-fconf', k, `信頼度 ${k}`, confCounts[k], filterConf)).join('');
  $('sourceFilter').innerHTML = chip('data-fsource', '', 'すべて', all.length, filterSource) +
    Object.keys(srcCounts).map(k => chip('data-fsource', k, `${SOURCE_ICONS[k] || ''} ${SOURCE_LABELS[k] || k}`, srcCounts[k], filterSource)).join('');
  $('genreFilter').innerHTML = chip('data-fgenre', '', 'すべて', all.length, filterGenre) +
    Object.keys(genCounts).map(k => chip('data-fgenre', k, `${GENRE_ICONS[k] || ''} ${k || 'その他'}`, genCounts[k], filterGenre)).join('');
  document.querySelectorAll('#sortSeg [data-sort]').forEach(b => b.classList.toggle('on', b.dataset.sort === currentSort));

  const items = all
    .filter(r => (!filterSource || r.source === filterSource) && (!filterGenre || r.category === filterGenre)
      && (!filterConf || (r.free_info && r.free_info.confidence === filterConf)))
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
  if (!all.length && running) {
    container.innerHTML = `<div class="empty">
      <div class="empty-icon">⏳</div>
      <div class="empty-title">まだ見つかっていません</div>
      <div class="empty-text">リサーチ中です。案件が見つかりしだい、ここに表示されます。</div></div>`;
    return;
  }
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
    // フリーリサーチ（型番なし）は相場を出した検索語（例: ポールスミス セットアップ メンズ）でメルカリを開く
    const freeQ = d.free_info && !d.model && d.free_info.genre_market ? (d.free_info.genre_market.queries || [])[0] : '';
    const searchText = freeQ || [d.brand, d.model].filter(Boolean).join(' ') || d.keyword;
    const searchTextAttr = esc(JSON.stringify(searchText));
    const urlAttr = esc(JSON.stringify(d.url || ''));
    const mercariSearchUrl = `https://jp.mercari.com/search?keyword=${encodeURIComponent(searchText)}&status=sold_out&sort=created_time&order=desc&item_types=1`;
    const kindSearchUrl = `https://shop.kind.co.jp/search?q=${encodeURIComponent(searchText)}`;  // 手動で開くだけ（ツールからは取得しない）
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
          <div class="pf-box"><div class="pf-label">仕入れ${d.shipping != null ? '（送料込）' : ''}</div><div class="pf-value">${yen(d.purchase_price)}</div>${shippingNote(d)}</div>
          <div class="pf-arrow" aria-hidden="true">→</div>
          <div class="pf-box"><div class="pf-label">相場</div><div class="pf-value">${yen(d.reference_price)}</div></div>
        </div>
        <div class="card-badges">
          <span class="badge b-src">${esc(srcLabel)}</span>
          ${d.category ? `<span class="badge b-genre">${GENRE_ICONS[d.category] || ''} ${esc(d.category)}</span>` : ''}
          ${d.model ? `<span class="badge b-model">${esc(d.model)}</span>` : ''}
          ${remainBadge}
          ${d.photo_pending ? `<span class="badge b-pending" title="写真で傷・汚れを確認したあと、状態と利益を計算し直します">📷 ${s.live && s.live.photo_phase ? '写真確認中…' : '写真確認待ち'}</span>` : ''}
          ${d.condition_label && d.condition_label !== '状態不明' && !d.photo_condition ? `<span class="badge b-cond">${esc(d.condition_label)}</span>` : ''}
          ${freeBadges(d)}
        </div>
        ${freeInfoHtml(d)}
        ${photoConditionHtml(d)}
        <div class="card-brand">${esc(d.brand || '')}</div>
        <div class="card-title" title="${esc(d.title)}">${esc(d.title)}</div>
        <div class="card-actions">
          <button type="button" class="btn-sub" onclick="copyDealText(this, ${searchTextAttr})">📋 コピー</button>
          <a class="btn-sub btn-mercari" href="${esc(mercariSearchUrl)}" target="_blank" rel="noopener noreferrer">メルカリ相場を見る</a>
          <a class="btn-sub btn-kind" href="${esc(kindSearchUrl)}" target="_blank" rel="noopener noreferrer">KINDで見る</a>
          <button type="button" class="btn-sub btn-url" title="出品URLをコピー（Claude Code に貼って写真を確認してもらう用）" onclick="copyDealText(this, ${urlAttr})">🔗 URLをコピー</button>
        </div>
        <a class="card-btn" href="${esc(d.url)}" target="_blank" rel="noopener">商品を見る →</a>
      </div>
    </article>`;
  }).join('');
}

// 写真から AI が判定した状態（ランクのバッジ＋見つかった傷・汚れ＋確認できなかった箇所）
const PHOTO_RANK_CLASS = { '新品同様': 'pr-1', '目立った傷なし': 'pr-2', 'やや傷あり': 'pr-3', '傷あり': 'pr-4', '状態悪い': 'pr-5' };
function photoConditionHtml(d) {
  const p = d.photo_condition;
  if (!p || !p.rank) return '';
  const issues = Array.isArray(p.issues) ? p.issues : [];
  const local = p.source === 'local';
  const list = issues.length
    ? `<ul class="pc-issues">${issues.map(x => `<li><span class="sev sev-${esc(x.severity)}">${esc(x.severity)}</span>${esc(x.location)}${x.location && x.issue ? 'の' : ''}${esc(x.issue)}</li>`).join('')}</ul>`
    : '<div class="pc-none">写真で傷・汚れは見つかりませんでした</div>';
  const used = d.condition_label && d.condition_label.includes('写真')
    ? `相場の計算に写真の判定を使用（${esc(d.condition_label)}）`
    : `相場の計算はタイトルの状態（${esc(d.text_condition_label || d.condition_label || '状態不明')}）を使用`;
  return `<div class="photo-cond">
    <div class="pc-head"><span class="badge pc-rank ${PHOTO_RANK_CLASS[p.rank] || ''}">📷 ${esc(p.rank)}</span>${local ? '<span class="pc-src">無料AIの判定</span>' : ''}<span class="pc-conf">確信度 ${esc(p.confidence || '—')}</span></div>
    ${list}
    ${p.unchecked ? `<div class="pc-unchecked">写真で確認できなかった箇所: ${esc(p.unchecked)}</div>` : ''}
    <div class="pc-note">※ ${local ? `Mac内の無料AI（${esc(p.model || 'Ollama')}）による出品写真の判定です。有料の判定より見落とし・誤判定が多いので、必ず写真を自分の目で確認してください` : '出品写真からのAI判定です（見落とし・誤判定があり得ます）'}。${used}</div>
  </div>`;
}

// ── フリーリサーチ ─────────────────────────────────────
const FREE_KEY = 'rieki-free-conditions-v1';
const FREE_GENDERS = ['指定なし', 'メンズ', 'レディース'];
const free = { brands: [], genres: new Set(), custom: '', price_min: 5000, price_max: 15000, gender: '指定なし' };

function isFreeView() { return !$('freeView').hidden; }
function showFree() { showView('free'); renderFree(); }
function backToConditions() {
  if (LAST && LAST.conditions && LAST.conditions.mode === 'free') showFree(); else goStep(4, true);
}
function freeOpts() { return OPTIONS.free || { brands: [], genres: [], features: [], seed_count: 0 }; }

function restoreFree() {
  let f = null;
  try { f = JSON.parse(localStorage.getItem(FREE_KEY) || 'null'); } catch (e) { f = null; }
  if (!f || typeof f !== 'object') return;
  if (Array.isArray(f.brands)) free.brands = f.brands.filter(b => typeof b === 'string' && b.trim()).slice(0, 20);
  const known = new Set(freeOpts().genres.map(g => g.key));
  if (Array.isArray(f.genres)) f.genres.forEach(g => { if (known.has(g)) free.genres.add(g); });
  if (typeof f.custom === 'string') free.custom = f.custom;
  if (isFinite(Number(f.price_min))) free.price_min = Math.max(0, Number(f.price_min));
  if (isFinite(Number(f.price_max))) free.price_max = Math.max(0, Number(f.price_max));
  if (FREE_GENDERS.includes(f.gender)) free.gender = f.gender;
}
function saveFree() {
  try {
    localStorage.setItem(FREE_KEY, JSON.stringify({ brands: free.brands, genres: [...free.genres], custom: free.custom,
      price_min: free.price_min, price_max: free.price_max, gender: free.gender }));
  } catch (e) { /* 保存できなくても動作には影響なし */ }
  saveState();
}

function addFreeBrand(name) {
  name = String(name || '').trim();
  if (!name) return;
  name.split(/[,、，]+/).map(x => x.trim()).filter(Boolean).forEach(b => { if (!free.brands.includes(b)) free.brands.push(b); });
  saveFree();
  renderFree();
}
function addFreeBrandFromInput() {
  const el = $('freeBrandInput');
  addFreeBrand(el.value);
  el.value = '';
}

function renderFree() {
  if (!OPTIONS) return;
  const fo = freeOpts();
  $('freeBrandList').innerHTML = fo.brands.map(b => `<option value="${esc(b.name)}">${b.count ? `${b.count}件・平均利益${yen(b.avg_profit)}` : '登録済みのブランド'}</option>`).join('');
  $('freeBrandPicked').innerHTML = free.brands.length
    ? free.brands.map(b => `<button type="button" class="chip on" data-free-brand-del="${esc(b)}">✓ ${esc(b)} ✕</button>`).join('')
    : '<span class="hint">ブランドを入力するか、下の候補から選んでください</span>';
  $('freeBrandSuggest').innerHTML = fo.brands.filter(b => b.count > 0).slice(0, 24).map(b => {
    const on = free.brands.includes(b.name);
    return `<button type="button" class="chip ${on ? 'on' : ''}" data-free-brand="${esc(b.name)}">${on ? '✓ ' : ''}${esc(b.name)} <span class="chip-n">${b.count}件・${yen(b.avg_profit)}</span></button>`;
  }).join('');
  // ジャンル: 利益商品共有の件数。選んだブランドの実績があれば「このブランドの実績」も添える
  const brandGenres = {};
  fo.brands.filter(b => free.brands.includes(b.name)).forEach(b => Object.entries(b.genres || {}).forEach(([k, n]) => { brandGenres[k] = (brandGenres[k] || 0) + n; }));
  $('freeGenreChips').innerHTML = fo.genres.map(g => {
    const on = free.genres.has(g.key);
    const bn = brandGenres[g.key];
    return `<button type="button" class="chip ${on ? 'on' : ''}" data-free-genre="${esc(g.key)}">${on ? '✓ ' : ''}${g.icon || ''} ${esc(g.label)} <span class="chip-n">${bn ? `このブランド${bn}件・` : ''}全体${g.count}件</span></button>`;
  }).join('');
  $('freeGenreHint').textContent = fo.seed_count ? `件数は利益商品共有（${fo.seed_count}件）で利益が出た商品の数です` : '';
  if (document.activeElement !== $('freeCustomGenre')) $('freeCustomGenre').value = free.custom;
  if (document.activeElement !== $('freePriceMin')) $('freePriceMin').value = free.price_min;
  if (document.activeElement !== $('freePriceMax')) $('freePriceMax').value = free.price_max;
  $('freeGenderSeg').innerHTML = FREE_GENDERS.map(g => `<button type="button" class="${free.gender === g ? 'on' : ''}" data-free-gender="${g}">${g}</button>`).join('');
  if (document.activeElement !== $('freeMinProfit')) $('freeMinProfit').value = cond.min_profit;
  if (document.activeElement !== $('freeMinRoi')) $('freeMinRoi').value = cond.min_roi;
  $('freePerSourceSeg').innerHTML = PER_SOURCE_PRESETS.map(([v, l]) =>
    `<button type="button" class="${Number(cond.per_source) === v ? 'on' : ''}" data-per="${v}"><b>${l}</b><span>${v}件</span></button>`).join('');
  $('freePhotoOcrLine').hidden = !OPTIONS.photo_ocr_available;
  $('freePhotoOcr').checked = !!cond.photo_ocr;
  $('freePhotoLocalLine').hidden = !OPTIONS.photo_local_installed;
  $('freePhotoLocal').disabled = !OPTIONS.photo_local_available;
  $('freePhotoLocal').checked = !!(OPTIONS.photo_local_available && cond.photo_local);
  $('freePhotoCheckLine').hidden = !OPTIONS.photo_check_available;
  $('freePhotoCheck').checked = !!cond.photo_check;
  $('freeSourceGrid').innerHTML = OPTIONS.sources.map(s => {
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
  $('freeShoppingNew').checked = !!cond.yahoo_shopping_new;
  renderFreeFinal();
}

function freeGenreLabels(keys, custom) {
  const fo = freeOpts();
  const labels = (keys || []).map(k => (fo.genres.find(g => g.key === k) || {}).label).filter(Boolean);
  return labels.concat(String(custom || '').split(/[,、，]+/).map(x => x.trim()).filter(Boolean));
}
function freeError() {
  if (!free.brands.length) return 'ブランドを1つ以上入力してください';
  if (!freeGenreLabels([...free.genres], free.custom).length) return 'ジャンルを1つ以上選ぶか、自由入力してください';
  if (!state.sources.size) return '仕入れ先を1つ以上選んでください';
  if (Number(free.price_max) > 0 && Number(free.price_min) > Number(free.price_max)) return '仕入れ値の範囲（下限 > 上限）を見直してください';
  return '';
}
// 所要時間の目安（分）: 検索語（ブランドの表記 × ジャンルの検索語）× 仕入れ先 + 相場 + 説明文の確認
function freeEstimate() {
  const fo = freeOpts();
  const enSet = new Set(fo.en_brands || []);
  const en = free.brands.reduce((a, b) => a + (enSet.has(b) ? 2 : 1), 0);   // 英字名でも検索するブランドは2本
  const gq = [...free.genres].reduce((a, k) => a + ((fo.genres.find(g => g.key === k) || {}).queries || 1), 0)
    + freeGenreLabels([], free.custom).length;
  const q = en * gq;
  const src = Math.max(1, state.sources.size);
  const per = Number(cond.per_source) || 20;
  const total = q * src * (0.1 + per / 120) + free.brands.length * gq * 0.4 + q * src * per * 0.35 / 60;
  return { q: Math.round(q), lo: Math.max(1, Math.round(total * 0.6)), hi: Math.max(2, Math.round(total * 1.5)) };
}
function renderFreeFinal() {
  if (!OPTIONS || !$('freeSummary')) return;
  const g = freeGenreLabels([...free.genres], free.custom);
  const srcs = OPTIONS.sources.filter(s => state.sources.has(s.key)).map(s => s.label);
  const rows = [
    ['ブランド', free.brands.join('・')],
    ['ジャンル', g.join('・')],
    ['仕入れ値', `${num(free.price_min)}円〜${Number(free.price_max) > 0 ? num(free.price_max) + '円' : '上限なし'}（送料込み）`],
    ['性別', free.gender],
    ['仕入れ先', srcs.join('・') + (state.sources.has('yahoo_shopping') ? (cond.yahoo_shopping_new ? '（Yahoo!ショッピングは新品も）' : '（Yahoo!ショッピングは中古のみ）') : '')],
    ['利益', profitText()],
    ['取得件数', `${cond.per_source}件`],
  ];
  $('freeSummary').innerHTML = rows.map(([k, v]) => `<div class="fs-row"><dt>${k}</dt><dd class="${v ? '' : 'missing'}">${esc(v || '未選択')}</dd></div>`).join('');
  const err = freeError();
  const e = freeEstimate();
  const f = m => (m >= 60 ? `${Math.floor(m / 60)}時間${m % 60 ? (m % 60) + '分' : ''}` : `${m}分`);
  $('freeFinalLine').innerHTML = err ? `<span class="warn">⚠ ${esc(err)}</span>`
    : `検索語 約<b>${e.q}</b>本 × 仕入れ先 <b>${state.sources.size}</b>か所・所要時間の目安 <b>約${f(e.lo)}〜${f(e.hi)}</b>（写真の確認の時間は別）`;
  $('freeStartBtn').disabled = !!err || isRunning();
}

function collectFreeConditions() {
  const c = collectConditions();
  return { ...c, mode: 'free', categories: [], brands: [], models: [], model_numbers: [], free_numbers: '',
    free_brands: free.brands.slice(), free_genres: [...free.genres], free_custom_genre: free.custom,
    free_price_min: String(free.price_min), free_price_max: String(free.price_max), free_gender: free.gender };
}
async function startFreeResearch() {
  $('freeErrorLine').textContent = '';
  const err = freeError();
  if (err) { $('freeErrorLine').textContent = err; return; }
  saveFree();
  await postResearch(collectFreeConditions(), 'freeErrorLine', 'freeStartBtn');
}
function freeCondSummary(c) {
  const parts = [`フリーリサーチ：${(c.free_brands || []).join('・')}`, freeGenreLabels(c.free_genres, c.free_custom_genre).join('・')];
  if (c.free_gender && c.free_gender !== '指定なし') parts.push(c.free_gender);
  parts.push(`${num(c.free_price_min)}〜${num(c.free_price_max)}円`);
  if (c.min_profit !== undefined && c.min_profit !== '') parts.push(`利益${num(c.min_profit)}円以上`);
  return parts.filter(Boolean).join(' / ');
}

function handleFreeClick(e) {
  const t = e.target;
  const del = t.closest('[data-free-brand-del]');
  if (del) { free.brands = free.brands.filter(b => b !== del.dataset.freeBrandDel); saveFree(); renderFree(); return true; }
  const sb = t.closest('[data-free-brand]');
  if (sb) {
    const b = sb.dataset.freeBrand;
    if (free.brands.includes(b)) free.brands = free.brands.filter(x => x !== b); else free.brands.push(b);
    saveFree(); renderFree(); return true;
  }
  const g = t.closest('[data-free-genre]');
  if (g) { const k = g.dataset.freeGenre; if (free.genres.has(k)) free.genres.delete(k); else free.genres.add(k); saveFree(); renderFree(); return true; }
  const gd = t.closest('[data-free-gender]');
  if (gd) { free.gender = gd.dataset.freeGender; saveFree(); renderFree(); return true; }
  const ps = t.closest('#freePerSourceSeg [data-per]');
  if (ps) { cond.per_source = Number(ps.dataset.per); saveState(); renderFree(); return true; }
  return false;
}
function handleFreeChange(e) {
  const id = e.target.id;
  if (id === 'freeCustomGenre') { free.custom = e.target.value; saveFree(); renderFree(); return true; }
  if (id === 'freePriceMin') { free.price_min = Math.max(0, Number(e.target.value) || 0); saveFree(); renderFree(); return true; }
  if (id === 'freePriceMax') { free.price_max = Math.max(0, Number(e.target.value) || 0); saveFree(); renderFree(); return true; }
  if (id === 'freeMinProfit') { cond.min_profit = Math.max(0, Number(e.target.value) || 0); saveState(); renderFree(); return true; }
  if (id === 'freeMinRoi') { cond.min_roi = Math.max(0, Number(e.target.value) || 0); saveState(); renderFree(); return true; }
  if (id === 'freePhotoOcr') { cond.photo_ocr = e.target.checked; saveState(); renderFreeFinal(); return true; }
  if (id === 'freePhotoLocal') { cond.photo_local = e.target.checked; saveState(); renderFreeFinal(); return true; }
  if (id === 'freePhotoCheck') { cond.photo_check = e.target.checked; saveState(); renderFreeFinal(); return true; }
  return false;
}
document.addEventListener('keydown', e => {
  if (e.target && e.target.id === 'freeBrandInput' && e.key === 'Enter' && !e.isComposing) { e.preventDefault(); addFreeBrandFromInput(); }
});

// 送料（Yahoo!ショッピング）
function shippingNote(d) {
  if (d.shipping == null) return '';
  if (!d.shipping) return '<div class="pf-note">送料無料</div>';
  return `<div class="pf-note">本体${yen(d.base_price)}＋送料${yen(d.shipping)}${d.postage_estimated ? '（仮）' : ''}</div>`;
}
// フリーリサーチ: 信頼度・性別のバッジ
function freeBadges(d) {
  const f = d.free_info;
  if (!f) return '';
  const g = f.gender === '不明' ? '性別不明' : f.gender;
  return `<span class="badge conf conf-${esc(f.confidence)}">相場の信頼度 ${esc(f.confidence)}</span><span class="badge b-gender">${esc(g)}</span>`;
}
// フリーリサーチ: 相場の根拠・下振れ時の利益・注目ポイント
function freeInfoHtml(d) {
  const f = d.free_info;
  if (!f) return '';
  const gm = f.genre_market, mm = f.model_market;
  let basis = '';
  if (f.basis === 'model' && mm) {
    basis = `型番 <b>${esc(mm.model)}</b>（${esc(mm.from)}で判明）の売り切れ ${num(mm.n)}件・中央値 ${yen(mm.median)}（${yen(mm.min)}〜${yen(mm.max)}）で計算`;
    if (gm) basis += `<br><span class="fi-sub">参考: 同じブランド・種類の売り切れ ${num(gm.n)}件・中央値 ${yen(gm.median)}</span>`;
  } else if (gm) {
    basis = `メルカリ売り切れ ${num(gm.n)}件（「${esc((gm.queries || []).join('」「'))}」）・中央値 <b>${yen(gm.median)}</b>・中央50%は ${yen(gm.q1)}〜${yen(gm.q3)}（ばらつき ${Math.round(gm.spread * 100)}%）`;
  }
  const range = f.profit_low != null
    ? `<div class="fi-range">利益の幅（相場の中央50%で計算）: <b class="${f.profit_low < 0 ? 'neg' : ''}">${f.profit_low >= 0 ? '+' : ''}${yen(f.profit_low)}</b> 〜 <b>+${yen(f.profit_high)}</b></div>` : '';
  const feats = (f.features || []).map(x => {
    let cmp = '';
    if (x.without_median != null && x.with_n) {
      const sign = x.diff_pct > 0 ? '+' : '';
      cmp = `：売り切れでこの特徴あり ${yen(x.with_median)}（${x.with_n}件）/ なし ${yen(x.without_median)}（${x.without_n}件）<b class="${x.diff_pct >= 0 ? 'pos' : 'neg'}">${sign}${x.diff_pct}%</b>`;
    } else if (x.with_n) {
      cmp = `：売り切れでこの特徴あり ${yen(x.with_median)}（${x.with_n}件・比べる件数が少ない）`;
    }
    return `<li><span class="fi-tag">${esc(x.tag)}</span><small>${esc(x.where || '')}</small>${cmp}</li>`;
  }).join('');
  const note = f.basis === 'model' ? '' : '<div class="fi-note">※ 型番ではなく「ブランド＋アイテムの種類」の相場です。モデル・素材・サイズで値段が大きく違うことがあるので、メルカリで似た商品の売り切れを確認してください</div>';
  return `<div class="free-info">
    <div class="fi-head">📊 相場の根拠</div>
    <div class="fi-basis">${basis}</div>
    ${range}
    ${feats ? `<div class="fi-head">⭐ 注目ポイント</div><ul class="fi-feats">${feats}</ul>` : ''}
    ${(f.also_on || []).length ? `<div class="fi-sub">同じ商品が他の仕入れ先にも: ${f.also_on.map(a => `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(SOURCE_LABELS[a.source] || a.source)} ${yen(a.price)}</a>`).join('・')}</div>` : ''}
    ${note}
  </div>`;
}

// ── 初期化 ───────────────────────────────────────────────
// 写真から型番を読む機能の有効/無効（ヘッダーに小さく）と、スキャン中・後の読み取り件数・概算料金（調査中の画面）
function renderPhotoRead(p, o, l) {
  if (!p && !o && !l) return;
  $('photoStatus').hidden = false;
  $('photoStatus').textContent = [o && o.label, p && p.label].filter(Boolean).join(' / ');
  const lines = [];
  const ost = (o && o.stats) || {};
  if (o && o.available && ost.targets) {
    lines.push(`🔎 写真のタグ読み取り（無料）：型番 採用 ${num(ost.adopted)}件 / 対象 ${num(ost.targets)}件（キャッシュ ${num(ost.cached)}件）・写真 ${num(ost.images)}枚`);
  }
  const st = (p && p.stats) || {};
  if (p && p.enabled && (st.targets || st.inspect_targets || st.api_calls)) {
    lines.push(`📷 写真AI確認（有料）：型番 採用 ${num(st.adopted)}件 / 対象 ${num(st.targets)}件・傷汚れ 判定 ${num(st.inspected)}件（状態を下げて残した ${num(st.downgraded)}件・利益不足で除外 ${num(st.dropped)}件）・API ${num(st.api_calls)}回・概算 ${num(Math.round(st.cost_yen || 0))}円`
      + (st.stopped ? '（料金上限で停止）' : ''));
  }
  const lst = (l && l.stats) || {};
  if (l && lst.inspect_targets) {
    lines.push(`🖥 写真で傷・汚れ確認（無料・Mac内AI）：判定 ${num(lst.inspected)}件 / 対象 ${num(lst.inspect_targets)}件（キャッシュ ${num(lst.inspect_cached)}件・状態を下げて残した ${num(lst.downgraded)}件・利益不足で除外 ${num(lst.dropped)}件）・写真 ${num(lst.photos)}枚`);
  }
  $('photoStats').hidden = !lines.length;
  if (lines.length) $('photoStats').textContent = lines.join('　');
}

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
  renderPhotoRead(s.photo_read, s.photo_ocr, s.photo_local);
  LAST = s;
  if (s.state === 'running') {
    // ページを開き直したとき: ここまでに見つかった案件をそのまま表示できるようにする
    RESULTS = s.results || [];
    liveVersionSeen = s.live ? s.live.version : -1;
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
