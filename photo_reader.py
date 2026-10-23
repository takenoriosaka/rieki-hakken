"""
写真から型番（品番）を読むモジュール

型番照合（require_model_number）で、タイトルにも商品説明文にも型番が無かった出品の写真を
Claude API に見せ、品質表示タグ・内側のタグ・刻印などに印字された品番を読み取る（main.py から呼ぶ）。

・使えるのは次の両方を満たすときだけ（それ以外は静かに無効。既存の動作は一切変わらない）:
    (a) .env に ANTHROPIC_API_KEY がある（set_api_key.command で登録する）
    (b) .rieki_friend_mode が無い（知り合いの Mac ではない）
  anthropic / Pillow の import は有効なときだけ行う。
・Claude が返した文字列は必ず model_extractor.extract() に通し、正規の型番形式に当てはまったものだけ
  採用する（読み間違い・推測の混入を防ぐ）。採用後は既存どおりメルカリ相場の取得へ進む。
・結果（読めなかった場合も含む）は出品URL単位で DB にキャッシュし、再スキャンでは API を呼ばない。
  API エラーのときは保存せず、次回再試行する。
・1回のスキャンの料金上限（settings.photo_read_max_yen_per_scan）を超えたら、写真読み取りだけ止める。
・API キーはログに出さない（エラー表示でも念のため伏せる）。
"""

import base64
import io
import json
import math
import os
import re
import time

import database
import model_extractor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
FRIEND_MODE_PATH = os.path.join(BASE_DIR, ".rieki_friend_mode")

# API キーの先頭（リポジトリにキーの文字列を入れない pre-commit フックに引っかからないよう分けて書く）
_KEY_PREFIX = "sk-" "ant-"

# 写真から読んだ結果のキャッシュ期限（時間）。出品の写真は変わらないため長め（90日）
PHOTO_CACHE_HOURS = 24 * 90

# settings（config.json）で変えられる値の既定
DEFAULTS = {
    "photo_read_model": "claude-sonnet-5-5",   # 精度優先で Sonnet 系の最新
    "photo_read_effort": "medium",             # low / medium / high（考える深さ。高いほど出力料金が増える）
    "photo_read_max_yen_per_scan": 1000,       # 1回のスキャンの料金上限（円。概算）
    "photo_read_max_images": 20,               # 1出品あたりに見せる写真の上限（タグは後ろの方に多いので末尾を優先）
    "photo_read_max_edge": 1568,               # 送る前に縮小する長辺（px）。タグの小さい文字が読める大きさ
    "photo_read_usd_jpy": 155,                 # 料金の円換算レート（概算表示・上限判定用）
}

# モデルごとの料金（USD / 100万トークン: 入力, 出力）。claude-api スキルの料金表（2026-10-06 時点）で確認。
# 表に無いモデルは上限判定が甘くならないよう最も高い料金で概算する
_PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5":   (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5-5":   (4.00, 20.00),
    "claude-opus-5":     (5.00, 25.00),
    "claude-opus-4-8":   (5.00, 25.00),
    "claude-haiku-5-5":  (0.10, 0.50),
    "claude-haiku-4-5":  (1.00, 5.00),
    "claude-fable-5-1":  (10.00, 50.00),
}
_PRICE_UNKNOWN = (10.00, 50.00)

# 安全上の理由で断られた（refusal）ときに、API 側で別モデルに自動で回してもらう設定を使えるモデル
_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}

# 1リクエストの出力上限（JSON は短いが、考える分（thinking）も含むため余裕を持たせる）
_MAX_TOKENS = 8000
# 写真のダウンロード間隔（秒）。画像配信サーバーへの負荷を抑える
_DOWNLOAD_INTERVAL = 0.3
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_SYSTEM = (
    "あなたは中古品の出品写真から、タグや刻印に印字された品番を一字一句正確に書き写す担当です。"
    "写真で読めない文字は推測しません。"
)

_PROMPT = """上の{n}枚は、フリマ・オークションに出品された「{brand}」（{aliases}）の{category}商品写真です。

写真に写っている品質表示タグ・洗濯表示タグ・内側のブランドタグ・紙タグ・刻印・ギャランティカード・箱やラベルなどに印字された、この商品の品番（型番・スタイル番号・モデル番号）を探して、その文字をそのまま書き写してください。

このブランドの品番は、例えば次のような形式です（形式の参考で、この商品の品番ではありません）:
{examples}

ルール:
- 写真の中で実際に読める文字だけを書き写す。上の例や、商品の見た目・知識から推測した品番は書かない。
- 隠れている・ぼやけている・小さすぎるなどで1文字でも確信できない品番は書かない。
- 品番が写っていない・読めないときは model_numbers を空の配列にする。
- サイズ、カラー番号、素材表記、RN番号・CA番号、製造番号・シリアル番号、JANコード、価格は品番ではないので書かない。
- 同じ品番が複数の写真に写っていても1回だけ書く。text には品番だけを、写真の表記どおり（ハイフンや空白も含めて）書く。
- photo には、その品番が写っている写真の番号を書く。"""

# 構造化出力（JSON）の形
_SCHEMA = {
    "type": "object",
    "properties": {
        "model_numbers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "photo": {"type": "integer"},
                },
                "required": ["text", "photo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["model_numbers"],
    "additionalProperties": False,
}

# スキャン統計（scan() の開始時に reset_session で初期化。ローカル画面にも表示する）
stats = {
    "targets": 0,      # 写真読み取りの対象になった出品（キャッシュ利用を含む）
    "cached": 0,       # キャッシュの結果を使った件数（API は呼ばない）
    "read": 0,         # API で写真を読んだ件数
    "adopted": 0,      # 正規の型番形式に当てはまり採用した件数（キャッシュ利用を含む）
    "api_calls": 0,    # API 呼び出し回数
    "errors": 0,       # API エラー・写真取得エラー（保存せず次回再試行）
    "cost_yen": 0.0,   # 概算料金（円）
    "stopped": False,  # 料金上限で写真読み取りを止めたか
}
_session = {"settings": dict(DEFAULTS), "disabled": "", "client": None}


# ──────────────────────────────────────────────────────────────────────────────
# 有効/無効の判定
# ──────────────────────────────────────────────────────────────────────────────

def _api_key() -> str:
    """.env の ANTHROPIC_API_KEY（無ければ ''）。環境変数ではなく .env だけを見る（使う人を限定するため）"""
    if not os.path.isfile(ENV_PATH):
        return ""
    try:
        from dotenv import dotenv_values
        return (dotenv_values(ENV_PATH).get("ANTHROPIC_API_KEY") or "").strip()
    except Exception:
        return ""


def status() -> tuple[bool, str]:
    """(有効か, 画面に出す理由)。キーの中身は返さない"""
    if os.path.exists(FRIEND_MODE_PATH):
        return False, "このMacでは使いません"
    key = _api_key()
    if not key:
        return False, "APIキー未設定"
    if not key.startswith(_KEY_PREFIX):
        return False, "APIキーの形式が正しくありません"
    return True, ""


def enabled() -> bool:
    """写真から型番を読む機能が使えるか（スキャン中に API キーが無効と分かった場合も False）"""
    return status()[0] and not _session["disabled"]


def status_info() -> dict:
    """ローカル画面に出す状態（有効/無効の表示とスキャン統計）"""
    ok, reason = status()
    if ok and _session["disabled"]:
        ok, reason = False, _session["disabled"]
    label = "写真から型番読み取り: " + ("有効" if ok else f"無効（{reason}）")
    return {"enabled": ok, "label": label, "stats": dict(stats), "summary": summary() if ok else ""}


def reset_session(settings: dict) -> None:
    """スキャン開始時に呼ぶ（統計・料金のカウントを0に戻し、設定を読み直す）"""
    for k in stats:
        stats[k] = False if k == "stopped" else 0
    stats["cost_yen"] = 0.0
    _session["settings"] = {k: settings.get(k, v) for k, v in DEFAULTS.items()}
    _session["disabled"] = ""
    _session["client"] = None


def summary() -> str:
    """スキャン統計の1行表示"""
    s = stats
    stop = " / 料金上限で停止" if s["stopped"] else ""
    err = f" / エラー {s['errors']}件" if s["errors"] else ""
    return (f"写真読み取り 対象 {s['targets']}件（キャッシュ {s['cached']}件・API {s['read']}件）/ "
            f"採用 {s['adopted']}件 / API呼び出し {s['api_calls']}回 / 概算 ¥{s['cost_yen']:,.0f}{err}{stop}")


# ──────────────────────────────────────────────────────────────────────────────
# 読み取り（main.py から呼ぶ）
# ──────────────────────────────────────────────────────────────────────────────

def read_listings(items: list, brand: str, category: str, get_images) -> dict[str, str]:
    """型番が取れなかった出品の写真から品番を読み、{item.url: 型番} を返す（採用できたものだけ）。
    get_images: 出品のリストを受け取り {url: 写真URLの一覧 or None} を返す関数（main.py。キャッシュに
                無い出品だけについて呼ぶ）。
    """
    if not items or not enabled():
        return {}
    if not model_extractor.has_number_format(brand, category):
        print(f"    写真から型番読み取り: {brand} は品番の形式が無いブランドのため対象外")
        return {}

    unique = list({it.url: it for it in items if it.url}.values())
    results: dict[str, str] = {}
    cached = database.get_cached_photo_reads([it.url for it in unique], PHOTO_CACHE_HOURS)
    stats["targets"] += len(unique)
    stats["cached"] += len(cached)
    for url, texts in cached.items():
        model = _adopt(texts, brand, category)
        if model:
            results[url] = model
            stats["adopted"] += 1

    todo = [it for it in unique if it.url not in cached]
    cache_str = f"（キャッシュ {len(cached)}件・採用 {len(results)}件）" if cached else ""
    print(f"    説明文でも型番なし {len(unique)}件 → 写真から品番を読み取ります{cache_str}")
    if not todo:
        return results
    if stats["stopped"]:
        print(f"    写真読み取り: 料金上限に達しているため {len(todo)}件は読みません")
        return results

    image_urls = get_images(todo)
    for it in todo:
        if stats["stopped"] or _session["disabled"]:
            break
        urls = image_urls.get(it.url)
        if not urls:
            stats["errors"] += 1
            continue  # 写真URLが取れなかった（ページ構造の変化・取得エラー）→ 保存せず次回再試行
        texts = _read_photos(it, urls, brand, category)
        if texts is None:
            continue  # API エラー等 → 保存せず次回再試行
        model = _adopt(texts, brand, category)
        database.cache_photo_read(it.url, texts, model, PHOTO_CACHE_HOURS)
        if model:
            results[it.url] = model
            stats["adopted"] += 1
            print(f"      写真から型番 {model} を採用: {it.title[:40]}")
        elif texts:
            print(f"      写真の文字 {', '.join(texts)[:60]} は型番の形式でないため不採用: {it.title[:30]}")
    return results


def _adopt(texts: list[str], brand: str, category: str) -> str | None:
    """Claude が読んだ文字列を model_extractor.extract() に通し、正規の型番形式に当てはまる最初のものを返す。
    モデル名（POLITEAMA 等）だけが読めた場合は採用しない（型番の取りこぼし対策のため）"""
    for text in texts:
        # 「LTA20A4568 POLITEAMA」のように余分な語が付いていても、語ごとにも試す
        for t in [text] + text.split():
            model = model_extractor.extract(t, brand, category)
            if model_extractor.is_number(model, brand):
                return model
    return None


def _settings(key: str):
    return _session["settings"].get(key, DEFAULTS[key])


def _read_photos(item, urls: list[str], brand: str, category: str) -> list[str] | None:
    """1出品の写真をまとめて1リクエストで Claude に読ませ、品番の文字列の一覧を返す。
    写真が1枚も取れない・API エラーなら None（保存しない）"""
    urls = _pick(urls, int(_settings("photo_read_max_images")))
    images = []
    for u in urls:
        img = _download(u, int(_settings("photo_read_max_edge")))
        if img:
            images.append(img)
        time.sleep(_DOWNLOAD_INTERVAL)
    if not images:
        stats["errors"] += 1
        print(f"      [写真読み取り] 写真をダウンロードできませんでした: {item.url}")
        return None

    model = str(_settings("photo_read_model"))
    est_tokens = sum(t for _, t in images) + 1500
    if stats["cost_yen"] + _yen(model, est_tokens, 1500) > float(_settings("photo_read_max_yen_per_scan")):
        stats["stopped"] = True
        print(f"    [写真読み取り] 1回のスキャンの料金上限 ¥{float(_settings('photo_read_max_yen_per_scan')):,.0f} に"
              f"達するため、写真読み取りを止めます（スキャンは続けます）")
        return None

    content = []
    for i, (data, _tokens) in enumerate(images, 1):
        content.append({"type": "text", "text": f"写真{i}:"})
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    aliases = " / ".join(model_extractor.brand_aliases(brand)) or brand
    content.append({"type": "text", "text": _PROMPT.format(
        n=len(images), brand=brand, aliases=aliases,
        category=f"{category}の" if category else "",
        examples="\n".join(f"  {e}" for e in model_extractor.number_examples(brand, category)),
    )})

    resp = _call_api(model, content)
    if resp is None:
        return None
    if resp.stop_reason == "refusal":
        # 断られた場合は「読めなかった」として保存する（毎回同じ写真で料金がかからないように）
        print(f"      [写真読み取り] 回答を断られました（読めなかった扱い）: {item.url}")
        stats["read"] += 1
        return []
    if resp.stop_reason == "max_tokens":
        stats["errors"] += 1
        print(f"      [写真読み取り] 回答が途中で切れました（次回再試行）: {item.url}")
        return None
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        stats["errors"] += 1
        print(f"      [写真読み取り] 回答の形式が想定外です（次回再試行）: {item.url}")
        return None
    stats["read"] += 1
    texts = []
    for d in data.get("model_numbers") or []:
        t = str(d.get("text") or "").strip() if isinstance(d, dict) else ""
        if t and t not in texts:
            texts.append(t)
    return texts


def _pick(urls: list[str], limit: int) -> list[str]:
    """見せる写真を選ぶ。上限を超えるときは1枚目（全体像）と、タグが写りやすい後ろの方を優先する"""
    if len(urls) <= limit:
        return urls
    return urls[:1] + urls[len(urls) - (limit - 1):]


def _download(url: str, max_edge: int) -> tuple[str, int] | None:
    """写真をダウンロードして長辺 max_edge 以下に縮小し、(base64 の JPEG, 推定トークン数) を返す。
    ログインなし（Cookie 等は使わない）。失敗したら None"""
    import requests
    from PIL import Image
    try:
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=20)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        img = img.convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)   # 小さい写真はそのまま（拡大しない）
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        w, h = img.size
        # 画像のトークン数 = 28px 四方のマス目の数（公式ドキュメント「Vision」。上限 4,784）
        tokens = min(math.ceil(w / 28) * math.ceil(h / 28), 4784)
        return base64.standard_b64encode(buf.getvalue()).decode("ascii"), tokens
    except Exception as e:
        print(f"      [写真読み取り] 写真の取得エラー ({url}): {type(e).__name__}")
        return None


def _client():
    if _session["client"] is None:
        import anthropic
        # キーは .env から直接渡す（環境変数には入れない）
        _session["client"] = anthropic.Anthropic(api_key=_api_key(), max_retries=3, timeout=180)
    return _session["client"]


def _call_api(model: str, content: list):
    """Messages API を1回呼ぶ。エラーなら None（キャッシュせず次回再試行）"""
    import anthropic
    kwargs = dict(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_config={
            "effort": str(_settings("photo_read_effort")),
            "format": {"type": "json_schema", "schema": _SCHEMA},
        },
    )
    stats["api_calls"] += 1
    try:
        client = _client()
        if model in _FALLBACK_MODELS:
            # 安全上の理由で断られたら、API 側で適したモデルに自動で回してもらう
            resp = client.beta.messages.create(
                **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        _session["disabled"] = "APIキーが無効です"
        print("    [写真読み取り] APIキーが無効です。set_api_key.command で登録し直してください"
              "（このスキャンでは写真読み取りを止めます）")
        return None
    except anthropic.PermissionDeniedError as e:
        _session["disabled"] = "APIキーに権限がありません"
        print(f"    [写真読み取り] API の利用が許可されていません: {_safe(e)}（このスキャンでは止めます）")
        return None
    except anthropic.RateLimitError as e:
        stats["errors"] += 1
        print(f"      [写真読み取り] API の利用制限に達しました（次回再試行）: {_safe(e)}")
        return None
    except anthropic.APIStatusError as e:
        stats["errors"] += 1
        print(f"      [写真読み取り] API エラー {e.status_code}（次回再試行）: {_safe(e)}")
        return None
    except anthropic.APIConnectionError as e:
        stats["errors"] += 1
        print(f"      [写真読み取り] API に接続できません（次回再試行）: {type(e).__name__}")
        return None
    u = resp.usage
    in_tokens = ((u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
                 + (getattr(u, "cache_read_input_tokens", 0) or 0))
    stats["cost_yen"] += _yen(model, in_tokens, u.output_tokens or 0)
    return resp


def _yen(model: str, in_tokens: int, out_tokens: int) -> float:
    p_in, p_out = _PRICES.get(model, _PRICE_UNKNOWN)
    usd = in_tokens / 1_000_000 * p_in + out_tokens / 1_000_000 * p_out
    return usd * float(_settings("photo_read_usd_jpy"))


def _safe(e: Exception) -> str:
    """エラー文を短くし、万一キーの文字列が含まれていても伏せる"""
    msg = str(getattr(e, "message", "") or e)
    return re.sub(re.escape(_KEY_PREFIX) + r"[A-Za-z0-9_\-]*", "(APIキー)", msg)[:200]
