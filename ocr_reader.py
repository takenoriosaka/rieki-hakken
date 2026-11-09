"""
写真のタグから型番（品番）を無料で読むモジュール（Mac 内蔵の文字認識 = Apple Vision の VNRecognizeTextRequest）

型番照合（require_model_number）で、タイトルにも商品説明文にも型番が無かった出品の写真を
ダウンロードし、Mac の中で文字認識（OCR）して、読めた文字を説明文と同じように
model_extractor で照合する（read_listings）。外部の API は使わないので料金はかからない。

・使えるのは macOS で pyobjc-framework-Vision が入っているときだけ（それ以外は静かに無効）。
  ローカル画面の「写真のタグ読み取り（無料・Mac内OCR）」のチェック（settings.photo_ocr_enabled、既定オン）で
  1回ごとにオン/オフできる。
・有料の写真AI確認（photo_reader、Claude API）とは別。こちらで型番が取れなかった出品だけを、
  有料の確認をオンにしたときに photo_reader へ回す（main.py）。
・採用するのは model_extractor の正規の型番形式に当てはまるものと、写真にブランド名も写っている場合の
  登録済みモデル名だけ（読み間違い・無関係な文字の混入を防ぐ）。OCR でよくある読み間違い
  （数字の 0 を O、1 を I・l と読む）は、数字に挟まれた・数字に隣接した文字だけを直した版でも照合する。
・結果は photo_ocr_cache に「出品URL＋写真URLの一覧＋OCR の版（_OCR_VERSION）」のキーで保存する
  （写真が差し替えられたら読み直す）。読めた文字の全文を保存し、型番の照合はキャッシュからも毎回やり直す
  （model_extractor の形式を増やしたら OCR し直さずに反映される）。
・ログインなし（Cookie 等は使わない）。写真のダウンロードは photo_reader.download_bytes を使う。

傷・汚れの判定（状態）は Mac 内のローカル AI（Ollama）で行う（local_inspector。入口は inspect_deals_local でも呼べる）。
"""

import hashlib
import re
import sys
import time

import database
import model_extractor

# OCR の仕方（認識レベル・言語・画像の前処理）を変えたら上げる。キャッシュのキーに含める
_OCR_VERSION = "1"
OCR_CACHE_HOURS = 24 * 90
_LANGUAGES = ["ja-JP", "en-US"]
# 1出品あたりに読む写真の上限（ラクマ・トレファクは多くて20枚程度）
_MAX_IMAGES = 30
# 写真のダウンロード間隔（秒）。画像配信サーバーへの負荷を抑える（photo_reader と同じ）
_DOWNLOAD_INTERVAL = 0.3

DEFAULTS = {
    "photo_ocr_enabled": True,   # このスキャンで写真のタグを無料の OCR で読むか（ローカル画面のチェック）
}

stats = {
    "targets": 0,   # 写真のタグを読む対象になった出品
    "cached": 0,    # キャッシュの OCR 結果を使った件数
    "read": 0,      # OCR した出品の件数
    "images": 0,    # OCR した写真の枚数
    "adopted": 0,   # 型番（またはモデル名）を採用した件数（キャッシュ利用を含む）
    "errors": 0,    # 写真URL・写真の取得エラー（保存せず次回再試行）
    "seconds": 0.0,
}
_session = {"settings": dict(DEFAULTS)}
_vision = {"checked": False, "ok": False, "reason": ""}


def _load_vision() -> bool:
    if not _vision["checked"]:
        _vision["checked"] = True
        if sys.platform != "darwin":
            _vision["reason"] = "Mac 以外では使えません"
        else:
            try:
                import Vision  # noqa: F401
                import Foundation  # noqa: F401
                _vision["ok"] = True
            except Exception:
                _vision["reason"] = "pyobjc-framework-Vision が入っていません"
    return _vision["ok"]


def available() -> bool:
    """この Mac で OCR を使えるか（画面のチェックの状態は含まない）"""
    return _load_vision()


def enabled() -> bool:
    return available() and bool(_session["settings"].get("photo_ocr_enabled", True))


def reset_session(settings: dict) -> None:
    for k in stats:
        stats[k] = 0
    stats["seconds"] = 0.0
    _session["settings"] = {k: settings.get(k, v) for k, v in DEFAULTS.items()}


def status_info() -> dict:
    ok = available()
    return {"available": ok, "enabled": enabled(), "reason": _vision["reason"], "stats": dict(stats),
            "label": "写真のタグ読み取り（無料・Mac内OCR）: " + ("使えます" if ok else f"無効（{_vision['reason']}）")}


def summary() -> str:
    s = stats
    err = f" / エラー {s['errors']}件" if s["errors"] else ""
    return (f"写真のタグ読み取り（無料OCR）: 対象 {s['targets']}件（キャッシュ {s['cached']}件）・"
            f"採用 {s['adopted']}件 / OCR {s['read']}件・写真 {s['images']}枚 / {s['seconds'] / 60:.1f}分{err}")


# ──────────────────────────────────────────────────────────────────────────────
# 文字認識
# ──────────────────────────────────────────────────────────────────────────────

def ocr_image(data: bytes) -> list[str]:
    """画像（JPEG/PNG/WebP のバイト列）の文字を認識し、行の一覧を返す（読めなければ []）"""
    if not data or not _load_vision():
        return []
    import objc
    import Vision
    from Foundation import NSData
    with objc.autorelease_pool():
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setRecognitionLanguages_(_LANGUAGES)
        req.setUsesLanguageCorrection_(False)   # 品番は辞書の単語ではないので補正しない
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
            NSData.dataWithBytes_length_(data, len(data)), None)
        ok, _err = handler.performRequests_error_([req], None)
        if not ok:
            return []
        lines = []
        for obs in req.results() or []:
            cands = obs.topCandidates_(1)
            if cands and len(cands):
                t = str(cands[0].string()).strip()
                if t:
                    lines.append(t)
        return lines


def ocr_photos(urls: list[str]) -> tuple[str, int] | None:
    """写真をダウンロードして順に OCR し、(全文（写真ごとに空行で区切る）, OCR できた枚数) を返す。
    1枚もダウンロードできなければ None"""
    import photo_reader
    texts, n = [], 0
    for u in urls[:_MAX_IMAGES]:
        data = photo_reader.download_bytes(u)
        time.sleep(_DOWNLOAD_INTERVAL)
        if data is None:
            continue
        n += 1
        texts.append("\n".join(ocr_image(data)))
    if not n:
        return None
    return "\n\n".join(texts), n


# ──────────────────────────────────────────────────────────────────────────────
# 読めた文字からの型番の照合
# ──────────────────────────────────────────────────────────────────────────────

def _fix_digits(line: str) -> str:
    """OCR でよくある読み間違いを直した行（数字に隣接する O→0、I・l・|→1 だけ。数字を3つ以上含む語のみ）"""
    out = []
    for word in line.split(" "):
        if sum(c.isdigit() for c in word) < 3:
            out.append(word)
            continue
        chars = list(word)
        for i, c in enumerate(chars):
            if c not in "OoIl|":
                continue
            prev = chars[i - 1] if i > 0 else ""
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if prev.isdigit() or nxt.isdigit():
                chars[i] = "0" if c in "Oo" else "1"
        out.append("".join(chars))
    return " ".join(out)


# ブランドごとの OCR の読み間違いの直し方（_fix_digits の後に当てる）。品番の形が決まっている部分だけ直す
_BRAND_FIXES = {
    # タトラスの品番の先頭 L（レディース）を I・1・l・| と読む（実例: タグの LTA-4146 を「ITA-4146」と読んだ 2026-11）
    "タトラス": [(re.compile(r'(?<![A-Za-z0-9])[I1l|]T(?=(?:AT|KE|LA|SE|A|K)[\s\-]?\d)'), "LT")],
}


def _fix_line(line: str, brand: str) -> str:
    line = _fix_digits(line)
    for pat, repl in _BRAND_FIXES.get(brand, []):
        line = pat.sub(repl, line)
    return line


def extract_model(text: str, brand: str, category: str = "") -> str | None:
    """OCR の全文から型番を探す（説明文と同じ model_extractor の照合）。
    型番は1行ずつ照合し（行をまたいで別の文字とつながらないように）、見つからなければ読み間違いを直した行でも探す。
    型番が無ければ登録済みのモデル名（写真にブランド名も写っているときだけ）"""
    if not text:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for fix in (False, True):
        for ln in lines:
            v = _fix_line(ln, brand) if fix else ln
            if fix and v == ln:
                continue
            m = model_extractor.extract(v, brand, category)
            if model_extractor.is_number(m, brand):
                return m
    if model_extractor.has_brand(text, brand):
        name = model_extractor.extract_name(text, brand)
        if name:
            return name
    return None


# ──────────────────────────────────────────────────────────────────────────────
# main.py から呼ぶ
# ──────────────────────────────────────────────────────────────────────────────

def photos_key(urls: list[str]) -> str:
    return hashlib.sha1((_OCR_VERSION + "\n" + "\n".join(urls)).encode("utf-8")).hexdigest()


def read_listings(items: list, brand: str, category: str, get_images, should_stop=None,
                  numbers_only: bool = False, label: str = "説明文でも型番なし") -> dict[str, str]:
    """型番が取れなかった出品の写真を OCR し、{item.url: 型番 or モデル名} を返す（採用できたものだけ）。
    get_images: 出品のリストを受け取り {url: 写真URLの一覧 or None} を返す関数（main.py）
    numbers_only: 品番だけを返す（写真のモデル名は返さない）。タイトルにモデル名・品番がある出品でも写真のタグの品番を
    優先するキーワード（config の prefer_photo_model。カナダグース）で、タイトルの代わりに使う品番を探すとき"""
    if not items or not enabled():
        return {}
    unique = list({it.url: it for it in items if it.url}.values())
    stats["targets"] += len(unique)
    print(f"    {label} {len(unique)}件 → 写真のタグを読み取ります（無料・Mac内OCR）")
    started = time.time()
    results: dict[str, str] = {}
    cached: dict[str, str] = {}
    try:
        image_urls = get_images(unique)
        keys = {it.url: photos_key(image_urls.get(it.url) or []) for it in unique if image_urls.get(it.url)}
        cached = database.get_cached_photo_ocr(list(keys.items()), OCR_CACHE_HOURS)
        stats["cached"] += len(cached)
        for it in unique:
            if should_stop and should_stop():
                print("    [中断] 写真のタグ読み取りを打ち切りました")
                break
            urls = image_urls.get(it.url)
            if not urls:
                stats["errors"] += 1
                continue   # 写真URLが取れなかった → 保存せず次回再試行
            if it.url in cached:
                text = cached[it.url]
            else:
                got = ocr_photos(urls)
                if got is None:
                    stats["errors"] += 1
                    print(f"      [写真のタグ読み取り] 写真をダウンロードできませんでした: {it.url}")
                    continue
                text, n = got
                stats["read"] += 1
                stats["images"] += n
            model = extract_model(text, brand, category)
            if it.url not in cached:
                database.cache_photo_ocr(it.url, keys[it.url], text, model, OCR_CACHE_HOURS)
            if numbers_only and not model_extractor.is_number(model, brand):
                model = None   # キャッシュには通常どおり保存済み。ここでは品番だけを返す
            if model:
                results[it.url] = model
                stats["adopted"] += 1
                print(f"      写真のタグから {model} を採用: {it.title[:40]}")
    finally:
        stats["seconds"] += time.time() - started
    print(f"    写真のタグ読み取り: {len(unique)}件中 {len(results)}件で型番・モデル名を採用"
          f"（キャッシュ {len(cached)}件・{time.time() - started:.0f}秒）")
    return results


def inspect_deals_local(deals: list, get_images, should_stop=None) -> dict[str, dict]:
    """Mac 内のローカル AI（Ollama）で案件の写真の傷・汚れを判定する（local_inspector.inspect_deals に任せる）。
    返り値は photo_reader.inspect_deals と同じ形 {item.url: {"rank", "issues", "confidence", "unchecked"}}"""
    import local_inspector
    return local_inspector.inspect_deals(deals, get_images, should_stop=should_stop)
