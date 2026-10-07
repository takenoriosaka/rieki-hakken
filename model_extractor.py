"""
型番・モデル名抽出モジュール

腕時計・サングラス・ダウンジャケットの一部ブランドは型番が
アルファニューメリックで規格化されているため正規表現で抽出する（_PATTERNS）。
アクセサリー（ジュエリー）やモンクレール等は型番が社内コード化されておらず、
コレクション名・スタイル名で呼ばれることが多いため名称リストで照合する（_MODEL_NAMES）。
"""

import re
import unicodedata

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
        # RB2140 / rb2140 / RB 2140 / RB-2140 / 0RB2140 / RB2140F / RB2140 901 / RB2140901
        # （末尾1文字はアジアンフィット等の版違い。_normalize_rayban で整える）
        r'(?<![A-Za-z0-9])0?RB[\s\-]?\d{4}(?:[A-Z](?![A-Za-z]))?(?![A-Za-z])',
        # 「RB」が付かない 2140 / 2140F は、代表的な型番（_RAYBAN_MODELS）に限って拾う
        # （年・価格・サイズ等の4桁の数字を型番と誤認しないため。パターンは下で追加する）
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
        # HMS011, HWY012 (公式サイトで確認済み)。メルカリの表記ゆれ: hms011 / HMS 011 / HMS-011 /
        # HMI055P4004T2（品番の後ろに色・サイズコードが続く）→ HMS011 / HMI055
        r'(?<![A-Za-z0-9])H[A-Z]{2}[\s\-]?\d{3}(?!\d)',
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
_STRIP_HYPHEN_BRANDS = {"ノースフェイス", "ピレネックス"}

# ── レイバン: 仕入れ対象の代表型番と愛称（メルカリ売却済みで相場が付くもの。2026-10 確認）──
# 画面の「型番の候補」に「RB2140 ウェイファーラー」のように愛称付きで出す（愛称が無いものは空文字）。
# 型番の末尾1文字（F=アジアンフィット、N=新型 等）は別型番として扱う。
# メルカリ相場で RB2140F ¥11,000 / RB2140 ¥6,000 のように F 付きが高いことが多いため（2026-10 確認）。
_RAYBAN_MODELS: dict[str, str] = {}   # 型番 → 愛称（下の KNOWN_MODEL_NUMBERS の前で設定）


def _normalize_rayban(s: str) -> str:
    """レイバン型番の表記ゆれを整える: rb-2140 / 0RB2140 / RB 2140 → RB2140、2140F / rb2140f → RB2140F"""
    m = re.search(r'(\d{4})([A-Z])?', s.upper())
    if not m:
        return s.upper()
    return f"RB{m.group(1)}{m.group(2) or ''}"


# ── モデル名（コレクション名）で照合するブランド ────────────────────────
# 型番が社内コード化されておらず、出品タイトルにはコレクション名で
# 書かれることがほとんどのブランド（カルティエは時計と別軸でアクセサリーも持つ）
_MODEL_NAMES: dict[str, list[str]] = {
    # 並びは「日本語名(正規名), 英字名」のペア。画面のチェックボックスは日本語名から作り、
    # 仕入れ先検索・メルカリ相場は日本語名と英字名の両方で行う（model_names() 参照）。
    # 照合は並び順の優先度で行う（派生・限定ラインを先に）。
    "モンクレール": [
        "マヤ", "MAYA", "バディ", "BADY", "ベイカー", "BAKER",
        "フラグメント", "FRAGMENT", "モンジュネーブル", "MONTGENEVRE",
    ],
    "カルティエ": [
        "ラブブレス", "LOVE BRACELET", "ラブリング", "LOVE RING",
        "ジュストアンクル", "JUSTE UN CLOU",
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
    "レイバン": [
        # 愛称。型番（RB2140 等）が取れない出品だけ愛称で照合する（型番が優先）。
        # 派生モデルを先に（ニューウェイファーラー → ウェイファーラー、エリカメタル → エリカ）
        "ニューウェイファーラー", "NEW WAYFARER",
        "フォールディングウェイファーラー", "FOLDING WAYFARER",
        "ウェイファーラー", "WAYFARER",
        "クラブマスター", "CLUBMASTER",
        "アビエーター", "AVIATOR",
        "エリカメタル", "ERIKA METAL",
        "エリカ", "ERIKA",
        "ジャスティン", "JUSTIN",
        "ラウンドメタル", "ROUND METAL",
        "キャラバン", "CARAVAN",
        "オリンピアン", "OLYMPIAN",
        "ヘキサゴナル", "HEXAGONAL",
    ],
    "ブルガリ": [
        # セーブザチルドレン（チャリティー限定ライン）はビーゼロワンの部分文字列を
        # タイトルに含むため、通常のビーゼロワンより先に判定して価格帯を分離する
        "セーブザチルドレン", "SAVE THE CHILDREN",
        "ビーゼロワン", "B-ZERO1", "セルペンティ", "SERPENTI",
        "パレンテシ", "PARENTESI", "ディーバ", "DIVA",
    ],
}

# 照合だけに使う追加の表記ゆれ（正規名 → 表記）。空白・中黒・ハイフン・ドットは
# 照合時に無視するので「ラブ ブレス」「B.ZERO1」等は個別に書かなくてよい。
_MODEL_VARIANTS: dict[str, list[str]] = {
    "ラブブレス": ["LOVEブレス", "LOVEバングル", "ラブバングル"],
    "ラブリング": ["LOVEリング"],
    "パンテール": ["PANTHÈRE"],
    "モンジュネーブル": ["モンジュネーヴル"],
    "ビーゼロワン": ["BZERO1", "ビーゼロ1"],
    "ディーバ": ["ディーヴァ"],
    "ニューウェイファーラー": ["ニューウェイファラー"],
    "フォールディングウェイファーラー": ["フォールディングウェイファラー"],
    "ウェイファーラー": ["ウェイファラー", "ウェーファーラー"],
    "アビエーター": ["アビエイター", "アヴィエーター"],
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
    "ピレネックス": [
        # 派生モデルを先に（スプートニックソフト・スプートニックベスト → スプートニック、
        # グルノーブルファー → グルノーブル）。ファー付きのグルノーブルとソフトは相場が1万円ほど高い（2026-10 確認）
        ("アデル",               [r'(?<![ァ-ヶー])アデル', _A + r'ADELE' + _Z]),
        ("スプートニックソフト", [_A + r'(?:スプートニッ?ク|SPOUTNIC)\s*(?:ダウン\s*)?(?:ソフト|SOFT)' + _Z]),
        ("スプートニックベスト", [_A + r'(?:スプートニッ?ク|SPOUTNIC)\s*(?:ダウン\s*|DOWN\s*)?(?:ベスト|VEST)' + _Z]),
        ("スプートニック",       [_A + r'(?:スプートニッ?ク|SPOUTNIC)']),
        ("グルノーブルファー",   [_A + r'(?:グルノーブル|GRENOBLE)\s*(?:ファー|FUR)' + _Z]),
        ("グルノーブル",         [_A + r'(?:グルノーブル|GRENOBLE)' + _Z]),
        ("アヌシー",             [r'アヌシー?', _A + r'ANNECY' + _Z]),
        ("ボルドー",             [r'ボルドー', _A + r'BORDEAUX' + _Z]),
        ("ベルフォール",         [r'ベルフォー[ルト]', _A + r'BELFORT' + _Z]),
        ("オーセンティック",     [r'オーセンティック', _A + r'AUTHENTIC' + _Z]),
        # 「フランス」「バランス」に誤マッチしないよう、直前がカタカナでないこと
        ("ランス",               [r'(?<![ァ-ヶー])ランス', _A + r'REIMS' + _Z]),
        ("コクーン",             [r'コクーン', _A + r'COCOON' + _Z]),
        ("バロー",               [r'(?<![ァ-ヶー])バロー(?![ァ-ヶ])', _A + r'BARROW' + _Z]),
        ("カンヌ",               [r'(?<![ァ-ヶー])カンヌ', _A + r'CANNES' + _Z]),
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
    "アデル": "ADELE", "スプートニックソフト": "SPOUTNIC SOFT", "スプートニックベスト": "SPOUTNIC VEST",
    "スプートニック": "SPOUTNIC", "グルノーブルファー": "GRENOBLE FUR", "グルノーブル": "GRENOBLE",
    "アヌシー": "ANNECY", "ボルドー": "BORDEAUX", "ベルフォール": "BELFORT", "オーセンティック": "AUTHENTIC",
    "ランス": "REIMS", "コクーン": "COCOON", "バロー": "BARROW", "カンヌ": "CANNES",
}
for _brand, _models in _STRICT_MODELS.items():
    _MODEL_NAMES[_brand] = [x for _name, _ in _models for x in (_name, _STRICT_EN[_name])]

_STRICT_COMPILED = {
    b: [(name, [re.compile(p, re.IGNORECASE) for p in pats]) for name, pats in models]
    for b, models in _STRICT_MODELS.items()
}

# レイバンの代表型番（型番 → 愛称）。並びは画面の表示順（相場の高さ・売却件数の多い順）
_RAYBAN_MODELS.update({
    "RB4264": "",
    "RB2140F": "ウェイファーラー",
    "RB2132F": "ニューウェイファーラー",
    "RB3647N": "ダブルブリッジ",
    "RB3592": "",
    "RB3689": "アビエーター",
    "RB4258F": "",
    "RB3136": "キャラバン",
    "RB4171F": "エリカ",
    "RB3539": "エリカメタル",
    "RB3016F": "クラブマスター",
    "RB3447": "ラウンドメタル",
    "RB4259F": "",
    "RB4305F": "",
    "RB3548N": "ヘキサゴナル",
    "RB3548": "ヘキサゴナル",
    "RB4334D": "",
    "RB3681": "",
    "RB4105": "フォールディングウェイファーラー",
    "RB2132": "ニューウェイファーラー",
    "RB2176": "クラブマスターフォールディング",
    "RB4258": "",
    "RB4260D": "",
    "RB4187F": "クリス",
    "RB4259": "",
    "RB3119": "オリンピアン",
    "RB2180F": "",
    "RB4195": "",
    "RB3025": "アビエーター",
    "RB4165F": "ジャスティン",
    "RB4305": "",
    "RB4306": "",
    "RB3016": "クラブマスター",
    "RB4171": "エリカ",
    "RB2140": "ウェイファーラー",
    "RB3578": "",
    "RB2447": "",
    "RB4187": "クリス",
    "RB2180": "",
})
# 「RB」が付かない 2140 / 2140F は代表型番に限って拾う（年・価格・サイズ等の4桁と区別するため、
# 前後が数字・英字・小数点・桁区切り・円/年でないこと）
_PATTERNS["レイバン"].append(
    r'(?<![A-Za-z0-9.,¥￥\-/])(?:'
    + "|".join(sorted({m[2:6] for m in _RAYBAN_MODELS}))
    + r')[A-Z]?(?![A-Za-z0-9.,円年\-/])'
)

# ── グッチ時計: 仕入れ対象の代表型番とシリーズ名（メルカリ売却済みで相場が付くもの。2026-10 確認）──
# グッチはサングラス（GG0061S 等）と時計で同じブランド名を使うため、時計の型番は
# ジャンル別の照合（_CATEGORY_PATTERNS）で扱い、サングラスの型番と混ぜない。
#   現行品: YA + 6〜7桁（YA126402 / YA1264155。ya126402・YA 126402・YA-126402 も同じ型番）
#   旧型:   数字 + L/M（1500L・5500M 等。L=レディース、M=メンズで相場が違うので別型番）
# 並びは画面の表示順（現行品→旧型、それぞれ売却件数の多い順）
# Gフレーム(YA128xxx/YA147xxx)・トワール(YA112xxx)・インターロッキング(YA133xxx)は売却済みタイトルに
# 同じ型番が載る例が少なく（型番一致で0〜1件）、相場が出ないため候補に入れていない（2026-10 確認）
_GUCCI_WATCH_MODELS: dict[str, str] = {
    "YA157401": "グリップ",
    "YA157410": "グリップ",
    "YA157403": "グリップ",
    "YA157409": "グリップ",
    "YA157429": "グリップ",
    "YA1264155": "Gタイムレス",
    "YA1264106": "Gタイムレス",
    "YA1264125": "Gタイムレス",
    "YA1265007": "Gタイムレス",
    "YA1265013": "Gタイムレス",
    "YA126402": "Gタイムレス",
    "YA126401": "Gタイムレス",
    "YA136219": "ダイヴ",
    "YA136218": "ダイヴ",
    "YA136344": "ダイヴ",
    "YA142301": "GG2570",
    "YA101331": "Gクロノ",
    "YA139501": "ホースビット",
    "5500M": "Gクラス",
    "1500L": "バングル",
    "6300L": "ホースビット",
    "1400L": "バングル",
    "1900L": "バングル",
    "3900L": "スクエア",
    "5500L": "Gクラス",
    "6400L": "ホースビット",
    "6700L": "バングル",
    "9040M": "",
    "9000M": "",
    "3600L": "スクエア",
    "101M": "Gクロノ",
    "1800L": "チェンジベルト",
    "8600M": "Gメトロ",
    "2000M": "シェリーライン",
    "7900M": "スクエア",
}
# 旧型の「数字＋L/M」は代表型番に限って拾う（100M防水・200M 等を型番と誤認しないため）
_GUCCI_OLD = sorted({m for m in _GUCCI_WATCH_MODELS if not m.startswith("YA")}, key=len, reverse=True)
# L/M を省いた数字だけの表記（「グッチ 1500 腕時計」）は、L/M のどちらか一方しか無い型番に限る
# （5500L/5500M・9040M/9040L・9000M/9000L・3600L/3600M・2000M/2000L は相場が違うので数字だけでは決めない）。
# さらに数字だけのときはタイトルにブランド名と「時計」「ウォッチ」等が両方あるときだけ拾う（_extract_gucci_watch）
_GUCCI_BARE_OK = {"1500": "1500L", "1400": "1400L", "1900": "1900L", "1800": "1800L",
                  "6300": "6300L", "6400": "6400L", "6700": "6700L", "3900": "3900L", "8600": "8600M"}
_GUCCI_WATCH_WORDS = ("時計", "ウォッチ", "WATCH")
_GUCCI_YA_RE = re.compile(r'(?<![A-Za-z0-9])YA[\s\-]?(\d{6,7})(?!\d)', re.IGNORECASE)
# 前後が数字・英字・小数点・桁区切り・円/年でないこと。「Ref.1500L」「1500 L」「1500l」「7900M.1」も拾う
_GUCCI_OLD_RE = re.compile(
    r'(?<![A-Za-z0-9.,¥￥\-/])(' + "|".join(m[:-1] for m in _GUCCI_OLD) + r')\s?([LM])(?![A-Za-z0-9])',
    re.IGNORECASE)
_GUCCI_BARE_RE = re.compile(
    r'(?<![A-Za-z0-9.,¥￥\-/:])(' + "|".join(sorted(_GUCCI_BARE_OK)) + r')(?![A-Za-z0-9.,円年\-/%:]|\s?(?:mm|ミリ|本|個|g|ｇ|件|点|m|M|L)(?![A-Za-z]))',
    re.IGNORECASE)
_REF_RE = re.compile(r'(?:Ref|REF|ref)\s*[.:：]?\s*')


def _extract_gucci_watch(title: str) -> str | None:
    """グッチ時計の型番（YA126402 / 1500L）。サングラスの GG0061S 等は見ない"""
    t = unicodedata.normalize("NFKC", title or "")
    t = _REF_RE.sub(" ", t)            # Ref.1500L / Ref: YA126402 → 1500L / YA126402
    m = _GUCCI_YA_RE.search(t)
    if m:
        return "YA" + m.group(1)
    for m in _GUCCI_OLD_RE.finditer(t):
        key = m.group(1) + m.group(2).upper()
        if key in _GUCCI_WATCH_MODELS:
            return key
    # 数字だけ（1500）はブランド名と「時計」系の語がタイトルにあるときだけ
    up = t.upper()
    if any(w in up for w in _GUCCI_WATCH_WORDS) and has_brand(t, "グッチ"):
        m = _GUCCI_BARE_RE.search(t)
        if m:
            return _GUCCI_BARE_OK[m.group(1)]
    return None


# ジャンル別に型番の照合を切り替えるブランド: (ブランド, ジャンル) → 抽出関数。
# ここに無い組み合わせは従来どおり _PATTERNS[ブランド] で照合する（グッチのサングラスは GG0061S）
_CATEGORY_EXTRACTORS = {
    ("グッチ", "時計"): _extract_gucci_watch,
}

# 画面の「型番」候補に添える愛称（ブランド → {型番: 愛称}）。例: RB2140 → ウェイファーラー
MODEL_NUMBER_LABELS: dict[str, dict[str, str]] = {
    "レイバン": {k: v for k, v in _RAYBAN_MODELS.items() if v},
    "グッチ": {k: v for k, v in _GUCCI_WATCH_MODELS.items() if v},
}

# 画面の「型番」候補に最初から出す代表的な型番（メルカリ売却実績の多いもの。2026-10 確認）
KNOWN_MODEL_NUMBERS: dict[str, list[str]] = {
    "レイバン": list(_RAYBAN_MODELS),
    # ピレネックスはメルカリの売却済みタイトルに品番が載ることが少ない（モデル名が主）。
    # 品番検索で売却実績が4件以上あったものだけ候補にする（2026-10 確認）
    "ピレネックス": ["HMK009", "HMS011", "HMW012", "HMO009", "HMO050"],
    "ノースフェイス": [
        "NDW91952", "ND92215", "ND91950", "ND92338", "ND92237", "ND92340", "ND92031",
        "ND92342", "ND18174", "ND91915", "ND91930", "ND92232", "ND92557", "ND92231",
    ],
    # グッチは時計の型番だけ（サングラスの GG0061S 等は過去の検出実績から出る）
    "グッチ": list(_GUCCI_WATCH_MODELS),
}

# 型番の候補のうち、特定のジャンル専用のもの（ブランド → {型番: ジャンル}）。
# 画面では選択中のジャンルの型番だけを出し、リサーチでもそのジャンルのキーワードにだけ使う
MODEL_NUMBER_CATEGORIES: dict[str, dict[str, str]] = {
    "グッチ": {m: "時計" for m in _GUCCI_WATCH_MODELS},
}

# ── ブランド名の表記ゆれ（他ブランド混入チェック用） ──────────────────────
# キー: config.json の brand_name / キーワード先頭語。値: タイトルに含まれていればOKとする表記
BRAND_ALIASES: dict[str, list[str]] = {
    "デュベティカ": ["デュベティカ", "DUVETICA", "デュべティカ", "デュベチカ", "デュペティカ", "ドゥベティカ"],
    "ノースフェイス": ["ノースフェイス", "ノースフェース", "NORTH FACE", "NORTHFACE", "TNF",
                    "ノース・フェイス", "ノース フェイス"],
    # モデル名（英字）で相場を取るブランド。英字だけのタイトル（MONCLER MAYA 等）も
    # メルカリ相場のブランド名チェックを通るようにする
    "モンクレール": ["モンクレール", "MONCLER"],
    "カルティエ": ["カルティエ", "CARTIER"],
    "ティファニー": ["ティファニー", "TIFFANY"],
    "ヴァンクリーフ&アーペル": ["ヴァンクリーフ", "ヴァン クリーフ", "ヴァン・クリーフ", "VAN CLEEF", "VANCLEEF"],
    "ブルガリ": ["ブルガリ", "BVLGARI", "BULGARI"],
    # 時計・サングラス: 英字ブランド名だけのタイトル（Ray-Ban RB2140 等）も相場に入れる
    "ピレネックス": ["ピレネックス", "ピレネクス", "PYRENEX", "PYRNEX"],
    "レイバン": ["レイバン", "RAY-BAN", "RAYBAN", "RAY BAN", "RAY・BAN", "RAY･BAN"],
    "オメガ": ["オメガ", "OMEGA"],
    "タグホイヤー": ["タグホイヤー", "タグ・ホイヤー", "タグ ホイヤー", "TAG HEUER", "TAGHEUER", "TAG-HEUER"],
    "ブライトリング": ["ブライトリング", "BREITLING"],
    "パネライ": ["パネライ", "PANERAI"],
    "グッチ": ["グッチ", "GUCCI"],
    "セリーヌ": ["セリーヌ", "CELINE", "CÉLINE"],
    "プラダ": ["プラダ", "PRADA"],
    "ルイヴィトン": ["ルイヴィトン", "ルイ・ヴィトン", "ルイ ヴィトン", "ヴィトン", "LOUIS VUITTON", "VUITTON"],
    "シャネル": ["シャネル", "CHANEL"],
    "オークリー": ["オークリー", "OAKLEY"],
    "トムフォード": ["トムフォード", "トム・フォード", "トム フォード", "TOM FORD", "TOMFORD"],
    "バーバリー": ["バーバリー", "BURBERRY"],
    "クリスチャンディオール": ["クリスチャンディオール", "ディオール", "DIOR"],
    "ヴェルサーチ": ["ヴェルサーチ", "ヴェルサーチェ", "VERSACE"],
    "リックオウエンス": ["リックオウエンス", "リック・オウエンス", "リック オウエンス", "RICK OWENS", "RICKOWENS"],
    "ジャンポールゴルチエ": ["ジャンポールゴルチエ", "ゴルチエ", "GAULTIER"],
    "ロエベ": ["ロエベ", "LOEWE"],
    "ミュウミュウ": ["ミュウミュウ", "MIUMIU", "MIU MIU"],
    "バレンシアガ": ["バレンシアガ", "BALENCIAGA"],
}


def brand_aliases(brand: str) -> list[str]:
    """ブランド名の表記ゆれ一覧（未登録ならブランド名そのもの）"""
    return BRAND_ALIASES.get(brand, [brand] if brand else [])


def has_brand(title: str, brand: str) -> bool:
    """タイトルにブランド名（いずれかの表記）が含まれるか。大文字小文字は区別しない"""
    t = (title or "").lower()
    return any(a.lower() in t for a in brand_aliases(brand))


# ── モデル名のカタカナ/英字表記（照合・仕入れ先検索・メルカリ相場で共用） ─────────

def _n(s: str) -> str:
    """モデル名の比較用（空白・ハイフン・ドット・中黒・下線を除去して大文字化）"""
    return re.sub(r"[\s\-_.・･]", "", s or "").upper()


def _build_model_index() -> dict[str, list[tuple[str, str | None, list[str]]]]:
    """ブランド → [(正規名(カタカナ), 英字名, 照合用の全表記)]（優先度順）"""
    index: dict[str, list[tuple[str, str | None, list[str]]]] = {}
    for brand, names in _MODEL_NAMES.items():
        entries: list[tuple[str, str | None, list[str]]] = []
        i = 0
        while i < len(names):
            name = names[i]
            en = None
            if not name.isascii() and i + 1 < len(names) and names[i + 1].isascii():
                en = names[i + 1]
                i += 1
            i += 1
            if name.isascii():
                # 英字だけのモデル名（カタカナ名なし）。英字名をそのまま正規名にする
                entries.append((name, None, [name]))
                continue
            forms = [name] + ([en] if en else []) + _MODEL_VARIANTS.get(name, [])
            entries.append((name, en, forms))
        index[brand] = entries
    return index


_MODEL_INDEX = _build_model_index()
# 正規化した表記 → (ブランド, 正規名, 英字名)。ブランド不明時の逆引き用
_FORM_LOOKUP: dict[str, list[tuple[str, str, str | None]]] = {}
for _b, _entries in _MODEL_INDEX.items():
    for _canon, _en, _forms in _entries:
        for _f in _forms:
            _FORM_LOOKUP.setdefault(_n(_f), []).append((_b, _canon, _en))


def _lookup(model: str, brand: str = "") -> tuple[str, str | None, list[str]] | None:
    key = _n(model)
    hits = _FORM_LOOKUP.get(key, [])
    if brand:
        hits = [h for h in hits if h[0] == brand] or ([] if brand in _MODEL_INDEX else hits)
    if not hits:
        return None
    b, canon, _en = hits[0]
    for c, en, forms in _MODEL_INDEX[b]:
        if c == canon:
            return c, en, forms
    return None


def model_names(model: str, brand: str = "") -> tuple[str, str | None]:
    """モデル名（カタカナ・英字・表記ゆれのどれでもよい）から (カタカナ正規名, 英字名) を返す。
    例: トリニティ / TRINITY → ("トリニティ", "TRINITY")、DIONISIO → ("ディオニシオ", "DIONISIO")。
    未登録のモデルは (model, None)。英字だけのモデル名は (英字名, None)。
    """
    hit = _lookup(model, brand)
    if hit is None:
        return model, None
    return hit[0], hit[1]


def model_aliases(model: str, brand: str = "") -> list[str]:
    """照合用の全表記（正規名・英字名・表記ゆれ）。未登録なら [model]"""
    hit = _lookup(model, brand)
    return list(hit[2]) if hit else [model]


def search_names(model: str, brand: str = "") -> list[str]:
    """検索に使うモデル名の一覧: [カタカナ名, 英字名]。
    英字名が無い・カタカナ名と同じ（正規化後）・モデル名が英字のみ → 1件だけ返す。
    型番（数字を含み名称リストに無いもの）も [model] のまま。
    """
    kana, en = model_names(model, brand)
    names = [kana]
    if en and _n(en) != _n(kana):
        names.append(en)
    return names


def extract_name(title: str, brand: str) -> str | None:
    """モデル名だけを照合して返す（型番は見ない）。どの表記で見つかっても正規名（カタカナ）を返す"""
    if brand in _STRICT_COMPILED:
        for name, pats in _STRICT_COMPILED[brand]:
            if any(p.search(title or "") for p in pats):
                return name
        return None
    t = _n(title)
    for canon, _en, forms in _MODEL_INDEX.get(brand, []):
        if any(_n(f) in t for f in forms):
            return canon
    return None


# 型番の相場を「売却済みタイトルから同じ型番が抽出できるもの」だけで出すブランド。
# レイバンはメルカリで「RB2140」と検索すると RB2140F（アジアンフィット、相場が高い）も
# 混ざるため、型番ごとに分けて相場を出す。
# グッチ時計も同様（「グッチ 1500L」で検索すると 1400L・1900L 等も混ざる）。
_EXACT_NUMBER_BRANDS = {"レイバン", ("グッチ", "時計")}


def exact_number_market(brand: str, category: str = "") -> bool:
    return brand in _EXACT_NUMBER_BRANDS or (brand, category) in _EXACT_NUMBER_BRANDS


def has_category_extractor(brand: str) -> bool:
    """ジャンルによって型番の照合が変わるブランドか（グッチ: 時計とサングラス）"""
    return any(b == brand for b, _c in _CATEGORY_EXTRACTORS)


def is_strict_brand(brand: str) -> bool:
    return brand in _STRICT_COMPILED


def english_name(model: str, brand: str = "") -> str | None:
    """モデルの英字表記（例: フェーベ → FEBE、トリニティ → TRINITY）"""
    return model_names(model, brand)[1]


def extract(title: str, brand: str = "", category: str = "") -> str | None:
    """
    商品タイトルから型番 or モデル名を抽出して返す。
    見つからない場合は None。
    category: ジャンル（config.json の category）。ジャンル別の照合がある組み合わせ
    （グッチの時計）はそちらだけで照合する。それ以外は従来どおりブランドだけで照合する。
    """
    special = _CATEGORY_EXTRACTORS.get((brand, category))
    if special is not None:
        return special(title)
    for pattern in _PATTERNS.get(brand, []):
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            if brand == "レイバン":
                return _normalize_rayban(match.group(0))
            sep = r'[\s\-]+' if brand in _STRIP_HYPHEN_BRANDS else r'\s+'
            return re.sub(sep, '', match.group(0)).upper()

    # 型番が無ければモデル名で照合（型番が取れた商品は型番の相場を優先する）
    return extract_name(title, brand)


def normalize(model: str) -> str:
    """型番を正規化（比較用）"""
    return re.sub(r'[\s\-_]', '', model).upper()
