"""
写真から型番（品番）を読み、傷・汚れ（状態）を確認するモジュール

(1) 型番照合（require_model_number）で、タイトルにも商品説明文にも型番が無かった出品の写真を
    Claude API に見せ、品質表示タグ・内側のタグ・刻印などに印字された品番を読み取る（read_listings）。
(2) 利益が出る案件の写真を見せ、傷・汚れを5段階（新品同様 / 目立った傷なし / やや傷あり / 傷あり / 状態悪い）で
    判定する（inspect_deals）。結果はタイトルの状態と組み合わせ、悪いほうで利益を計算し直す
    （condition_checker.combine / analyzer.apply_photo_condition）。
  どちらも同じ1回の API 呼び出しで「品番」と「状態」の両方を答えさせる（料金を抑えるため。(1) で読んだ出品が
  案件になったときは、(2) で同じ写真を読み直さない）。

・使えるのは次の両方を満たすときだけ（それ以外は静かに無効。既存の動作は一切変わらない）:
    (a) .env に ANTHROPIC_API_KEY がある（set_api_key.command で登録する）
    (b) .rieki_friend_mode が無い（知り合いの Mac ではない）
  anthropic / Pillow の import は有効なときだけ行う。
・Claude が返した文字列は必ず model_extractor.extract() に通し、正規の型番形式に当てはまったものだけ
  採用する（読み間違い・推測の混入を防ぐ）。採用後は既存どおりメルカリ相場の取得へ進む。
・結果（読めなかった場合も含む）は DB にキャッシュし、再スキャンでは API を呼ばない。品番は出品URL単位
  （photo_read_cache）、状態は出品URL＋写真URLの一覧の単位（photo_inspect_cache。写真が差し替えられたら読み直す）。
  API エラーのときは保存せず、次回再試行する。
・1回のスキャンの料金上限（settings.photo_read_max_yen_per_scan）は品番・状態の両方で共有し、超えたら
  写真の確認だけ止める（スキャンは続ける）。
・有料のため既定はオフ。ローカル画面の「写真AI確認（有料・Claude API）」にチェックを入れたスキャンだけ
  (1)(2) を行う（settings.photo_check_enabled。既定 False。config.json・画面で指定が無ければ API は一切呼ばない）。
  型番のタグ読み取りは、先に無料の Mac 内 OCR（ocr_reader）で行い、そこで取れなかった出品だけをここで読む。
・API キーはログに出さない（エラー表示でも念のため伏せる）。
"""

import base64
import hashlib
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
    "photo_check_enabled": False,              # このスキャンで写真の確認（品番・状態）をするか（有料。ローカル画面のチェック。既定オフ）
    "photo_condition_check": True,             # 案件の写真で傷・汚れを確認するか
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
    "あなたは中古品の出品写真を確認する担当です。タグや刻印に印字された品番を一字一句正確に書き写し、"
    "写真に写っている傷・汚れを見落とさずに記録します。写真で読めない文字・見えない箇所は推測しません。"
)

_PROMPT_HEAD = """上の{n}枚は、フリマ・オークションに出品された「{brand}」（{aliases}）の{category}商品写真です。
出品タイトル: {title}
"""

_PROMPT_NUMBER = """
【1. 品番】
写真に写っている品質表示タグ・洗濯表示タグ・内側のブランドタグ・紙タグ・刻印・ギャランティカード・箱やラベルなどに印字された、この商品の品番（型番・スタイル番号・モデル番号）を探して、その文字をそのまま書き写してください。

このブランドの品番は、例えば次のような形式です（形式の参考で、この商品の品番ではありません）:
{examples}

ルール:
- 写真の中で実際に読める文字だけを書き写す。上の例や、商品の見た目・知識から推測した品番は書かない。
- 隠れている・ぼやけている・小さすぎるなどで1文字でも確信できない品番は書かない。
- 品番が写っていない・読めないときは model_numbers を空の配列にする。
- サイズ、カラー番号、素材表記、RN番号・CA番号、製造番号・シリアル番号、JANコード、価格は品番ではないので書かない。
- 同じ品番が複数の写真に写っていても1回だけ書く。text には品番だけを、写真の表記どおり（ハイフンや空白も含めて）書く。
- photo には、その品番が写っている写真の番号を書く。
"""

_PROMPT_NO_NUMBER = """
【1. 品番】
このブランドは品番を読む対象ではありません。model_numbers は空の配列にしてください。
"""

_PROMPT_CONDITION = """
【2. 状態（傷・汚れ）】
写真を1枚ずつよく見て、この商品の傷・汚れ・劣化を探し、中古品としての状態を判定してください。
特に次の箇所を確認してください: {checkpoints}

rank は次の5段階から1つ選ぶ（買い取って売り直す人の目で、厳しめに判定する）:
- 新品同様: 使用感が見られない（タグ付き・未使用に見える）
- 目立った傷なし: よく見ると小さな使用感はあるが、傷・汚れは目立たない
- やや傷あり: 小さな傷・汚れ・擦れが1〜2か所見える（着用・使用に支障なし）
- 傷あり: はっきり分かる傷・汚れ・擦れ・ほつれ等がある。または小さなものでも3か所以上ある
- 状態悪い: 破れ・穴・大きなシミ・ひどい劣化・部品やファーの欠損、全体に多数のシミ・汚れがあるなど、売るときに大きく値下がりする状態

severity の目安: 小 = 近くで見ると分かる / 中 = 普通に見て分かる / 大 = 遠目でも分かる・修理やクリーニングが必要

ルール:
- 写真で実際に見えたものだけを書く。タイトルや「中古だから」という推測では書かない。
- issues には見つけた傷・汚れを1件ずつ書く。location は箇所（例: 襟、右袖口、前立て、裾、フード、ファー、ロゴワッペン、ファスナー、持ち手、角、金具、風防、レンズ）、issue は内容（例: 黒ずみ、擦れ、羽毛抜け、ひび割れ、シミ、ほつれ、穴、毛玉、色あせ、メッキ剥がれ、傷）、severity は 小 / 中 / 大、photo はその写真の番号。
- 傷・汚れが見つからなければ issues は空の配列にする。
- 写真が3枚以下・全体の写真だけで寄りの写真が無い・遠い・暗い・ぼやけている・公式画像やカタログ画像だけ、などで状態を十分に確認できないときは confidence を「低」にする。主な箇所を寄りの写真でよく確認できたら「高」、その間は「中」。
- location・issue・unchecked はすべて日本語で書く。
- unchecked には、写真に写っていない・小さくて確認できなかった重要な箇所を短く書く（例: 「裏地・ポケット内側が写っていない」）。すべて確認できたら空文字にする。
"""

# ジャンルごとの確認箇所（_PROMPT_CONDITION の checkpoints）
_CHECKPOINTS = {
    "ダウン": "襟・フード・袖口・前立て・裾の黒ずみや皮脂汚れ、表地の擦れ・破れ・穴・ほつれ・テカり、羽毛抜け・羽毛の偏り、"
              "ファーの抜け・毛割れ、ロゴワッペンの剥がれ・色あせ、ファスナー・ボタン・スナップの欠損や傷、裏地の汚れ、毛玉、色あせ",
    "バッグ": "角の擦れ、持ち手・ショルダーの黒ずみやひび割れ、ヌメ革のシミ・焼け、内側の汚れ・ベタつき・ポケットの破れ、"
              "金具の傷・メッキ剥がれ、型崩れ、ファスナーの不具合",
    "時計":   "風防の傷・欠け、ケース・ベゼルの傷・打痕、ブレスレットやベルトの傷・伸び・劣化・切れ、文字盤のシミ・変色、リューズ",
    "サングラス": "レンズの傷・コーティング剥がれ、フレームの傷・塗装剥がれ、テンプルの曲がり・ゆるみ、鼻パッドの汚れ・劣化",
    "アクセサリー": "表面の傷、変色・くすみ、歪み・へこみ、石の欠け・欠損、留め具の不具合",
    "スカーフ": "シミ・汚れ、ほつれ・穴・糸引き、色あせ、強いシワ・折り跡、縁の巻きの傷み",
}
_CHECKPOINTS_DEFAULT = "全体の傷・汚れ・擦れ・破れ・色あせ・部品の欠損"

PHOTO_RANKS = ["新品同様", "目立った傷なし", "やや傷あり", "傷あり", "状態悪い"]

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
        "condition": {
            "type": "object",
            "properties": {
                "rank": {"type": "string", "enum": PHOTO_RANKS},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string"},
                            "issue": {"type": "string"},
                            "severity": {"type": "string", "enum": ["小", "中", "大"]},
                            "photo": {"type": "integer"},
                        },
                        "required": ["location", "issue", "severity", "photo"],
                        "additionalProperties": False,
                    },
                },
                "confidence": {"type": "string", "enum": ["高", "中", "低"]},
                "unchecked": {"type": "string"},
            },
            "required": ["rank", "issues", "confidence", "unchecked"],
            "additionalProperties": False,
        },
    },
    "required": ["model_numbers", "condition"],
    "additionalProperties": False,
}

# スキャン統計（scan() の開始時に reset_session で初期化。ローカル画面にも表示する）
stats = {
    "targets": 0,      # 写真から品番を読む対象になった出品（キャッシュ利用を含む）
    "cached": 0,       # 品番: キャッシュの結果を使った件数（API は呼ばない）
    "read": 0,         # API で写真を読んだ件数（品番・状態の両方を含む）
    "adopted": 0,      # 正規の型番形式に当てはまり採用した件数（キャッシュ利用を含む）
    "inspect_targets": 0,  # 状態: 写真で傷・汚れを確認する対象になった案件
    "inspected": 0,        # 状態: 判定できた案件（キャッシュ利用を含む）
    "inspect_cached": 0,   # 状態: キャッシュの結果を使った件数
    "downgraded": 0,       # 状態: 写真の判定で状態を下げた案件
    "dropped": 0,          # 状態: 写真の判定で利益が条件を下回り外した案件
    "api_calls": 0,    # API 呼び出し回数
    "errors": 0,       # API エラー・写真取得エラー（保存せず次回再試行）
    "cost_yen": 0.0,   # 概算料金（円）
    "seconds": 0.0,    # 写真の取得・API にかかった時間（秒）
    "stopped": False,  # 料金上限で写真の確認を止めたか
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
    """(使えるか, 画面に出す理由)。キーの中身は返さない（このスキャンでオフにしたかどうかは含まない）"""
    if os.path.exists(FRIEND_MODE_PATH):
        return False, "このMacでは使いません"
    key = _api_key()
    if not key:
        return False, "APIキー未設定"
    if not key.startswith(_KEY_PREFIX):
        return False, "APIキーの形式が正しくありません"
    return True, ""


def enabled() -> bool:
    """このスキャンで写真の確認（品番・状態）をするか（使える状態で、画面でオフにされておらず、
    スキャン中に API キーが無効と分かっていない）"""
    return status()[0] and not _session["disabled"] and bool(_settings("photo_check_enabled"))


def condition_enabled() -> bool:
    """このスキャンで案件の写真の傷・汚れを確認するか"""
    return enabled() and bool(_settings("photo_condition_check"))


def status_info() -> dict:
    """ローカル画面に出す状態（有効/無効の表示とスキャン統計）。
    available = この Mac で使えるか（画面の「写真をAIで確認する」のチェックを出すか）"""
    ok, reason = status()
    available = ok
    if ok and _session["disabled"]:
        ok, reason = False, _session["disabled"]
    label = "写真AI確認（有料・Claude API）: " + ("使えます" if ok else f"無効（{reason}）")
    return {"enabled": ok, "available": available, "label": label, "stats": dict(stats),
            "summary": summary() if ok else ""}


def reset_session(settings: dict) -> None:
    """スキャン開始時に呼ぶ（統計・料金のカウントを0に戻し、設定を読み直す）"""
    for k in stats:
        stats[k] = 0
    stats["stopped"] = False
    stats["cost_yen"] = 0.0
    stats["seconds"] = 0.0
    _session["settings"] = {k: settings.get(k, v) for k, v in DEFAULTS.items()}
    _session["disabled"] = ""
    _session["client"] = None


def summary() -> str:
    """スキャン統計の1行表示"""
    s = stats
    stop = " / 料金上限で停止" if s["stopped"] else ""
    err = f" / エラー {s['errors']}件" if s["errors"] else ""
    return (f"写真AI確認 品番: 対象 {s['targets']}件（キャッシュ {s['cached']}件）・採用 {s['adopted']}件 / "
            f"状態: 対象 {s['inspect_targets']}件・判定 {s['inspected']}件（キャッシュ {s['inspect_cached']}件）・"
            f"状態を下げて残した {s['downgraded']}件・利益不足で除外 {s['dropped']}件 / "
            f"API {s['api_calls']}回 / 概算 ¥{s['cost_yen']:,.0f} / {s['seconds'] / 60:.1f}分{err}{stop}")


# ──────────────────────────────────────────────────────────────────────────────
# (1) 品番の読み取り（main.py から呼ぶ）
# ──────────────────────────────────────────────────────────────────────────────

def read_listings(items: list, brand: str, category: str, get_images) -> dict[str, str]:
    """型番が取れなかった出品の写真から品番を読み、{item.url: 型番} を返す（採用できたものだけ）。
    同じ呼び出しで状態も判定し、photo_inspect_cache に保存する（案件になったら inspect_deals で使う）。
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

    started = time.time()
    try:
        image_urls = get_images(todo)
        for it in todo:
            if stats["stopped"] or _session["disabled"]:
                break
            urls = image_urls.get(it.url)
            if not urls:
                stats["errors"] += 1
                continue  # 写真URLが取れなかった（ページ構造の変化・取得エラー）→ 保存せず次回再試行
            got = _ask(it, urls, brand, category, ask_number=True)
            if got is None:
                continue  # API エラー等 → 保存せず次回再試行
            texts = got["texts"]
            model = _adopt(texts, brand, category)
            database.cache_photo_read(it.url, texts, model, PHOTO_CACHE_HOURS)
            if got["condition"]:
                database.cache_photo_inspect(it.url, _photos_key(urls), got["condition"], PHOTO_CACHE_HOURS)
            if model:
                results[it.url] = model
                stats["adopted"] += 1
                print(f"      写真から型番 {model} を採用: {it.title[:40]}")
            elif texts:
                print(f"      写真の文字 {', '.join(texts)[:60]} は型番の形式でないため不採用: {it.title[:30]}")
    finally:
        stats["seconds"] += time.time() - started
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


# ──────────────────────────────────────────────────────────────────────────────
# (2) 案件の状態（傷・汚れ）の確認（main.py から呼ぶ）
# ──────────────────────────────────────────────────────────────────────────────

def inspect_deals(deals: list, get_images, should_stop=None) -> dict[str, dict]:
    """案件（Deal）の写真で傷・汚れを確認し、{item.url: 状態の判定} を返す（判定できたものだけ）。
    見込み利益の大きい案件から順に確認する（料金上限で止まっても、大事な案件は確認済みになるように）。
    状態の判定 = {"rank", "issues": [{location, issue, severity, photo}], "confidence", "unchecked"}"""
    if not deals or not condition_enabled():
        return {}
    by_url: dict[str, object] = {}
    for d in sorted(deals, key=lambda d: d.estimated_profit, reverse=True):
        if d.item.url and d.item.url not in by_url:
            by_url[d.item.url] = d
    items = [d.item for d in by_url.values()]
    stats["inspect_targets"] += len(items)
    print(f"\n[写真で状態確認] 案件 {len(items)}件の写真で傷・汚れを確認します（見込み利益の大きい順）")

    started = time.time()
    results: dict[str, dict] = {}
    try:
        # 写真URLは説明文・品番の確認で取得済みならスクレイパー側で再取得しない
        image_urls = get_images(items)
        keys = {it.url: _photos_key(image_urls.get(it.url) or []) for it in items}
        cached = database.get_cached_photo_inspects(
            [(it.url, keys[it.url]) for it in items if image_urls.get(it.url)], PHOTO_CACHE_HOURS)
        stats["inspect_cached"] += len(cached)
        results.update(cached)
        if cached:
            print(f"    キャッシュの判定 {len(cached)}件を使います")
        for i, it in enumerate(items, 1):
            if it.url in results:
                continue
            if stats["stopped"] or _session["disabled"]:
                break
            if should_stop and should_stop():
                print("    [中断] 写真での状態確認を打ち切りました")
                break
            urls = image_urls.get(it.url)
            if not urls:
                stats["errors"] += 1
                print(f"      [写真で状態確認] 写真のURLを取得できませんでした: {it.url}")
                continue
            got = _ask(it, urls, by_url[it.url].brand, by_url[it.url].category, ask_number=False)
            if got is None or not got["condition"]:
                continue
            database.cache_photo_inspect(it.url, keys[it.url], got["condition"], PHOTO_CACHE_HOURS)
            results[it.url] = got["condition"]
            c = got["condition"]
            issues = "、".join(f"{x['location']}の{x['issue']}" for x in c["issues"][:4]) or "傷・汚れなし"
            print(f"      ({i}/{len(items)}) {c['rank']}（確信度 {c['confidence']}）{issues[:60]}: {it.title[:30]}")
    finally:
        stats["seconds"] += time.time() - started
    stats["inspected"] += len(results)
    return results


# 状態の判定の仕方（プロンプト・ランクの基準）を変えたら上げる。キャッシュのキーに含め、古い基準の判定を使わない
_INSPECT_VERSION = "2"


def _photos_key(urls: list[str]) -> str:
    """写真URLの一覧のキー（写真が差し替えられた・判定の基準を変えたら別のキーになる）"""
    return hashlib.sha1((_INSPECT_VERSION + "\n" + "\n".join(urls)).encode("utf-8")).hexdigest()


def _settings(key: str):
    return _session["settings"].get(key, DEFAULTS[key])


def _clean_condition(c) -> dict | None:
    """API の状態の判定を整える（形がおかしければ None）"""
    if not isinstance(c, dict) or c.get("rank") not in PHOTO_RANKS:
        return None
    issues = []
    for x in c.get("issues") or []:
        if not isinstance(x, dict):
            continue
        loc, issue = str(x.get("location") or "").strip(), str(x.get("issue") or "").strip()
        if not (loc or issue):
            continue
        sev = x.get("severity") if x.get("severity") in ("小", "中", "大") else "小"
        try:
            photo = int(x.get("photo") or 0)
        except (TypeError, ValueError):
            photo = 0
        issues.append({"location": loc[:30], "issue": issue[:40], "severity": sev, "photo": photo})
    conf = c.get("confidence") if c.get("confidence") in ("高", "中", "低") else "低"
    return {"rank": c["rank"], "issues": issues[:12], "confidence": conf,
            "unchecked": str(c.get("unchecked") or "").strip()[:120]}


def _ask(item, urls: list[str], brand: str, category: str, ask_number: bool) -> dict | None:
    """1出品の写真をまとめて1リクエストで Claude に見せ、品番と状態を答えさせる。
    返り値 {"texts": 品番の文字列の一覧, "condition": 状態の判定 or None}。
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
        print(f"      [写真AI確認] 写真をダウンロードできませんでした: {item.url}")
        return None

    model = str(_settings("photo_read_model"))
    est_tokens = sum(t for _, t in images) + 2500
    limit = float(_settings("photo_read_max_yen_per_scan"))
    if stats["cost_yen"] + _yen(model, est_tokens, 2500) > limit:
        stats["stopped"] = True
        print(f"    [写真AI確認] 1回のスキャンの料金上限 ¥{limit:,.0f} に達するため、"
              f"写真の確認を止めます（スキャンは続けます）")
        return None

    content = []
    for i, (data, _tokens) in enumerate(images, 1):
        content.append({"type": "text", "text": f"写真{i}:"})
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    aliases = " / ".join(model_extractor.brand_aliases(brand)) or brand
    examples = model_extractor.number_examples(brand, category) if ask_number else []
    prompt = _PROMPT_HEAD.format(n=len(images), brand=brand, aliases=aliases,
                                 category=f"{category}の" if category else "",
                                 title=(item.title or "")[:120])
    if ask_number and examples:
        prompt += _PROMPT_NUMBER.format(examples="\n".join(f"  {e}" for e in examples))
    else:
        prompt += _PROMPT_NO_NUMBER
    prompt += _PROMPT_CONDITION.format(checkpoints=_CHECKPOINTS.get(category, _CHECKPOINTS_DEFAULT))
    content.append({"type": "text", "text": prompt})

    resp = _call_api(model, content)
    if resp is None:
        return None
    if resp.stop_reason == "refusal":
        # 断られた場合は「読めなかった」として保存する（毎回同じ写真で料金がかからないように）
        print(f"      [写真AI確認] 回答を断られました（読めなかった扱い）: {item.url}")
        stats["read"] += 1
        return {"texts": [], "condition": None}
    if resp.stop_reason == "max_tokens":
        stats["errors"] += 1
        print(f"      [写真AI確認] 回答が途中で切れました（次回再試行）: {item.url}")
        return None
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    data = _parse_json(text)
    if data is None:
        stats["errors"] += 1
        print(f"      [写真AI確認] 回答の形式が想定外です（次回再試行）: {item.url}")
        return None
    stats["read"] += 1
    texts = []
    for d in (data.get("model_numbers") or []) if ask_number else []:
        t = str(d.get("text") or "").strip() if isinstance(d, dict) else ""
        if t and t not in texts:
            texts.append(t)
    return {"texts": texts, "condition": _clean_condition(data.get("condition"))}


def _parse_json(text: str) -> dict | None:
    """構造化出力の JSON を読む（念のため、前後に余計な文字やコードブロックが付いていても読む）"""
    text = (text or "").strip()
    for cand in (text, text[text.find("{"):text.rfind("}") + 1] if "{" in text else ""):
        if not cand:
            continue
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _pick(urls: list[str], limit: int) -> list[str]:
    """見せる写真を選ぶ。上限を超えるときは1枚目（全体像）と、タグが写りやすい後ろの方を優先する"""
    if len(urls) <= limit:
        return urls
    return urls[:1] + urls[len(urls) - (limit - 1):]


def download_bytes(url: str) -> bytes | None:
    """写真をダウンロードしてバイト列を返す（ログインなし。Cookie 等は使わない）。失敗したら None。
    無料の OCR（ocr_reader）でも使う"""
    import requests
    try:
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=20)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"      [写真] 写真の取得エラー ({url}): {type(e).__name__}")
        return None


def _download(url: str, max_edge: int) -> tuple[str, int] | None:
    """写真をダウンロードして長辺 max_edge 以下に縮小し、(base64 の JPEG, 推定トークン数) を返す。
    ログインなし（Cookie 等は使わない）。失敗したら None"""
    from PIL import Image
    data = download_bytes(url)
    if data is None:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)   # 小さい写真はそのまま（拡大しない）
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        w, h = img.size
        # 画像のトークン数 = 28px 四方のマス目の数（公式ドキュメント「Vision」。上限 4,784）
        tokens = min(math.ceil(w / 28) * math.ceil(h / 28), 4784)
        return base64.standard_b64encode(buf.getvalue()).decode("ascii"), tokens
    except Exception as e:
        print(f"      [写真AI確認] 写真の読み込みエラー ({url}): {type(e).__name__}")
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
