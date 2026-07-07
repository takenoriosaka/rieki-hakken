// ダッシュボードのフィルタ・並べ替え・描画ロジック。
// DEALS / MARKETS / SETTINGS / GENERATED_AT は index.html 内のインライン <script> で定義済み。

const SOURCE_LABELS = { yahoo_auctions: 'ヤフオク', mercari_cheap: 'メルカリ安値', sekaist: 'セカスト', vector_park: 'ベクトルパーク', trefac: 'トレファク', rakuma: 'ラクマ', yahoo_flea: 'Yahoo!フリマ' };
const SOURCE_BADGE_CLASS = { yahoo_auctions: 'badge-yahoo', mercari_cheap: 'badge-mercari', sekaist: 'badge-sekaist', vector_park: 'badge-vectorpark', trefac: 'badge-trefac', rakuma: 'badge-rakuma', yahoo_flea: 'badge-yahoofuri' };

let currentCategory = '';
let currentBrand = '';
let currentSort = 'profit';

// ── カテゴリタブを生成（件数は全件基準） ──────────────────────
function buildCategoryTabs() {
  const bar = document.getElementById('tabBar');
  const categories = [...new Set(DEALS.map(d => d.category).filter(Boolean))].sort();
  bar.innerHTML = '';

  const allBtn = document.createElement('button');
  allBtn.className = 'tab active';
  allBtn.textContent = `すべて (${DEALS.length})`;
  allBtn.onclick = () => filterCategory('', allBtn);
  bar.appendChild(allBtn);

  categories.forEach(cat => {
    const count = DEALS.filter(d => d.category === cat).length;
    const btn = document.createElement('button');
    btn.className = 'tab';
    btn.textContent = `${cat} (${count})`;
    btn.onclick = () => filterCategory(cat, btn);
    bar.appendChild(btn);
  });
}

function filterCategory(cat, btnEl) {
  currentCategory = cat;
  currentBrand = '';
  document.querySelectorAll('#tabBar .tab').forEach(b => b.classList.remove('active'));
  btnEl.classList.add('active');
  buildBrandChips();
  render();
}

// ── ブランドチップを生成（選択中カテゴリに応じて絞り込み） ─────
function buildBrandChips() {
  const bar = document.getElementById('chipBar');
  bar.innerHTML = '';
  const scoped = currentCategory ? DEALS.filter(d => d.category === currentCategory) : DEALS;

  const allBtn = document.createElement('button');
  allBtn.className = 'chip active';
  allBtn.textContent = `すべて (${scoped.length})`;
  allBtn.onclick = () => filterBrand('', allBtn);
  bar.appendChild(allBtn);

  const brandCounts = {};
  scoped.forEach(d => {
    if (d.brand) brandCounts[d.brand] = (brandCounts[d.brand] || 0) + 1;
  });
  Object.keys(brandCounts).sort().forEach(b => {
    const btn = document.createElement('button');
    btn.className = 'chip';
    btn.textContent = `${b} (${brandCounts[b]})`;
    btn.onclick = () => filterBrand(b, btn);
    bar.appendChild(btn);
  });
}

function filterBrand(brand, btnEl) {
  currentBrand = brand;
  document.querySelectorAll('#chipBar .chip').forEach(c => c.classList.remove('active'));
  btnEl.classList.add('active');
  render();
}

// ── 「{brand} {model}」をクリップボードにコピー ─────────────────
function copyDealText(btnEl, text) {
  if (!(navigator.clipboard && navigator.clipboard.writeText)) {
    console.error('クリップボードAPIが利用できません');
    return;
  }
  const original = btnEl.textContent;
  navigator.clipboard.writeText(text).then(() => {
    btnEl.textContent = 'コピーしました';
    setTimeout(() => { btnEl.textContent = original; }, 1500);
  }).catch(err => {
    console.error('コピーに失敗しました', err);
  });
}

function setSort(s, btnEl) {
  currentSort = s;
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  btnEl.classList.add('active');
  render();
}

// ── 除外キーワード（カンマ・読点・空白区切り） ──────────────────
function getExcludeTerms() {
  const raw = (document.getElementById('excludeInput').value || '').trim();
  if (!raw) return [];
  return raw.split(/[,、\s]+/).map(t => t.toLowerCase()).filter(Boolean);
}

// ── 相場表を生成（検索ボックスで絞り込み） ───────────────────
function buildMarkets() {
  const query = (document.getElementById('marketSearch').value || '').toLowerCase();
  const el = document.getElementById('marketTable');
  const filtered = query ? MARKETS.filter(m => m.keyword.toLowerCase().includes(query)) : MARKETS;
  if (!filtered.length) {
    el.innerHTML = '<div style="color:#bbb;font-size:12px">データなし</div>';
    return;
  }
  el.innerHTML = filtered.map(m =>
    `<div class="mkt-row">
       <span class="mkt-kw" title="${m.keyword}">${m.keyword}</span>
       <span class="mkt-price">¥${Number(m.median_price).toLocaleString()}</span>
     </div>`
  ).join('');
}

function render() {
  const query = (document.getElementById('searchInput').value || '').toLowerCase();
  const excludeTerms = getExcludeTerms();
  const priceMin = parseFloat(document.getElementById('priceMin').value);
  const priceMax = parseFloat(document.getElementById('priceMax').value);

  let items = DEALS.filter(d => {
    if (currentCategory && d.category !== currentCategory) return false;
    if (currentBrand && d.brand !== currentBrand) return false;
    if (query && !d.title.toLowerCase().includes(query)) return false;
    if (excludeTerms.length && excludeTerms.some(t => d.title.toLowerCase().includes(t))) return false;
    if (!isNaN(priceMin) && d.purchase_price < priceMin) return false;
    if (!isNaN(priceMax) && d.purchase_price > priceMax) return false;
    return true;
  });

  items = items.slice().sort((a, b) => {
    if (currentSort === 'profit')      return b.estimated_profit - a.estimated_profit;
    if (currentSort === 'roi')         return b.roi_percent - a.roi_percent;
    if (currentSort === 'price')       return a.purchase_price - b.purchase_price;
    if (currentSort === 'price_desc')  return b.purchase_price - a.purchase_price;
    return b.scanned_at < a.scanned_at ? -1 : 1; // new
  });

  document.getElementById('countLine').textContent = `表示 ${items.length} / 全${DEALS.length}件`;

  const container = document.getElementById('cardsContainer');
  if (!items.length) {
    container.innerHTML = '<div class="empty">条件に合う案件がありません</div>';
    return;
  }

  container.innerHTML = items.map(d => {
    const srcLabel = SOURCE_LABELS[d.source] || d.source;
    const srcClass = SOURCE_BADGE_CLASS[d.source] || 'badge-mercari';
    const profitClass = d.estimated_profit >= 10000 ? 'profit-high' : d.estimated_profit >= 5000 ? 'profit-mid' : 'profit-low';
    const img = d.image_url
      ? `<img class="card-img" src="${d.image_url}" loading="lazy" onerror="this.style.display='none'">`
      : `<div class="card-img-placeholder">🏷️</div>`;
    const modelBadge = d.model ? `<span class="badge badge-cond">${d.model}</span>` : '';
    const categoryBadge = d.category ? `<span class="badge badge-category">${d.category}</span>` : '';
    const searchText = [d.brand, d.model].filter(Boolean).join(' ');
    const searchTextAttr = JSON.stringify(searchText).replace(/"/g, '&quot;');
    const mercariSearchUrl = `https://jp.mercari.com/search?keyword=${encodeURIComponent(searchText)}`;
    return `
    <div class="card">
      ${img}
      <div class="card-body">
        <div class="card-badges">
          ${d.brand ? `<span class="badge badge-brand">${d.brand}</span>` : ''}
          ${categoryBadge}
          <span class="badge ${srcClass}">${srcLabel}</span>
          ${modelBadge}
          ${d.condition_label !== '状態不明' ? `<span class="badge badge-cond">${d.condition_label}</span>` : ''}
        </div>
        <div class="card-title">${d.title}</div>
        <div class="card-prices">仕入れ <span>¥${Number(d.purchase_price).toLocaleString()}</span> → 相場 <span>¥${Number(d.reference_price).toLocaleString()}</span></div>
        <div class="card-profit ${profitClass}">¥${Number(d.estimated_profit).toLocaleString()} 利益 <span style="font-size:13px;font-weight:400;color:#888">利益率 ${d.roi_percent}%</span></div>
        <div class="card-actions-secondary">
          <button type="button" class="btn-secondary btn-copy" onclick="copyDealText(this, ${searchTextAttr})">📋 コピー</button>
          <a class="btn-secondary btn-mercari" href="${mercariSearchUrl}" target="_blank" rel="noopener noreferrer">メルカリで見る</a>
        </div>
        <a class="card-btn" href="${d.url}" target="_blank" rel="noopener">商品を見る →</a>
      </div>
    </div>`;
  }).join('');
}

// ── 利益計算（config.json の手数料・送料設定を使用） ────────────
function calcProfit() {
  const sell = parseFloat(document.getElementById('calcSell').value) || 0;
  const buy  = parseFloat(document.getElementById('calcBuy').value) || 0;
  const el   = document.getElementById('calcResult');
  if (!sell || !buy) { el.textContent = '— 利益を計算 —'; el.className = 'calc-result'; return; }
  const profit = Math.round(sell * (1 - SETTINGS.commission_rate) - SETTINGS.shipping_cost - buy);
  el.textContent = profit >= 0 ? `利益 ¥${profit.toLocaleString()}` : `赤字 ¥${Math.abs(profit).toLocaleString()}`;
  el.className = 'calc-result' + (profit < 0 ? ' loss' : '');
}

function calcReverse() {
  const target = parseFloat(document.getElementById('calcTargetProfit').value) || 0;
  const sell   = parseFloat(document.getElementById('calcReverseSell').value) || 0;
  const el     = document.getElementById('calcReverseResult');
  if (!target || !sell) { el.textContent = '— 最大仕入れ価格 —'; el.className = 'calc-result'; return; }
  const maxBuy = Math.round(sell * (1 - SETTINGS.commission_rate) - SETTINGS.shipping_cost - target);
  el.textContent = maxBuy > 0 ? `最大仕入れ ¥${maxBuy.toLocaleString()}` : '利益目標が高すぎます';
  el.className = 'calc-result' + (maxBuy <= 0 ? ' loss' : '');
}

// ── 初期化 ───────────────────────────────────────────────
buildCategoryTabs();
buildBrandChips();
buildMarkets();
render();
