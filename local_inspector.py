"""
写真の傷・汚れを無料で判定するモジュール（Mac 内のローカル AI = Ollama の画像対応モデル）

利益が出る案件の写真を Mac の中の AI（Ollama。http://127.0.0.1:11434）に見せ、傷・汚れを
有料の写真AI確認（photo_reader.inspect_deals、Claude API）と同じ5段階
（新品同様 / 目立った傷なし / やや傷あり / 傷あり / 状態悪い）で判定する（inspect_deals）。
結果の形も同じ {"rank", "issues": [{location, issue, severity, photo}], "confidence", "unchecked"}
（＋ "source": "local", "model"）で、タイトルの状態と悪いほうで利益を計算し直す処理
（condition_checker.combine / analyzer.apply_photo_condition / main._check_photo_conditions）もそのまま使う。

・外部の API は使わないので料金はかからない。ログインなし（写真のダウンロードは photo_reader.download_bytes）。
・使えるのは Ollama が入っていて、判定に使うモデル（settings.photo_local_model）が取り込み済みのときだけ。
  サーバーが止まっていれば `ollama serve` を起動してみる（インストールはしない。知り合いの Mac で
  Ollama が無ければ、画面のチェックごと出さない）。
・小さいモデルは写真をまとめて見せると見落とし・取り違えが増えるため、写真を1枚ずつ見せて
  いちばん目立つ傷・汚れと状態の点数（0〜10）を答えさせ、いちばん悪い写真の点数でランクを決める（_aggregate）。
  小さな傷・汚れはほぼ見えず、破れ・穴・目立つシミなどの大きな傷みだけを拾う（誤検知は少ない）。
・ローカル画面の「写真で傷・汚れ確認（無料・Mac内AI）」のチェック（settings.photo_local_check_enabled）で
  1回ごとにオン/オフ。既定はオフ（精度を確かめてから使うかどうかを決めるため）。
・結果は photo_local_inspect_cache に「出品URL＋写真URLの一覧＋モデル名＋判定の版（_LOCAL_VERSION）」の
  キーで保存する（写真の差し替え・モデルやプロンプトを変えたら判定し直す）。有料版のキャッシュとは別。
・おまけ: 型番照合で OCR（ocr_reader）でも型番が読めなかった出品のタグの文字を、同じモデルに書き写させる
  （read_listings）。読んだ文字は OCR と同じ ocr_reader.extract_model（正規の型番形式・ブランド名つきの
  モデル名だけ）で照合する。既定はオフ（settings.photo_local_read_enabled）。
"""

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import time

import database

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OLLAMA_URL = "http://127.0.0.1:11434"
LOCAL_CACHE_HOURS = 24 * 90
# プロンプト・ランクの決め方を変えたら上げる（キャッシュのキーに含める）
_LOCAL_VERSION = "2"
_DOWNLOAD_INTERVAL = 0.3

DEFAULTS = {
    "photo_local_check_enabled": False,     # このスキャンで写真の傷・汚れを Mac 内 AI で判定するか（画面のチェック。既定オフ）
    "photo_local_read_enabled": False,      # OCR で型番が読めなかった出品のタグを Mac 内 AI で読むか（既定オフ）
    "photo_local_model": "qwen3-vl:8b-instruct",    # Ollama のモデル名
    "photo_local_max_images": 6,            # 1出品あたりに見る写真の上限
    "photo_local_max_edge": 640,            # 送る前に縮小する長辺（px）。大きいほど細かい傷が見えるが遅い
}

PHOTO_RANKS = ["新品同様", "目立った傷なし", "やや傷あり", "傷あり", "状態悪い"]

stats = {
    "inspect_targets": 0,  # 傷・汚れを確認する対象になった案件
    "inspected": 0,        # 判定できた案件（キャッシュ利用を含む）
    "inspect_cached": 0,   # キャッシュの判定を使った件数
    "downgraded": 0,       # 写真の判定で状態を下げた案件
    "dropped": 0,          # 写真の判定で利益が条件を下回り外した案件
    "read_targets": 0,     # タグの文字を読む対象になった出品
    "read_adopted": 0,     # 型番・モデル名を採用した件数
    "photos": 0,           # AI に見せた写真の枚数
    "errors": 0,
    "seconds": 0.0,
}
_session = {"settings": dict(DEFAULTS)}
_server = {"checked_at": 0.0, "ok": False, "reason": "", "models": []}


# ──────────────────────────────────────────────────────────────────────────────
# Ollama の状態
# ──────────────────────────────────────────────────────────────────────────────

def _ollama_bin() -> str | None:
    for p in (shutil.which("ollama"), "/opt/homebrew/bin/ollama", "/usr/local/bin/ollama",
              "/Applications/Ollama.app/Contents/Resources/ollama"):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _get(path: str, timeout: float = 2.0):
    import requests
    resp = requests.get(OLLAMA_URL + path, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _start_server() -> bool:
    """Ollama が入っていてサーバーが止まっていれば `ollama serve` を起動する（入っていなければ何もしない）"""
    exe = _ollama_bin()
    if not exe:
        return False
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
    log = open(os.path.join(BASE_DIR, "logs", "ollama_serve.log"), "ab")
    env = dict(os.environ, OLLAMA_FLASH_ATTENTION="1", OLLAMA_KV_CACHE_TYPE="q8_0")
    try:
        subprocess.Popen([exe, "serve"], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         start_new_session=True, env=env)
    except OSError:
        return False
    for _ in range(30):
        time.sleep(0.5)
        try:
            _get("/api/version", timeout=1.0)
            return True
        except Exception:
            continue
    return False


def check_server(start: bool = True, max_age: float = 30.0) -> tuple[bool, str]:
    """(使えるか, 使えないときの理由)。サーバーが止まっていれば start=True で起動を試みる。結果は max_age 秒覚える"""
    if time.time() - _server["checked_at"] < max_age and (_server["ok"] or not start):
        return _server["ok"], _server["reason"]
    ok, reason, models = False, "", []
    try:
        models = [m.get("name", "") for m in _get("/api/tags").get("models", [])]
        ok = True
    except Exception:
        if not _ollama_bin():
            reason = "Ollama が入っていません"
        elif start and _start_server():
            try:
                models = [m.get("name", "") for m in _get("/api/tags").get("models", [])]
                ok = True
            except Exception:
                reason = "Ollama を起動できませんでした"
        else:
            reason = "Ollama が起動していません（ターミナルで ollama serve を実行してください）"
    if ok:
        want = str(_settings("photo_local_model"))
        if not any(m == want or m == want + ":latest" for m in models):
            ok, reason = False, f"モデル {want} が入っていません（ollama pull {want}）"
    _server.update(checked_at=time.time(), ok=ok, reason=reason, models=models)
    return ok, reason


def installed() -> bool:
    """この Mac に Ollama が入っているか（入っていなければ画面にチェックを出さない）"""
    return _ollama_bin() is not None


def available() -> bool:
    return check_server()[0]


def enabled() -> bool:
    """このスキャンで写真の傷・汚れを Mac 内 AI で判定するか"""
    return bool(_settings("photo_local_check_enabled")) and available()


def read_enabled() -> bool:
    return bool(_settings("photo_local_read_enabled")) and available()


def reset_session(settings: dict) -> None:
    for k in stats:
        stats[k] = 0
    stats["seconds"] = 0.0
    _session["settings"] = {k: settings.get(k, v) for k, v in DEFAULTS.items()}
    _server["checked_at"] = 0.0


def status_info() -> dict:
    ok, reason = check_server(start=False) if installed() else (False, "Ollama が入っていません")
    label = "写真で傷・汚れ確認（無料・Mac内AI）: " + ("使えます" if ok else f"無効（{reason}）")
    return {"installed": installed(), "available": ok, "enabled": enabled(), "reason": reason,
            "model": str(_settings("photo_local_model")), "label": label, "stats": dict(stats)}


def summary() -> str:
    s = stats
    err = f" / エラー {s['errors']}件" if s["errors"] else ""
    return (f"写真で傷・汚れ確認（無料・Mac内AI {_settings('photo_local_model')}）: 対象 {s['inspect_targets']}件・"
            f"判定 {s['inspected']}件（キャッシュ {s['inspect_cached']}件）・状態を下げて残した {s['downgraded']}件・"
            f"利益不足で除外 {s['dropped']}件 / 写真 {s['photos']}枚 / {s['seconds'] / 60:.1f}分{err}")


def _settings(key: str):
    return _session["settings"].get(key, DEFAULTS[key])


# ──────────────────────────────────────────────────────────────────────────────
# Ollama の呼び出し
# ──────────────────────────────────────────────────────────────────────────────

def prepare_image(data: bytes, max_edge: int) -> str | None:
    """写真のバイト列を長辺 max_edge 以下の JPEG に縮小し base64 で返す（読めなければ None）"""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return base64.standard_b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def chat(prompt: str, images_b64: list[str], schema: dict, model: str | None = None,
         timeout: float = 180.0, system: str = "") -> dict | None:
    """Ollama の /api/chat を1回呼び、構造化出力（JSON）を dict で返す。エラーなら None。
    指示は system に入れると写真が変わっても使い回され（前置きのキャッシュ）、1枚あたり1秒ほど速い"""
    import requests
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt, "images": images_b64})
    body = {
        "model": model or str(_settings("photo_local_model")),
        "messages": messages,
        "format": schema,
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 120},
    }
    try:
        resp = requests.post(OLLAMA_URL + "/api/chat", json=body, timeout=timeout)
        resp.raise_for_status()
        text = (resp.json().get("message") or {}).get("content") or ""
    except Exception as e:
        print(f"      [Mac内AI] 呼び出しエラー: {type(e).__name__}")
        return None
    try:
        data = json.loads(text)
    except ValueError:
        s, e = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[s:e + 1]) if s >= 0 else None
        except ValueError:
            data = None
    return data if isinstance(data, dict) else None


# ──────────────────────────────────────────────────────────────────────────────
# 傷・汚れの判定（写真1枚ずつ → まとめる）
# ──────────────────────────────────────────────────────────────────────────────

# 写真1枚ごとに、いちばん目立つ傷・汚れ（problem・where）と、状態の点数 wear（0〜10）を答えさせる。
# 2026-11-06 の試し（Claude の判定 61件との答え合わせ）:
#   ・「傷を書き出して」→ 無傷の写真にも傷を書く（新品同様のディナンドで7件）
#   ・「状態を5段階で」「種類ごとに yes/no」→ ほぼ全部「傷なし」、タグを見ると「新品タグ付き」と誤答
#   ・点数（0〜10）→ 無傷の写真はそろって 2、破れ・穴・全体のシミの写真だけ 6〜8 になり、誤検知が少なかった
# 日本語の指示だと確認箇所の例をそのまま傷として書き写したため、指示は英語にしている。
_PHOTO_SCHEMA = {
    "type": "object",
    "properties": {
        "problem": {"type": "string"},
        "where": {"type": "string"},
        "wear": {"type": "integer", "minimum": 0, "maximum": 10},
    },
    "required": ["problem", "where", "wear"],
}
_PHOTO_PROMPT = """You grade the condition of a second-hand {item} from photos for a resale shop. Be a strict, picky inspector.
For the photo you get:
problem: the most noticeable flaw on the item (stain, grime, discoloration, fading, scuff, pilling, fraying, tear, hole, missing fur or part), max 8 words; "none" if the item looks flawless in this photo.
where: the part of the item with that flaw (max 4 words; empty if none).
wear: 0-10 condition score for what is visible in this photo.
0 = brand new with tags, 2 = like new, 4 = light use (tiny marks), 6 = visible stains/grime/scuffs, 8 = many stains or clear damage, 10 = torn, holes, or very dirty.
Wrinkles, folds, shadows and reflections are not flaws."""

# ジャンル → プロンプトの品物の英語名
_ITEM_EN = {"ダウン": "down jacket", "バッグ": "bag", "時計": "watch", "サングラス": "pair of sunglasses",
            "アクセサリー": "piece of jewelry", "スカーフ": "scarf"}
# 箇所の日本語（モデルの英語の答えを語の一部で置き換える。当てはまらなければ英語のまま）
_PART_JA = [("collar", "襟"), ("neck", "襟"), ("hood", "フード"), ("fur", "ファー"), ("cuff", "袖口"),
            ("sleeve", "袖"), ("shoulder", "肩"), ("zip", "ファスナー"), ("pocket", "ポケット"),
            ("hem", "裾"), ("bottom", "裾"), ("back", "背面"), ("lining", "裏地"), ("inner", "内側"),
            ("inside", "内側"), ("logo", "ロゴワッペン"), ("patch", "ワッペン"), ("badge", "ワッペン"),
            ("button", "ボタン"), ("snap", "スナップ"), ("tag", "タグ"), ("label", "タグ"),
            ("seam", "縫い目"), ("front", "前身頃"), ("chest", "胸"), ("strap", "ストラップ"),
            ("handle", "持ち手"), ("corner", "角"), ("lens", "レンズ"), ("frame", "フレーム"),
            ("bezel", "ベゼル"), ("crystal", "風防"), ("band", "ベルト"), ("bracelet", "ブレスレット"),
            ("shell", "表地"), ("outer", "表地"), ("fabric", "生地"), ("body", "身頃"), ("surface", "表面")]


def _ja(text: str, table: list[tuple[str, str]]) -> str:
    t = (text or "").lower()
    for en, ja in table:
        if en in t:
            return ja
    return (text or "").strip()


def _pick(urls: list[str], limit: int) -> list[str]:
    """見る写真を選ぶ（上限を超えるときは前から。全体・寄りの写真は前の方、タグは後ろの方に多い）"""
    return urls[:limit]


_DEFECT_JA = [("tear", "破れ"), ("torn", "破れ"), ("rip", "破れ"), ("hole", "穴"), ("stain", "シミ"),
              ("spot", "シミ"), ("mottl", "シミ・色ムラ"), ("blotch", "シミ・色ムラ"), ("grime", "黒ずみ"),
              ("dirt", "汚れ"), ("soil", "汚れ"), ("discolor", "変色"), ("fad", "色あせ"), ("yellow", "黄ばみ"),
              ("abrasion", "擦れ"), ("scuff", "擦れ"), ("worn", "擦れ"), ("wear", "擦れ"), ("fray", "ほつれ"),
              ("loose thread", "ほつれ"), ("pill", "毛玉"), ("fuzz", "毛羽立ち"), ("missing", "欠損"),
              ("peel", "剥がれ"), ("crack", "ひび割れ"), ("scratch", "傷"), ("down leak", "羽毛抜け"),
              ("feather", "羽毛抜け"), ("rust", "サビ"), ("dent", "へこみ"), ("lint", "毛羽・ホコリ"),
              ("dust", "ホコリ"), ("shine", "テカり"), ("shiny", "テカり")]

# ランクの決め方（写真ごとの wear の最大・2番目から）。変えたら _LOCAL_VERSION も上げる
_RULES = {"bad": 9, "bad_pair": (8, 6), "damaged": 6, "slight": 4}
_HARD_WORDS = ("tear", "torn", "rip", "hole", "missing")


def _aggregate(per_photo: list[dict | None]) -> dict:
    """写真ごとの点数をまとめて5段階のランクにする（いちばん悪い写真で決める）"""
    rows = []
    for i, r in enumerate(per_photo, 1):
        if not r:
            continue
        try:
            w = max(0, min(10, int(r.get("wear"))))
        except (TypeError, ValueError):
            continue
        rows.append((w, i, str(r.get("problem") or "").strip(), str(r.get("where") or "").strip()))
    ws = sorted((w for w, *_ in rows), reverse=True)
    top = ws[0] if ws else 0
    # 破れ・穴・欠損の写真（8点以上）があれば「状態悪い」。シミだけの8点はスナップボタン等の見間違いがあったため「傷あり」まで
    hard = any(w >= _RULES["bad_pair"][0] and any(k in p.lower() for k in _HARD_WORDS) for w, _, p, _ in rows)
    if top >= _RULES["bad"] or hard:
        rank = "状態悪い"
    elif top >= _RULES["damaged"]:
        rank = "傷あり"
    elif top >= _RULES["slight"]:
        rank = "やや傷あり"
    elif len(ws) >= 2 and sum(w == 0 for w in ws) >= 2 and top <= 2:
        rank = "新品同様"
    else:
        rank = "目立った傷なし"
    issues = []
    for w, i, problem, where in sorted(rows, reverse=True):
        if w < _RULES["slight"] or problem.lower() in ("", "none"):
            continue
        sev = "大" if w >= 8 else ("中" if w >= 6 else "小")
        issues.append({"location": (_ja(where, _PART_JA) or "（箇所不明）")[:30], "issue": _ja(problem, _DEFECT_JA)[:40],
                       "severity": sev, "photo": i, "en": problem[:80]})
    n = len(rows)
    conf = "高" if n >= 6 else ("低" if n <= 3 else "中")
    unchecked = "" if n >= 6 else f"写真が{n}枚だけのため、写っていない箇所の傷・汚れは確認できていません"
    if not unchecked:
        unchecked = "無料AIは小さな傷・汚れを見落としやすく、破れ・穴・目立つシミなどの大きな傷みだけを見ています"
    return {"rank": rank, "issues": issues[:12], "confidence": conf, "unchecked": unchecked, "wear": [w for w, *_ in rows]}


def inspect_images(images: list[bytes], brand: str, category: str, title: str,
                   model: str | None = None) -> dict | None:
    """写真（バイト列の一覧）を1枚ずつ AI に見せて状態を判定する（写真が1枚も読めなければ None）"""
    max_edge = int(_settings("photo_local_max_edge"))
    b64 = [prepare_image(b, max_edge) for b in images if b]
    b64 = [b for b in b64 if b]
    if not b64:
        return None
    per_photo = []
    for i, img in enumerate(b64, 1):
        system = _PHOTO_PROMPT.format(item=_ITEM_EN.get(category, "item"))
        per_photo.append(chat(f"Photo {i}.", [img], _PHOTO_SCHEMA, model=model, system=system))
        stats["photos"] += 1
    if not any(per_photo):
        return None
    out = _aggregate(per_photo)
    out.update(source="local", model=model or str(_settings("photo_local_model")))
    return out


def photos_key(urls: list[str], model: str | None = None) -> str:
    m = model or str(_settings("photo_local_model"))
    return hashlib.sha1((f"{_LOCAL_VERSION}\n{m}\n" + "\n".join(urls)).encode("utf-8")).hexdigest()


def inspect_deals(deals: list, get_images, should_stop=None) -> dict[str, dict]:
    """案件（Deal）の写真で傷・汚れを判定し、{item.url: 判定} を返す（photo_reader.inspect_deals と同じ形）。
    見込み利益の大きい案件から順に確認する"""
    if not deals or not enabled():
        return {}
    import photo_reader
    by_url: dict[str, object] = {}
    for d in sorted(deals, key=lambda d: d.estimated_profit, reverse=True):
        if d.item.url and d.item.url not in by_url:
            by_url[d.item.url] = d
    items = [d.item for d in by_url.values()]
    stats["inspect_targets"] += len(items)
    print(f"\n[写真で状態確認・無料] 案件 {len(items)}件の写真で傷・汚れを確認します（Mac内AI {_settings('photo_local_model')}）")
    started = time.time()
    results: dict[str, dict] = {}
    try:
        image_urls = get_images(items)
        limit = int(_settings("photo_local_max_images"))
        keys = {it.url: photos_key(_pick(image_urls.get(it.url) or [], limit)) for it in items}
        cached = database.get_cached_photo_local_inspects(
            [(it.url, keys[it.url]) for it in items if image_urls.get(it.url)], LOCAL_CACHE_HOURS)
        stats["inspect_cached"] += len(cached)
        results.update(cached)
        if cached:
            print(f"    キャッシュの判定 {len(cached)}件を使います")
        for i, it in enumerate(items, 1):
            if it.url in results:
                continue
            if should_stop and should_stop():
                print("    [中断] 写真での状態確認を打ち切りました")
                break
            urls = _pick(image_urls.get(it.url) or [], limit)
            if not urls:
                stats["errors"] += 1
                continue
            t0 = time.time()
            blobs = []
            for u in urls:
                b = photo_reader.download_bytes(u)
                time.sleep(_DOWNLOAD_INTERVAL)
                if b:
                    blobs.append(b)
            d = by_url[it.url]
            got = inspect_images(blobs, d.brand, d.category, it.title)
            if got is None:
                stats["errors"] += 1
                continue
            database.cache_photo_local_inspect(it.url, keys[it.url], got, LOCAL_CACHE_HOURS)
            results[it.url] = got
            issues = "、".join(f"{x['location']}の{x['issue']}" for x in got["issues"][:4]) or "傷・汚れなし"
            print(f"      ({i}/{len(items)}) {got['rank']}（確信度 {got['confidence']}・{time.time() - t0:.0f}秒）"
                  f"{issues[:60]}: {it.title[:30]}")
    finally:
        stats["seconds"] += time.time() - started
    stats["inspected"] += len(results)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# タグの文字の読み取り（OCR で型番が読めなかった出品の予備）
# ──────────────────────────────────────────────────────────────────────────────

_READ_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"],
}
_READ_PROMPT = """この写真に写っているタグ・ラベル・刻印に印字された文字を、1行ずつそのまま書き写してください。
英数字の品番（例: LTA20A4568、MTK18A453、3426MA など）は特に一字一句正確に。
読めない文字は推測せず、その行は書かない。文字が写っていなければ lines は空の配列。"""


def read_texts(images: list[bytes], model: str | None = None) -> str:
    """写真のタグの文字を AI に書き写させ、全文（写真ごとに空行で区切る）を返す"""
    max_edge = max(int(_settings("photo_local_max_edge")), 1280)
    out = []
    for b in images:
        img = prepare_image(b, max_edge)
        if not img:
            continue
        r = chat(_READ_PROMPT, [img], _READ_SCHEMA, model=model) or {}
        stats["photos"] += 1
        lines = [str(x).strip() for x in (r.get("lines") or []) if str(x).strip()]
        out.append("\n".join(lines[:40]))
    return "\n\n".join(out)


def read_listings(items: list, brand: str, category: str, get_images, should_stop=None) -> dict[str, str]:
    """OCR でも型番が取れなかった出品のタグの文字を AI に書き写させ、{item.url: 型番 or モデル名} を返す。
    照合は OCR と同じ（ocr_reader.extract_model）。対象の写真は OCR で文字が見つかった写真（タグ）だけ"""
    if not items or not read_enabled():
        return {}
    import ocr_reader
    import photo_reader
    unique = list({it.url: it for it in items if it.url}.values())
    stats["read_targets"] += len(unique)
    started = time.time()
    results: dict[str, str] = {}
    try:
        image_urls = get_images(unique)
        ocr_keys = [(it.url, ocr_reader.photos_key(image_urls[it.url])) for it in unique if image_urls.get(it.url)]
        ocr_text = database.get_cached_photo_ocr(ocr_keys, ocr_reader.OCR_CACHE_HOURS)
        for it in unique:
            if should_stop and should_stop():
                break
            urls = image_urls.get(it.url) or []
            tag_urls = tag_photos(urls, ocr_text.get(it.url))
            if not tag_urls:
                continue
            blobs = [b for b in (photo_reader.download_bytes(u) for u in tag_urls) if b]
            text = read_texts(blobs)
            model = ocr_reader.extract_model(text, brand, category)
            if model:
                results[it.url] = model
                stats["read_adopted"] += 1
                print(f"      Mac内AIがタグから {model} を読み採用: {it.title[:40]}")
    finally:
        stats["seconds"] += time.time() - started
    return results


def tag_photos(urls: list[str], ocr_text: str | None, limit: int = 6) -> list[str]:
    """タグが写っていそうな写真（OCR で英数字が読めた写真）。OCR の結果が無ければ後ろの方の写真"""
    if ocr_text is None:
        return urls[-limit:]
    parts = ocr_text.split("\n\n")
    picked = [u for u, t in zip(urls, parts) if sum(c.isalnum() for c in t) >= 6]
    return picked[-limit:]
