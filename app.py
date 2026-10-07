"""
ローカル リサーチ画面（Mac 上で起動してブラウザで操作する）

起動:
  ./venv/bin/python app.py        （または start.command をダブルクリック）
  → ブラウザで http://127.0.0.1:8765 が開く

・トップページでジャンル/ブランド/モデル名/型番/利益条件/オークション残り時間/
  取得件数/仕入れ先をチェックして「リサーチ開始」。
・スキャン本体は main.scan() をそのまま流用する（config.json は書き換えず、
  画面で選んだ条件を実行時に渡すだけ）。
・ローカル実行では DB の scan_deals 保存・docs/（GitHub Pages 用）生成・git push は
  一切行わない（main.scan() はそれらを行わない関数。さらに環境変数でも二重に防止）。
  メルカリ相場キャッシュ（price_cache）だけは従来どおり arbitrage.db に保存される。
・待ち受けは 127.0.0.1 のみ（同じ Mac からしか開けない）。
"""

import copy
import json
import os
import re
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)                      # config.json / arbitrage.db を相対パスで読むため
sys.path.insert(0, BASE_DIR)
os.environ["RIEKI_LOCAL_APP"] = "1"     # main.run() 側の push 防止ガード

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

import database          # noqa: E402
import main as scan_main  # noqa: E402
import model_extractor   # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("RIEKI_PORT", "8765"))
UI_DIR = os.path.join(BASE_DIR, "local_ui")
ASSETS_DIR = os.path.join(BASE_DIR, "dashboard_assets")
RESULT_DIR = os.path.join(BASE_DIR, "local_results")
LAST_RESULT_PATH = os.path.join(RESULT_DIR, "last_result.json")

# 「オメガ シーマスター」の2語目がモデル名かどうか判定するときに除外する一般名詞
_GENERIC_WORDS = {"時計", "腕時計", "ダウン", "サングラス", "ジュエリー", "アクセサリー", "スカーフ", "バッグ", "財布"}

# 旧ジャンル名 → 現在のジャンル名（保存済みの選択・過去の結果に旧名が残っていても動くように）
_CATEGORY_RENAMES = {"ジュエリー": "アクセサリー"}


def _cat(name: str) -> str:
    return _CATEGORY_RENAMES.get(name, name)

app = Flask(__name__, static_folder=None)


# ──────────────────────────────────────────────────────────────────────────────
# 設定・選択肢
# ──────────────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    with open(scan_main.CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _brand_of(kw: dict) -> str:
    return kw.get("brand_name") or kw["name"].split()[0]


def _has_digit(s: str) -> bool:
    return bool(re.search(r"\d", s))


def _norm(s: str) -> str:
    """型番・モデル名の比較用（空白・ハイフン・ドット・中黒を除去して大文字化）"""
    return re.sub(r"[\s\-_.・]", "", s or "").upper()


def _model_aliases() -> dict[str, list[str]]:
    """モデル名 → 照合用の全表記（model_extractor.model_aliases）の表。
    キーはカタカナ名・英字名・表記ゆれのどれでも引けるよう大文字で登録する。
    例: トリニティ / TRINITY → [トリニティ, TRINITY]
    """
    aliases: dict[str, list[str]] = {}
    for brand, names in model_extractor._MODEL_NAMES.items():
        for name in names:
            forms = model_extractor.model_aliases(name, brand)
            for f in [name] + forms:
                aliases.setdefault(f.upper(), forms)
    return aliases


def _number_categories(numbers: dict[str, list[str]]) -> dict[str, dict[str, str]]:
    """ジャンルで型番の照合が変わるブランド（グッチ: 時計/サングラス）について、
    型番の候補ごとにどのジャンルの型番かを返す（ブランド → {型番: ジャンル}）"""
    known = getattr(model_extractor, "MODEL_NUMBER_CATEGORIES", {})
    cfg_cats: dict[str, list[str]] = {}
    for kw in _load_config()["keywords"]:
        cfg_cats.setdefault(_brand_of(kw), [])
        c = _cat(kw.get("category", "") or "その他")
        if c not in cfg_cats[_brand_of(kw)]:
            cfg_cats[_brand_of(kw)].append(c)
    out: dict[str, dict[str, str]] = {}
    for b, nums in numbers.items():
        if not model_extractor.has_category_extractor(b):
            continue
        m: dict[str, str] = {}
        for n in nums:
            c = known.get(b, {}).get(n)
            if c is None:
                c = next((c for c in cfg_cats.get(b, []) if model_extractor.extract(n, b, c)), None)
            if c:
                m[n] = c
        out[b] = m
    return out


def _number_fits(num: str, brand: str, cat: str) -> bool:
    """型番がそのジャンルのキーワードで使えるか。ジャンル別の照合があるブランド（グッチ）だけ判定し、
    それ以外のブランドは従来どおり常に True"""
    if not model_extractor.has_category_extractor(brand):
        return True
    if model_extractor.extract(num, brand, cat) is not None:
        return True
    # どのジャンルの形式にも当てはまらない型番（自由入力の未登録型番など）は従来どおり全ジャンルで使う
    others = {_cat(kw.get("category", "") or "その他") for kw in _load_config()["keywords"]
              if _brand_of(kw) == brand} - {cat}
    return not any(model_extractor.extract(num, brand, c) for c in others)


def build_options() -> dict:
    cfg = _load_config()
    settings = cfg["settings"]
    keywords = cfg["keywords"]

    categories: list[str] = []
    brands: dict[str, dict] = {}
    for kw in keywords:
        cat = _cat(kw.get("category", "") or "その他")
        if cat not in categories:
            categories.append(cat)
        b = _brand_of(kw)
        entry = brands.setdefault(b, {"name": b, "categories": [], "keywords": []})
        if cat not in entry["categories"]:
            entry["categories"].append(cat)
        entry["keywords"].append(kw["name"])

    # モデル名（ブランドごと）: キーワードの2語目 + model_extractor の名称リスト + 過去の検出実績
    models: dict[str, list[str]] = {b: [] for b in brands}
    numbers: dict[str, list[str]] = {b: [] for b in brands}

    def add(target: dict, brand: str, name: str):
        if brand in target and name and name not in target[brand]:
            target[brand].append(name)

    for kw in keywords:
        toks = kw["name"].split()
        for t in toks[1:]:
            if t not in _GENERIC_WORDS and t != kw.get("category"):
                add(models, _brand_of(kw), t)
    for brand, names in model_extractor._MODEL_NAMES.items():
        for n in names:
            if not n.isascii():          # 英字の別名は日本語名とまとめて扱う
                add(models, brand, n)
    # 代表的な型番（ノースフェイスの ND91950 等）は過去の検出実績が無くても候補に出す
    for brand, nums in getattr(model_extractor, "KNOWN_MODEL_NUMBERS", {}).items():
        for n in nums:
            add(numbers, brand, n)
    try:
        database.init_db()
        with database._conn() as conn:
            rows = conn.execute(
                "SELECT brand, model, COUNT(*) AS c FROM scan_deals "
                "WHERE model != '' GROUP BY brand, model ORDER BY brand, c DESC"
            ).fetchall()
        aliases = _model_aliases()
        known_upper = {b: {m.upper() for g in (aliases.get(m.upper(), [m]) for m in ms) for m in g}
                       for b, ms in models.items()}
        for r in rows:
            b, m = r["brand"], r["model"]
            if _has_digit(m):
                add(numbers, b, m)
            elif m.upper() not in known_upper.get(b, set()):
                add(models, b, m)
    except Exception as e:  # DB が無くても画面は出す
        print(f"[app] 過去の型番読み込みエラー: {e}")

    sources = [
        {"key": k, "label": v, "auction": k == "yahoo_auctions"}
        for k, v in scan_main.SOURCE_LABELS.items()
    ]
    return {
        "categories": categories,
        "brands": list(brands.values()),
        "models": models,
        "model_numbers": numbers,
        # 型番の候補に添える愛称（ブランド → {型番: 愛称}）。例: RB2140 → ウェイファーラー
        "model_number_labels": getattr(model_extractor, "MODEL_NUMBER_LABELS", {}),
        # ジャンル専用の型番（ブランド → {型番: ジャンル}）。例: グッチ YA126402 → 時計。
        # 画面では選択中のジャンルの型番だけを出す（グッチのサングラスだけ選んだときに時計の型番を出さない）
        "model_number_categories": _number_categories(numbers),
        # 英字名でも検索するモデル（ブランド → {カタカナ名: 英字名}）。画面の所要時間の目安に使う
        "model_en": {
            b: {m: n[1] for m in ms if not _has_digit(m)
                for n in [model_extractor.search_names(m, b)] if len(n) > 1}
            for b, ms in models.items()
        },
        "sources": sources,
        "keywords": [
            {"name": kw["name"], "brand": _brand_of(kw), "category": _cat(kw.get("category", "") or "その他")}
            for kw in keywords
        ],
        "defaults": {
            "min_profit": settings.get("min_profit_yen", 3000),
            "min_roi": 0,
            "per_source": 20,
            "config_per_source": settings.get("search_items_per_source", 80),
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# 条件 → スキャン計画
# ──────────────────────────────────────────────────────────────────────────────

def _to_int(v, default=None):
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def build_plan(req: dict, cfg: dict) -> list[tuple[dict, str | None]]:
    """画面の条件から (キーワード設定, 絞り込みモデル or None) のリストを作る。
    モデル名・型番が選ばれたブランドは「ブランド名 + 型番」等で個別に検索し、
    結果もそのモデルを含むものに絞る。キーワード自体にモデル名が入っている場合
    （例: オメガ シーマスター）はそのまま検索する。
    """
    cats = {_cat(c) for c in (req.get("categories") or [])}
    brands = set(req.get("brands") or [])
    # "ブランド|モデル" 形式（チェックボックス）
    picked: dict[str, list[str]] = {}
    for v in (req.get("models") or []) + (req.get("model_numbers") or []):
        if "|" in v:
            b, m = v.split("|", 1)
            picked.setdefault(b, []).append(m.strip())
    free_numbers = [t.strip() for t in re.split(r"[,、，\s]+", req.get("free_numbers") or "") if t.strip()]
    # 自由入力の型番は、型番の形式から判別できるブランド（例: 2531.80→オメガ）にだけ使う。
    # どのブランドの形式にも当てはまらなければ、選択中の全ブランドで検索する。
    # ジャンルで型番の形式が変わるブランド（グッチ: 時計 YA126402 / サングラス GG0061S）は、
    # 選択中のジャンルで判別する。
    sel_pairs = {(_brand_of(kw), _cat(kw.get("category", "") or "その他")) for kw in cfg["keywords"]}
    sel_pairs = {(b, c) for b, c in sel_pairs if b in brands and c in cats}
    free_for: dict[str, list[str]] = {}
    for num in free_numbers:
        owners = {b for b, c in sel_pairs if model_extractor.extract(num, b, c)}
        for b in owners or brands:
            free_for.setdefault(b, []).append(num)

    plan: list[tuple[dict, str | None]] = []
    seen: set[str] = set()
    for kw in cfg["keywords"]:
        cat = _cat(kw.get("category", "") or "その他")
        brand = _brand_of(kw)
        if cat not in cats or brand not in brands:
            continue
        targets = picked.get(brand, []) + free_for.get(brand, [])
        # グッチ等: 時計の型番はサングラスのキーワードでは使わない（逆も同じ）
        targets = [m for m in targets if not _has_digit(m) or _number_fits(m, brand, cat)]
        if not targets:
            if kw["name"] not in seen:
                seen.add(kw["name"])
                plan.append((kw, None))
            continue
        for m in targets:
            if _norm(m) in _norm(kw["name"]):
                if kw["name"] not in seen:
                    seen.add(kw["name"])
                    plan.append((kw, None))
                continue
            c = copy.deepcopy(kw)
            # 型番はブランド名と組み合わせて1本で検索。モデル名は
            #   カタカナ名: 元のキーワード + カタカナ名（例: デュベティカ ダウン ディオニシオ）
            #   英字名:     ブランド名 + 英字名      （例: デュベティカ DIONISIO）
            # の2本で検索する（英字だけのタイトルの出品も拾うため）。ラクマ・ヤフオクは
            # デュベティカ/DUVETICA 等のブランド名を同一視して検索するので、英字側の
            # ブランド名はカタカナのままでよく、ジャンル語（ダウン等）を付けない方が多く拾える。
            if _has_digit(m):
                c["name"] = f"{brand} {m}"
            else:
                names = model_extractor.search_names(m, brand)
                c["name"] = f"{kw['name']} {names[0]}"
                if len(names) > 1:
                    c["search_queries"] = [c["name"]] + [f"{brand} {n}" for n in names[1:]]
            c["brand_name"] = brand
            if c["name"] not in seen:
                seen.add(c["name"])
                plan.append((c, m))
    return plan


def _deal_matches_model(deal, model: str, aliases: dict[str, list[str]]) -> bool:
    # 厳密照合ブランド（デュベティカ・ノースフェイス）は抽出済みのモデル名/型番で判定する
    # （「ヌプシジャケット」を選んだときに「ショートヌプシ」を含めない）
    if deal.model and model_extractor.is_strict_brand(deal.brand):
        want = model_extractor.extract(model, deal.brand) or model
        return _norm(deal.model) == _norm(want)
    if deal.model and (model_extractor.model_names(deal.model, deal.brand)[0]
                       == model_extractor.model_names(model, deal.brand)[0]):
        return True
    names = aliases.get(model.upper(), [model])
    hay_title = _norm(deal.item.title)
    hay_model = _norm(deal.model)
    for n in names:
        nn = _norm(n)
        if nn and (nn in hay_title or (hay_model and nn in hay_model)):
            return True
    return False


def _deal_to_dict(d) -> dict:
    return {
        "keyword": d.keyword,
        "brand": d.brand,
        "model": d.model,
        "category": _cat(d.category),
        "title": d.item.title,
        "url": d.item.url,
        "source": d.item.source,
        "image_url": d.item.image_url or "",
        "purchase_price": d.item.price,
        "reference_price": d.mercari_avg_price,
        "estimated_profit": d.estimated_profit,
        "roi_percent": d.roi_percent,
        "condition_label": d.condition_label,
        "end_time": d.item.end_time,
        "is_auction": d.item.is_auction,
    }


# ──────────────────────────────────────────────────────────────────────────────
# ジョブ（同時に1件だけ）
# ──────────────────────────────────────────────────────────────────────────────

class _Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.state = "idle"        # idle / running / done / stopped / error
        self.index = 0
        self.total = 0
        self.keyword = ""
        self.started_at = None
        self.finished_at = None
        self.logs: deque[str] = deque(maxlen=400)
        self.results: list[dict] = []
        self.conditions: dict = {}
        self.error = ""
        self.stop_requested = False
        self.found_count = 0       # 実行中に見つかった案件候補の数（ログの「--> N 件の案件」を合算）

    def snapshot(self, with_results: bool) -> dict:
        with self.lock:
            out = {
                "state": self.state,
                "index": self.index,
                "total": self.total,
                "keyword": self.keyword,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "logs": list(self.logs)[-60:],
                "result_count": len(self.results),
                "found_count": self.found_count,
                "conditions": self.conditions,
                "error": self.error,
            }
            if with_results:
                out["results"] = self.results
            return out


JOB = _Job()


class _ThreadLogTee:
    """sys.stdout の代わり。ジョブスレッドからの print をターミナルにも画面ログにも流す。"""

    def __init__(self, original):
        self.original = original
        self._buf = ""

    def write(self, s):
        self.original.write(s)
        if JOB.thread is not None and threading.current_thread() is JOB.thread:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    m = _FOUND_RE.search(line)
                    with JOB.lock:
                        JOB.logs.append(line)
                        if m:
                            JOB.found_count += int(m.group(1))
        return len(s)

    def flush(self):
        self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


sys.stdout = _ThreadLogTee(sys.stdout)

_FOUND_RE = re.compile(r"-->\s*(\d+)\s*件の案件")


def _run_job(req: dict):
    try:
        cfg = _load_config()
        settings = dict(cfg["settings"])
        per_source = _to_int(req.get("per_source"), 20)
        settings["search_items_per_source"] = max(1, min(per_source, 100))
        min_profit = _to_int(req.get("min_profit"), settings.get("min_profit_yen", 3000))
        settings["min_profit_yen"] = min_profit
        min_roi = float(req.get("min_roi") or 0)
        hours = req.get("auction_hours")
        hours = float(hours) if hours not in (None, "", "0", 0) else None
        auction_only = bool(req.get("auction_only"))
        sources = set(req.get("sources") or [])

        plan = build_plan(req, cfg)
        with JOB.lock:
            JOB.total = len(plan)
        if not plan:
            raise ValueError("条件に合うキーワードがありません（ジャンルとブランドを選んでください）")

        n_queries = sum(len(kw.get("search_queries") or [kw["name"]]) for kw, _ in plan)
        q_str = f"（検索語 {n_queries} 本）" if n_queries != len(plan) else ""
        print("=" * 50)
        print(f"ローカル リサーチ開始: {len(plan)} キーワード{q_str} / 仕入れ先 {','.join(sorted(sources))}")
        print("=" * 50)

        def item_filter(item) -> bool:
            if auction_only and not item.is_auction:
                return False
            if hours is None:
                return True
            if not item.is_auction or not item.end_time:
                return not auction_only   # 即決・フリマ形式は残り時間の対象外
            remain = item.end_time - time.time()
            return 0 < remain <= hours * 3600

        aliases = _model_aliases()
        model_of = {kw["name"]: m for kw, m in plan}

        def progress(i, total, keyword):
            with JOB.lock:
                JOB.index = i
                JOB.keyword = keyword

        database.init_db()
        deals = scan_main.scan(
            [kw for kw, _ in plan], settings,
            sources=sources, item_filter=item_filter,
            progress=progress, should_stop=lambda: JOB.stop_requested,
        )

        best: dict[str, object] = {}
        for d in deals:
            # search_keyword = その案件を見つけた検索キーワード（型番照合時も元のキーワード）
            m = model_of.get(d.search_keyword or d.keyword)
            if m and not _deal_matches_model(d, m, aliases):
                continue
            if d.roi_percent < min_roi:
                continue
            prev = best.get(d.item.url)
            if prev is None or d.estimated_profit > prev.estimated_profit:
                best[d.item.url] = d
        results = sorted((_deal_to_dict(d) for d in best.values()),
                         key=lambda r: r["estimated_profit"], reverse=True)

        with JOB.lock:
            JOB.results = results
            JOB.index = JOB.total if not JOB.stop_requested else JOB.index
            JOB.state = "stopped" if JOB.stop_requested else "done"
            JOB.finished_at = datetime.now().isoformat(timespec="seconds")
        print(f"ローカル リサーチ完了: {len(results)} 件")
        _save_last()
    except Exception as e:
        traceback.print_exc()
        with JOB.lock:
            JOB.state = "error"
            JOB.error = str(e)
            JOB.finished_at = datetime.now().isoformat(timespec="seconds")


def _save_last():
    try:
        os.makedirs(RESULT_DIR, exist_ok=True)
        snap = JOB.snapshot(with_results=True)
        with open(LAST_RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
    except Exception as e:
        print(f"[app] 結果保存エラー: {e}")


def _load_last():
    try:
        with open(LAST_RESULT_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        JOB.state = snap.get("state", "done")
        JOB.index = snap.get("index", 0)
        JOB.total = snap.get("total", 0)
        JOB.started_at = snap.get("started_at")
        JOB.finished_at = snap.get("finished_at")
        JOB.results = snap.get("results", [])
        for r in JOB.results:          # 旧ジャンル名（ジュエリー）で保存された結果
            if isinstance(r, dict) and r.get("category"):
                r["category"] = _cat(r["category"])
        JOB.conditions = snap.get("conditions", {})
        JOB.logs.extend(snap.get("logs", []))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[app] 前回結果の読み込みエラー: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# ルーティング
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return send_from_directory(UI_DIR, "index.html")


@app.get("/ui/<path:name>")
def ui_file(name):
    return send_from_directory(UI_DIR, name)


@app.get("/assets/<path:name>")
def asset_file(name):
    return send_from_directory(ASSETS_DIR, name)


@app.get("/api/options")
def api_options():
    return jsonify(build_options())


@app.post("/api/research")
def api_research():
    req = request.get_json(silent=True) or {}
    with JOB.lock:
        if JOB.state == "running":
            return jsonify({"ok": False, "error": "リサーチ実行中です。終わるまでお待ちください"}), 409
        if not req.get("sources"):
            return jsonify({"ok": False, "error": "仕入れ先を1つ以上選んでください"}), 400
        JOB.state = "running"
        JOB.index = 0
        JOB.total = 0
        JOB.keyword = ""
        JOB.logs.clear()
        JOB.results = []
        JOB.error = ""
        JOB.stop_requested = False
        JOB.found_count = 0
        JOB.conditions = req
        JOB.started_at = datetime.now().isoformat(timespec="seconds")
        JOB.finished_at = None
        JOB.thread = threading.Thread(target=_run_job, args=(req,), daemon=True)
        JOB.thread.start()
    return jsonify({"ok": True})


@app.get("/api/status")
def api_status():
    with_results = request.args.get("results") == "1"
    return jsonify(JOB.snapshot(with_results))


@app.post("/api/stop")
def api_stop():
    with JOB.lock:
        if JOB.state == "running":
            JOB.stop_requested = True
    return jsonify({"ok": True})


if __name__ == "__main__":
    _load_last()
    url = f"http://{HOST}:{PORT}"
    print(f"リサーチ画面: {url}  （終了するにはこのウィンドウで Ctrl+C）")
    if os.environ.get("RIEKI_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)
