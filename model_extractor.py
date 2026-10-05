"""
型番・モデル名抽出モジュール

腕時計・サングラス・ダウンジャケットの一部ブランドは型番が
アルファニューメリックで規格化されているため正規表現で抽出する（_PATTERNS）。
アクセサリー（ジュエリー）やモンクレール等は型番が社内コード化されておらず、
コレクション名・スタイル名で呼ばれることが多いため名称リストで照合する（_MODEL_NAMES）。
"""

import re

# ── 正規表現で型番を抽出するブランド ────────────────────────────────────
# 優先度順（より具体的なものを先に）
_PATTERNS: dict[str, list[str]] = {
    # 腕時計
    "IWC": [
        r'\bIW\d{5,6}\b',                          # IW325101, IW510101
    ],
    "オメガ": [
        r'\b\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{2}\.\d{3}\b',  # 210.30.42.20.01.001
        r'\b\d{3,4}\.\d{2}(?:\.\d{2,3})?\b',              # 2531.80, 311.30.42
    ],
    "タグホイヤー": [
        r'\b[A-Z]{2,3}\d{4,5}[A-Z]{0,2}\.[A-Z]{2}\d{3,4}\b',  # CAR2111.BA0724
        r'\b[A-Z]{2,3}\d{4,5}[A-Z]{0,2}\b',                    # CAR2111, CBN2A1B
    ],
    "カルティエ": [
        r'\bW[A-Z0-9]{6,9}\b',         # WSTA0002, W69009Z3 (時計)
        r'\bCR[A-Z0-9]{6,8}\b',        # CRQZ0020 (時計)
    ],
    "ブライトリング": [
        r'\b[A-Z]\d{5}(?:\d{3,6})?[A-Z0-9]{0,4}\b',  # A13356121B1A1, A17321
    ],
    "パネライ": [
        r'\bPAM\s*0{0,2}\d{3,5}\b',    # PAM00312, PAM312, PAM 01564
    ],

    # サングラス（フレーム内側の刻印型番。出品タイトルに含まれることが多い）
    "レイバン": [
        r'\bRB\s?\d{4}\b',              # RB3025, RB 3025
    ],
    "グッチ": [
        r'\bGG\s?\d{4}[A-Z]?\b',        # GG0061S
    ],
    "セリーヌ": [
        r'\bCL\s?\d{5}[A-Z]?\b',        # CL40046U
    ],
    "プラダ": [
        r'\bSPR\s?\d{2}[A-Z]\b',        # SPR17W, SPR06W
    ],
    "ルイヴィトン": [
        r'\bZ\s?\d{4}[A-Z]?\b',         # Z1502W
    ],
    "シャネル": [
        r'\b(?:CH)?5\d{3}\b',           # 5380, CH5380
    ],
    "オークリー": [
        r'\bOO\s?\d{4}\b',              # OO9102
    ],
    "トムフォード": [
        r'\bTF\s?\d{3,4}\b',            # TF237, TF5401
    ],
    "バーバリー": [
        r'\bBE\s?\d{4}\b',              # BE4216
    ],
    "クリスチャンディオール": [
        # 実例: CD3182S, 0175S（中確度。型番がモデル名+カラーコードで揺れる）
        r'\bCD\d{3,4}S?\b',
        r'\b\d{3,4}S\b',
    ],

    # ダウンジャケット（タグの品番表記。ブランドにより確度が異なる）
    "カナダグース": [
        r'\b\d{4}[A-Z]\b',              # 2409L (Expedition), 3426M (Forester)
    ],
    "ピレネックス": [
        r'\bH[A-Z]{2}\d{3}\b',          # HMS011, HWY012 (公式サイトで確認済み)
    ],
    "タトラス": [
        r'(?<![A-Za-z0-9])[LM]TA\d{2}[A-Z]?\d{3,5}(?:-[A-Z0-9]+)?(?![A-Za-z0-9])',  # LTA13A4301, MTA13S4213, LTA1654530
    ],
    "ノースフェイス": [
        # ND/NDW=ダウン、NY/NYW・NP/NPW=シェル系。メルカリの表記ゆれ: ND91950 / nd91950 /
        # ND 91950 / ND-91950 / ND92438R・NP62130Z（末尾1文字は版違いのため除外して照合）
        # キッズ(NDJ)は exclude_words で除外済みなので対象外
        r'(?<![A-Za-z0-9])N(?:DW|YW|PW|D|Y|P)[\s\-]?\d{5}(?=[A-Z]?(?![A-Za-z0-9]))',
    ],
}

# 抽出結果からハイフンも取り除くブランド（ND-91950 → ND91950）
_STRIP_HYPHEN_BRANDS = {"ノースフェイス"}

# ── モデル名（コレクション名）で照合するブランド ────────────────────────
# 型番が社内コード化されておらず、出品タイトルにはコレクション名で
# 書かれることがほとんどのブランド（カルティエは時計と別軸でアクセサリーも持つ）
_MODEL_NAMES: dict[str, list[str]] = {
    "モンクレール": [
        "マヤ", "MAYA", "バディ", "BADY", "ベイカー", "BAKER",
        "フラグメント", "FRAGMENT", "モンジュネーブル", "MONTGENEVRE",
    ],
    "カルティエ": [
        "ラブブレス", "ラブリング", "LOVE", "ジュストアンクル", "JUSTE UN CLOU",
        "トリニティ", "TRINITY", "パンテール", "PANTHERE", "クラッシュ", "CRASH",
    ],
    "ティファニー": [
        "Tスマイル", "T SMILE", "オープンハート", "OPEN HEART",
        "ハードウェア", "HARDWEAR", "Tワイヤー", "T WIRE", "ノット", "KNOT",
        "セッティング", "SETTING",
    ],
    "ヴァンクリーフ&アーペル": [
        "アルハンブラ", "ALHAMBRA",
    ],
    "ブルガリ": [
        # セーブザチルドレン（チャリティー限定ライン）はビーゼロワンの部分文字列を
        # タイトルに含むため、通常のビーゼロワンより先に判定して価格帯を分離する
        "セーブザチルドレン",
        "ビーゼロワン", "B-ZERO1", "BZERO1", "セルペンティ", "SERPENTI",
        "パレンテシ", "PARENTESI", "ディーバ", "DIVA",
    ],
}


# ── 厳密なモデル名照合（正規表現・表記ゆれ・似た名前の区別） ────────────────
# _MODEL_NAMES の単純な部分一致では「ディオニシオドゥエ」が「ディオニシオ」に、
# 「ショートヌプシ」が「ヌプシジャケット」に誤マッチするため、ブランドによっては
# (正規名, [正規表現]) で照合し、どの表記で見つかっても正規名（カタカナ）を返す。
# 並びは優先度順（派生モデルを先に）。英字は前後が英字でないこと（ACE≠FACE）を条件にする。
_A = r'(?<![A-Za-z])'
_Z = r'(?![A-Za-z])'
_NOT_DERIVED = r'(?!\s*(?:DUE|ドゥエ|TRE|トレ|SEI|セイ))'   # デュベティカの派生（ドゥエ等）を除外

_STRICT_MODELS: dict[str, list[tuple[str, list[str]]]] = {
    "デュベティカ": [
        ("ディオニシオドゥエ", [r'ディオニシオ\s*ドゥエ', _A + r'DIONISIO\s*-?\s*DUE' + _Z]),
        ("ディオニシオ",       [r'ディオニシオ' + _NOT_DERIVED, _A + r'DIONISIO' + _NOT_DERIVED + _Z]),
        ("アダラドゥエ",       [r'アダラ\s*ドゥエ', _A + r'ADHARA\s*-?\s*DUE' + _Z]),
        ("アダラ",             [r'アダラ' + _NOT_DERIVED, _A + r'ADHARA' + _NOT_DERIVED + _Z]),
        ("カッパ",             [r'カッパ', _A + r'KAPPA' + _Z]),
        ("ティアセイ",         [r'ティア\s*セイ', _A + r'THIA\s*-?\s*SEI' + _Z]),
        ("ティア",             [r'(?<![ァ-ヶー])ティア(?!\s*(?:セイ|ラ|ドロップ))', _A + r'THIA' + _NOT_DERIVED + _Z]),
        ("アチェドゥエ",       [r'(?:アチェ|エース)\s*ドゥエ', _A + r'ACE\s*-?\s*DUE' + _Z]),
        ("エース",             [r'(?:アチェ|エース)' + _NOT_DERIVED, _A + r'ACE' + _NOT_DERIVED + _Z]),
        ("フェーベドゥエ",     [r'フェー?ベ\s*ドゥエ', _A + r'FEBE\s*-?\s*DUE' + _Z]),
        ("フェーベ",           [r'フェー?ベ' + _NOT_DERIVED, _A + r'FEBE' + _NOT_DERIVED + _Z]),
        ("デネブ",             [r'デネブ' + _NOT_DERIVED, _A + r'DENEB' + _NOT_DERIVED + _Z]),
        ("デイモス",           [r'デイモス', _A + r'DEIMOS' + _Z]),
        ("アリステオ",         [r'アリステオ', _A + r'ARISTEO' + _Z]),
        ("エフィラ",           [r'エフィラ' + _NOT_DERIVED, _A + r'EFIRA' + _NOT_DERIVED + _Z]),
        ("アリア",             [r'(?<![ァ-ヶー])アリア(?![ァ-ヶー])', _A + r'A[LR]IA' + _Z]),
        ("レキシ",             [r'レキシ' + _NOT_DERIVED, _A + r'LEX[YI]' + _NOT_DERIVED + _Z]),
        ("ベガ",               [r'(?<![ァ-ヶー])ベガ(?!ス)', _A + r'VEGA' + _Z]),
        ("ポルーチェ",         [r'ポルーチェ', _A + r'POLLUCE' + _Z]),
        ("エラクレ",           [r'エラク[レル]', _A + r'ERACLE' + _Z]),
    ],
    "ノースフェイス": [
        ("ショートヌプシ",         [r'ショート\s*ヌプシ', _A + r'SHORT\s*NUPTSE' + _Z]),
        ("ヌプシベスト",           [r'ヌプシ\s*(?:ダウン\s*)?ベスト', _A + r'NUPTSE\s*(?:DOWN\s*)?VEST' + _Z]),
        ("ヌプシジャケット",       [r'(?<!ショート)(?<!ショート\s)ヌプシ\s*(?:ダウン\s*)?ジャケット',
                                    _A + r'(?<!SHORT\s)NUPTSE\s*(?:DOWN\s*)?JACKET' + _Z]),
        ("バルトロライトジャケット", [r'バルトロ\s*ライト', _A + r'BALTRO\s*LIGHT' + _Z]),
        ("バルトロジャケット",     [r'バルトロ(?!\s*ライト)', _A + r'BALTRO(?!\s*LIGHT)' + _Z]),
        ("アンタークティカパーカ", [r'アンタークティカ\s*パーカ', _A + r'ANTARCTICA\s*PARKA' + _Z]),
        ("マクマードパーカ",       [r'マクマード', _A + r'MC\s*MURDO' + _Z]),
        ("ヒマラヤンパーカ",       [r'ヒマラヤン', _A + r'HIMALAYAN' + _Z]),
        ("ヒムダウンパーカ",       [r'ヒム\s*ダウン', _A + r'HIM\s*DOWN' + _Z]),
        ("キャンプシエラベスト",   [r'キャンプ\s*シエラ\s*ベスト', _A + r'CAMP\s*SIERRA\s*VEST' + _Z]),
        ("キャンプシエラショート", [r'キャンプ\s*シエラ(?!\s*ベスト)', _A + r'CAMP\s*SIERRA(?!\s*VEST)' + _Z]),
        ("エレバスジャケット",     [r'エレバス', _A + r'ELEBUS' + _Z]),
        ("アコンカグアジャケット", [r'(?<!マグネ)アコンカグア(?!\s*(?:ベスト|フーディ))',
                                    _A + r'ACONCAGUA(?!\s*(?:VEST|HOOD))' + _Z]),
        ("ビレイヤーパーカ",       [r'ビレイヤー', _A + r'BELAYER' + _Z]),
        ("マウンテンダウンコート", [r'マウンテン\s*ダウン\s*コート', _A + r'MOUNTAIN\s*DOWN\s*COAT' + _Z]),
        ("マウンテンダウンジャケット", [r'マウンテン\s*ダウン\s*ジャケット', _A + r'MOUNTAIN\s*DOWN\s*JACKET' + _Z]),
        ("ライトヒートジャケット", [r'ライト\s*ヒート\s*ジャケット', _A + r'LIGHT\s*HEAT\s*JACKET' + _Z]),
        ("サミットシリーズ",       [r'サミット', _A + r'SUMMIT' + _Z]),
    ],
}

# 画面（app.py）のモデル名チェックボックスは _MODEL_NAMES から作られるため、
# 厳密照合ブランドも「正規名(カタカナ), 英字名」の並びで登録しておく（照合自体は _STRICT_MODELS）
_STRICT_EN = {
    "ディオニシオドゥエ": "DIONISIO DUE", "ディオニシオ": "DIONISIO", "アダラドゥエ": "ADHARA DUE",
    "アダラ": "ADHARA", "カッパ": "KAPPA", "ティアセイ": "THIA SEI", "ティア": "THIA",
    "アチェドゥエ": "ACE DUE", "エース": "ACE", "フェーベドゥエ": "FEBE DUE", "フェーベ": "FEBE",
    "デネブ": "DENEB", "デイモス": "DEIMOS", "アリステオ": "ARISTEO", "エフィラ": "EFIRA",
    "アリア": "ALIA", "レキシ": "LEXY", "ベガ": "VEGA", "ポルーチェ": "POLLUCE", "エラクレ": "ERACLE",
    "ショートヌプシ": "SHORT NUPTSE", "ヌプシベスト": "NUPTSE VEST", "ヌプシジャケット": "NUPTSE JACKET",
    "バルトロライトジャケット": "BALTRO LIGHT JACKET", "バルトロジャケット": "BALTRO JACKET",
    "アンタークティカパーカ": "ANTARCTICA PARKA", "マクマードパーカ": "MCMURDO PARKA",
    "ヒマラヤンパーカ": "HIMALAYAN PARKA", "ヒムダウンパーカ": "HIM DOWN PARKA",
    "キャンプシエラベスト": "CAMP SIERRA VEST", "キャンプシエラショート": "CAMP SIERRA SHORT",
    "エレバスジャケット": "ELEBUS JACKET", "アコンカグアジャケット": "ACONCAGUA JACKET",
    "ビレイヤーパーカ": "BELAYER PARKA", "マウンテンダウンコート": "MOUNTAIN DOWN COAT",
    "マウンテンダウンジャケット": "MOUNTAIN DOWN JACKET", "ライトヒートジャケット": "LIGHT HEAT JACKET",
    "サミットシリーズ": "SUMMIT",
}
for _brand, _models in _STRICT_MODELS.items():
    _MODEL_NAMES[_brand] = [x for _name, _ in _models for x in (_name, _STRICT_EN[_name])]

_STRICT_COMPILED = {
    b: [(name, [re.compile(p, re.IGNORECASE) for p in pats]) for name, pats in models]
    for b, models in _STRICT_MODELS.items()
}

# 画面の「型番」候補に最初から出す代表的な型番（メルカリ売却実績の多いもの。2026-10 確認）
KNOWN_MODEL_NUMBERS: dict[str, list[str]] = {
    "ノースフェイス": [
        "NDW91952", "ND92215", "ND91950", "ND92338", "ND92237", "ND92340", "ND92031",
        "ND92342", "ND18174", "ND91915", "ND91930", "ND92232", "ND92557", "ND92231",
    ],
}

# ── ブランド名の表記ゆれ（他ブランド混入チェック用） ──────────────────────
# キー: config.json の brand_name / キーワード先頭語。値: タイトルに含まれていればOKとする表記
BRAND_ALIASES: dict[str, list[str]] = {
    "デュベティカ": ["デュベティカ", "DUVETICA", "デュべティカ", "デュベチカ", "デュペティカ", "ドゥベティカ"],
    "ノースフェイス": ["ノースフェイス", "ノースフェース", "NORTH FACE", "NORTHFACE", "TNF",
                    "ノース・フェイス", "ノース フェイス"],
}


def brand_aliases(brand: str) -> list[str]:
    """ブランド名の表記ゆれ一覧（未登録ならブランド名そのもの）"""
    return BRAND_ALIASES.get(brand, [brand] if brand else [])


def has_brand(title: str, brand: str) -> bool:
    """タイトルにブランド名（いずれかの表記）が含まれるか。大文字小文字は区別しない"""
    t = (title or "").lower()
    return any(a.lower() in t for a in brand_aliases(brand))


def extract_name(title: str, brand: str) -> str | None:
    """モデル名だけを照合して返す（型番は見ない）。厳密照合ブランドは正規名を返す"""
    if brand in _STRICT_COMPILED:
        for name, pats in _STRICT_COMPILED[brand]:
            if any(p.search(title or "") for p in pats):
                return name
        return None
    title_upper = (title or "").upper()
    for name in _MODEL_NAMES.get(brand, []):
        if name.upper() in title_upper:
            return name.upper()
    return None


def is_strict_brand(brand: str) -> bool:
    return brand in _STRICT_COMPILED


def english_name(model: str) -> str | None:
    """厳密照合モデルの英字表記（例: フェーベ → FEBE）。メルカリ相場の再検索に使う"""
    return _STRICT_EN.get(model)


def extract(title: str, brand: str = "") -> str | None:
    """
    商品タイトルから型番 or モデル名を抽出して返す。
    見つからない場合は None。
    """
    for pattern in _PATTERNS.get(brand, []):
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            sep = r'[\s\-]+' if brand in _STRIP_HYPHEN_BRANDS else r'\s+'
            return re.sub(sep, '', match.group(0)).upper()

    # 型番が無ければモデル名で照合（型番が取れた商品は型番の相場を優先する）
    return extract_name(title, brand)


def normalize(model: str) -> str:
    """型番を正規化（比較用）"""
    return re.sub(r'[\s\-_]', '', model).upper()
