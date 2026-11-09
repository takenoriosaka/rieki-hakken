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
        # 2026-11 追加: 先頭の 0 を英字の O と書く表記（ORB4259F）、ブランド名に続けて書く表記（Ray-BanRB4391D）、
        # 末尾の版違いをハイフンで区切る表記（RB2140-F・RB4258-F）、新しい型番（RB0840S = 0 + 3桁 + S）。
        # RB5xxx・RB6xxx・RB7xxx はメガネ（光学フレーム。RX5154 等）の型番なので拾わない（RX・RJ=キッズ も拾わない）
        r'(?:(?<![A-Za-z0-9])[0O]?|(?<=BAN))RB[\s\-]?(?![5-7]\d{3})\d{4}'
        r'(?:[A-Z](?![A-Za-z])|-[A-Z](?![A-Za-z0-9]))?(?![A-Za-z])',
        # ボシュロム（B&L）期のヴィンテージ: W0365・W1661（レンズ内側・テンプルの刻印。W + 4桁）。2026-11 追加
        r'(?<![A-Za-z0-9])W\d{4}(?![A-Za-z0-9])',
        # 「RB」が付かない 2140 / 2140F は、代表的な型番（_RAYBAN_MODELS）に限って拾う
        # （年・価格・サイズ等の4桁の数字を型番と誤認しないため。パターンは下で追加する）
    ],
    "グッチ": [
        # GG0061S。ジャンルが「サングラス」のときは _extract_gucci_sunglasses、「時計」のときは
        # _extract_gucci_watch で照合する（_CATEGORY_EXTRACTORS）。これはジャンル指定なしのときだけ使う
        r'\bGG\s?\d{4}[A-Z]?\b',
    ],
    "セリーヌ": [
        # CL40046U。ジャンルが「サングラス」のときは _extract_celine_sunglasses で照合する（_CATEGORY_EXTRACTORS）
        r'\bCL\s?\d{5}[A-Z]?\b',
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
        # 品番は 数字4桁 + 性別（M=メンズ・L=レディース）。前後に付く記号:
        #   J=日本向け（3438JM・2302JL。3438MJ と後ろに書く出品もある）、B=ブラックレーベル（3804MB・3481JMB）、
        #   A・I=フィット等の版違い（3426MA・2459MBA・3037JLI）、末尾の R=レギュラー丈（4565MR・7950JMR・「3433JM R」）
        # 例: 2409L, 3426M, 3438JM, 2079JL, 3804MB, 3481JMB, 3802LA, 3804JLAR。小文字（3802la）も同じ品番。
        # 変更前は \b\d{4}[A-Z]\b で、2文字以上の記号（JM・MB 等）と、日本語に続けて書いた品番
        # （「シャトーパーカ2053MB」。\b は日本語を語の一部とみなす）を取りこぼしていた（メルカリ売り切れ 2026-10 確認）。
        # 誤認しないもの: 価格・年（¥2580・2,580・2024AW・2025FW）、サイズ（「9061 M」= 空白の後の M/L）、
        # キッズ（6948K）、帽子等の A だけの記号（1885A）。空白・ハイフン区切りは J 付き（3438 JM）のときだけ許す
        # 末尾 W はレディースの新しい表記（3595W=メイフィールド。写真のタグ・説明文で確認 2026-11）。
        # 「2025W」のような年 + W は品番としない
        r'(?<![A-Za-z0-9.,¥￥])[1-9]\d{3}(?:[\s\-]?J[ML]|[ML]J?)B?J?[AI]?R?(?![A-Za-z0-9])',
        r'(?<![A-Za-z0-9.,¥￥])(?!(?:19|20)\d{2}W)[1-9]\d{3}W(?![A-Za-z0-9])',
    ],
    "ピレネックス": [
        # HMS011, HWY012 (公式サイトで確認済み)。メルカリの表記ゆれ: hms011 / HMS 011 / HMS-011 /
        # HMI055P4004T2（品番の後ろに色・サイズコードが続く）→ HMS011 / HMI055
        r'(?<![A-Za-z0-9])H[A-Z]{2}[\s\-]?\d{3}(?!\d)',
    ],
    "タトラス": [
        # 品番は L(レディース)/M(メンズ) + 系統(TA・TAT・TK・TKE・TLA・TSE) + 年2桁 + A(秋冬)/S(春夏) + 品番3〜4桁
        # （5桁は誤記と思われるが、変更前の正規表現どおり拾う）。
        # 例: LTA13A4301, MTA15S4468, MTK20A4201, MTK18A453, MTAT21A4754-D, LTKE21A4164-D, MTLA21A4140-D。
        # 別注品・旧品番は 年1桁 + 店コード2文字（LTA2BM4694=BEAMS, LTA6RB4349=ROSE BUD, MTA7TM499=TOMORROWLAND）。
        # メルカリの表記ゆれ: lta13a4301 / LTA-13A-4301 / LTA14A 4571 / 末尾の -D・-M・-L（種別コード）
        # → _normalize_tatras で LTA13A4301 の形に整える（メルカリ売却済み 2026-10 確認）
        # 直前の数字は許す（「2020LTA20A4794-D」のように年と続けて書く出品があるため）
        # 2020年頃からの別注・ライン違いは 系統2文字の部分に店コード等が入る（MTNU20A4199-D=ナノユニバース別注、
        # MTOU22A4893-D、LTNO20A4652-D。トレファクの説明文の型番欄・写真のタグで確認 2026-11）。
        # 任意の2文字は 年2桁 + A/S の形のときだけ許す（誤認を避けるため）
        r'(?<![A-Za-z])[LM]T(?:(?:AT|KE|LA|SE|A|K)[\s\-]?(?:\d{2}[AS]|\d[A-Z]{2})|[A-Z]{2}[\s\-]?\d{2}[AS])[\s\-]?\d{3,5}(?!\d)',
        # 2012年頃までの旧品番: MTA-460-12 / LTA-4173-12（品番 + 年2桁。ハイフン区切りのまま型番とする）
        r'(?<![A-Za-z])[LM]TA[\s\-]\d{3,4}[\s\-]\d{2}(?!\d)',
        # 年の付かない旧品番: LTA-4192 / MTA-439 / LTA-4229L（末尾の L・M・D は種別コードとして品番に含めない）。
        # ハイフン区切りのときだけ（写真のタグ・トレファクの説明文で確認 2026-11）
        r'(?<![A-Za-z])[LM]TA\-\d{3,4}(?=[LMD]?(?![A-Za-z0-9\-]))',
    ],
    "ノースフェイス": [
        # ND/NDW=ダウン、NY/NYW・NP/NPW=シェル系。メルカリの表記ゆれ: ND91950 / nd91950 /
        # ND 91950 / ND-91950 / ND92438R・NP62130Z（末尾1文字は版違いのため除外して照合）
        # キッズ(NDJ)は exclude_words で除外済みなので対象外
        r'(?<![A-Za-z0-9])N(?:DW|YW|PW|D|Y|P)[\s\-]?\d{5}(?=[A-Z]?(?![A-Za-z0-9]))',
    ],
    "モンクレール": [
        # 品番（タグの表記）= [シーズン][1/2][ライン3桁][スタイル番号7桁] + 素材コード5桁。_normalize_moncler で整える。
        #   シーズン: 2015年頃まで数字1〜2桁（132-091-40366-05 / 420934931949）、以降は英字（A〜L。D20914036605）
        #   1/2: 春夏/秋冬。ライン: 091=メンズ、093=レディース、092=ジーニアス、097=グルノーブル、
        #        09U・09S 等=コラボ、391=ガムブルー、95x=キッズ（キッズは品番として扱わない）
        #   スタイル番号: 2019年頃まで 数字5桁 + 2桁（4036605=マヤ、4033805=モンジュネーブル）、
        #        以降は 1A53600 のような 数字 + 英字 + 5桁（1A=ダウン等のアウター、9B=ニット切替 等）
        #   素材コード: 68950・54155・595ZZ・C0250 等（品番の照合には使わない。カラーの 999・742、サイズの 0〜5 も使わない）
        # 例: 132-091-40366-05 / D20914036605 68950 / E2091419078568950 / 4036605 68950 / 41303 05 68950 /
        #     49947-80-54155 / H20911A00001 595ZZ / I20931A00046597R1 / K2 093 1A525 00（メルカリ売り切れ・
        #     ベクトルパーク・ヤフオク・ラクマ・トレファクの出品 2026-10 確認）
        # 誤認しないもの: 価格（¥45,381）・電話番号（090-1234-5678）・JAN（13桁）・年（2022-23）・
        # 管理番号（D6047・240066188873・14831332FPAF）。ライン無しの品番は素材コードと区切って書かれたもの
        # （4036605 68950・45381 50 68950）だけ拾う
        r'(?<![A-Z0-9])(?:(?P<season>[A-M]|\d{1,2})(?P<half>[12])[\s\-]?(?P<line>09[0-9A-Z]|95\d|391)[\s\-]?)?'
        r'(?P<style>\d[A-Z][0-9A-Z]{3}[\s\-]?[0-9A-Z]{2}|\d{5}[\s\-]?\d{2})'
        r'(?:(?P<fsep>[\s\-]?)(?P<fabric>[0-9A-Z]{4,6}))?(?![A-Z0-9])',
    ],
}

# 抽出結果からハイフンも取り除くブランド（ND-91950 → ND91950）
_STRIP_HYPHEN_BRANDS = {"ノースフェイス", "ピレネックス"}
# 型番の照合前に全角英数字を整える（NFKC）ブランド（ＬＴＡ１３Ａ４３０１ 等の全角表記も拾う）
_NFKC_NUMBER_BRANDS = {"タトラス", "カナダグース", "モンクレール"}

# ── レイバン: 仕入れ対象の代表型番と愛称（メルカリ売却済みで相場が付くもの。2026-10 確認）──
# 画面の「型番の候補」に「RB2140 ウェイファーラー」のように愛称付きで出す（愛称が無いものは空文字）。
# 型番の末尾1文字（F=アジアンフィット、N=新型 等）は別型番として扱う。
# メルカリ相場で RB2140F ¥11,000 / RB2140 ¥6,000 のように F 付きが高いことが多いため（2026-10 確認）。
_RAYBAN_MODELS: dict[str, str] = {}   # 型番 → 愛称（下の KNOWN_MODEL_NUMBERS の前で設定）


def _normalize_rayban(s: str) -> str:
    """レイバン型番の表記ゆれを整える: rb-2140 / 0RB2140 / RB 2140 → RB2140、2140F / rb2140f → RB2140F"""
    u = re.sub(r'[\s\-]', '', s.upper())
    if u.startswith("W"):
        return u                      # ボシュロム期の W0365 等はそのまま
    m = re.search(r'(\d{4})([A-Z])?', u)
    if not m:
        return s.upper()
    return f"RB{m.group(1)}{m.group(2) or ''}"


def _normalize_tatras(s: str) -> str:
    """タトラス品番の表記ゆれを整える: lta-13a-4301 / LTA14A 4571 → LTA13A4301 / LTA14A4571、
    旧品番 mta 460 12 → MTA-460-12（末尾の種別コード -D 等は正規表現の一致範囲に含めない）"""
    m = re.fullmatch(r'([LM]TA)[\s\-](\d{3,4})[\s\-](\d{2})', s.upper())
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.fullmatch(r'([LM]TA)\-(\d{3,4})', s.upper())
    if m:
        return f"{m.group(1)}-{m.group(2)}"   # 年の付かない旧品番（LTA-4192）
    return re.sub(r'[\s\-]+', '', s).upper()


def _normalize_canadagoose(s: str) -> str:
    """カナダグース品番の表記ゆれを整える: 3438mj / 3438 JM → 3438JM、3438MBJ → 3438JMB、
    3804JLAR / 4565MR → 3804JLA / 4565M（丈の R は品番に含めない）。並びは 数字 + J + 性別 + B + A/I"""
    s = re.sub(r'[\s\-]+', '', s).upper()
    num, tail = s[:4], s[4:]
    if tail.endswith("R"):
        tail = tail[:-1]
    gender = "M" if "M" in tail else ("W" if tail == "W" else "L")
    return (num + ("J" if "J" in tail else "") + gender + ("B" if "B" in tail else "")
            + "".join(c for c in tail if c in "AI"))


_MONCLER_KIDS = "__KIDS__"   # キッズの品番（ライン 95x）。_normalize_moncler の戻り値


def _normalize_moncler(m: re.Match) -> str | None:
    """モンクレール品番を「同じモデル」として相場を取れる形に整える。
    - スタイル番号がシーズンをまたいで同じもの（2019年頃までの 4036605、1A53600 のような番号）は
      スタイル番号7桁だけにする（D20914036605 / 132-091-40366-05 / 4036605 68950 → 4036605。
      マヤは2013年の 132-091-40366-05 も2018年の D20914036605 も 4036605。ガーニーは H2・K2 とも 1A52500）
    - 2020年頃からの連番（1A00001・1A00277 等。3〜4桁目が 00）はシーズンごとに別のモデルに振り直されるため
      （トレオンパンは H2 1A00277・I2 1A00170・J2 1A00107、1A00001 はウォラストン・アカンサス等）、
      シーズン・ライン付きの12桁（I20911A00001）にする。シーズンが書かれていない連番は品番として扱わない
    - キッズ（ライン 95x）は _MONCLER_KIDS。ライン無し・素材コードと区切らずに続けた数字列は扱わない（管理番号等）"""
    line = m.group("line")
    style = re.sub(r'[\s\-]', '', m.group("style")).upper()
    if not line and not (m.group("fabric") and m.group("fsep")):
        return None
    if not re.fullmatch(r'\d{7}|\d[A-Z][0-9A-Z]{5}', style):
        return None
    if line and line.startswith("95"):
        return _MONCLER_KIDS
    if re.fullmatch(r'\d[A-Z]00\d{3}', style):
        if not line:
            return None
        return (m.group("season") + m.group("half") + line + style).upper()
    return style


def _moncler_number(title: str) -> str | None:
    """タイトルの最初のモンクレール品番（_normalize_moncler の形）。キッズの品番なら _MONCLER_KIDS"""
    t = unicodedata.normalize("NFKC", title or "").upper()
    for match in re.finditer(_PATTERNS["モンクレール"][0], t):
        num = _normalize_moncler(match)
        if num:
            return num
    return None


# ── モデル名（コレクション名）で照合するブランド ────────────────────────
# 型番が社内コード化されておらず、出品タイトルにはコレクション名で
# 書かれることがほとんどのブランド（カルティエは時計と別軸でアクセサリーも持つ）
_MODEL_NAMES: dict[str, list[str]] = {
    # 並びは「日本語名(正規名), 英字名」のペア。画面のチェックボックスは日本語名から作り、
    # 仕入れ先検索・メルカリ相場は日本語名と英字名の両方で行う（model_names() 参照）。
    # 照合は並び順の優先度で行う（派生・限定ラインを先に）。
    # モンクレールは厳密照合（_MONCLER_MODELS）。ここには下のループで正規名・英字名が入る
    # （変更前の マヤ・バディ・フラグメント・モンジュネーブルはそのまま残る。ベイカー/BAKER はメルカリ売り切れで
    # 0件のため外した）
    "モンクレール": [],
    "カルティエ": [
        "ラブブレス", "LOVE BRACELET", "ラブリング", "LOVE RING",
        "ジュストアンクル", "JUSTE UN CLOU",
        "トリニティ", "TRINITY", "パンテール", "PANTHERE", "クラッシュ", "CRASH",
    ],
    # ティファニーは厳密照合（_TIFFANY_MODELS）。ここには下のループで正規名・英字名が入る
    "ティファニー": [],
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
    # ── サングラス（2026-11 追加。メルカリ売り切れのタイトル・説明文・写真のOCRで2件以上あったモデル名）──
    # ディオールは型番（0049S・CD2289）が取れない出品をモデル名で照合する（_extract_dior_sunglasses）。
    # 派生モデルを先に（ソーリアルポップ → ソーリアル）
    "クリスチャンディオール": [
        "ソーリアルポップ", "SO REAL POP",
        "ソーリアル", "SO REAL",
        "ソーステレール", "SO STELLAIRE",
        "グロッシー1", "GLOSSY1",
        "30モンテーニュ", "30MONTAIGNE",
        "ミニポップ", "MINIPOP",
        "ヴォリュート", "VOLUTE",
        "アヴェンチュラ", "AVENTURA",
        "ベリーディオール", "VERY DIOR",
        "アディオラブル", "ADIORABLE",
    ],
    # オークリーはタイトルの多くがモデル名だけ（型番 OO9102 が載るのは2割弱）。型番が無い出品をモデル名で照合する
    # （_extract_oakley_sunglasses）。派生・似た名前を先に（レーダーロック・レーダーEV → レーダー、
    # ハーフジャケット・ストレートジャケット・アイジャケット・フラックジャケット）
    "オークリー": [
        "ホルブルック", "HOLBROOK",
        "フロッグスキン", "FROGSKINS",
        "ジョウブレイカー", "JAWBREAKER",
        "ジョウボーン", "JAWBONE",
        "レーダーロック", "RADARLOCK",
        "レーダーEV", "RADAR EV",
        "レーダー", "RADAR",
        "フラック2.0", "FLAK 2.0",
        "フラックジャケット", "FLAK JACKET",
        "ハーフジャケット", "HALF JACKET",
        "ストレートジャケット", "STRAIGHT JACKET",
        "アイジャケット", "EYE JACKET",
        "スートロ", "SUTRO",
        "ガスカン", "GASCAN",
        "ジュリエット", "JULIET",
        "ロメオ", "ROMEO",
        "Mフレーム", "M FRAME",
        "モンスタードッグ", "MONSTER DOG",
        "ミニット", "MINUTE",
        "ストレートリンク", "STRAIGHT LINK",
        "ラッチ", "LATCH",
        "EVゼロ", "EVZERO",
        "アクチュエーター", "ACTUATOR",
        "スタントデビル", "STUNT DEVIL",
    ],
    # EYEVAN（アイヴァン）の通常ラインはモデル名だけ（型番は無い。PBK・TORT・SMK 等はカラー）。2026-11 メルカリ売り切れで
    # 2件以上売れていた名前（Webb 23件 等）。EYEVAN 7285 は3桁の番号（319 / 770 等）で照合する（_extract_eyevan_sunglasses）
    "EYEVAN": [
        "ウェッブ", "WEBB",
        "サドラー", "SADLER",
        "ルビン", "LUBIN",
        "バイロン", "BYRON",
        "コールドウェル", "CALDWELL",
        "ハンク", "HANK",
        "ルーベン", "RUBEN",
        "フリッカー", "FLICKER",
        "コナー", "CONNER",
        "メイソン", "MASON",
        "ハンザ", "HANSA",
        "アズテック", "AZTEC",
    ],
    # ayame（アヤメ）はモデル名だけ（型番は無い。LAYS-DHV の DHV 等はカラー）。TORO・KORO は「レトロ」「コロナ」等に
    # 部分一致しないよう英字だけ。HEX は形の名前（ヘキサゴン）と紛らわしいため入れない
    "ayame": [
        "レイズ", "LAYS",
        "ボストンプロ", "BOSTON PRO",
        "タンクプロ", "TANK PRO",
        "ニューオールド", "NEWOLD",
        "オールドスター", "OLDSTAR",
        "フォーカス", "FOCUS",
        "TORO",
        "KORO",
        "クラブオー", "CLUB-O",
        "マンティス", "MANTIS",
        "スパイク", "SPIKE",
        "シッポウ", "SIPPOU",
        "アドミラル", "ADMIRAL",
        "ジェネラル", "GENERAL",
        "マンレイ", "MANRAY",
    ],
    # リックオウエンスはモデル名だけ（型番はタイトル・タグにほぼ出ない）。「シールド」は形の名前としても使われるため最後に
    "リックオウエンス": [
        "クリースター", "KRIESTER",
        "ドキュメンタ", "DOCUMENTA",
        "パフォーマ", "PERFORMA",
        "フォグ", "FOG",
        "GETH",                  # 「ゲス」は別ブランド（GUESS）と紛らわしいため英字だけ
        "シールディング", "SHIELDING",
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
    "ルビン": ["ルービン"],          # EYEVAN Lubin
    "パンテール": ["PANTHÈRE"],
    "モンジュネーブル": ["モンジュネーヴル"],
    "ビーゼロワン": ["BZERO1", "ビーゼロ1"],
    "ディーバ": ["ディーヴァ"],
    "ニューウェイファーラー": ["ニューウェイファラー"],
    "フォールディングウェイファーラー": ["フォールディングウェイファラー"],
    "ウェイファーラー": ["ウェイファラー", "ウェーファーラー"],
    "アビエーター": ["アビエイター", "アヴィエーター"],
    # サングラス（2026-11 追加）
    "ソーリアル": ["ソウリアル", "ソーリアル"],
    "ソーステレール": ["ソーステラー", "ソーステライル"],
    "グロッシー1": ["グロッシー"],
    "30モンテーニュ": ["サーティモンテーニュ", "モンテーニュ30"],
    "ヴォリュート": ["ボリュート"],
    "アヴェンチュラ": ["アベンチュラ"],
    "パフォーマ": ["パフォーマー"],
    "シッポウ": ["SIPPPOU", "七宝"],
    "ホルブルック": ["HOLBLOOK", "ホルブロック"],
    "フロッグスキン": ["FROGSKIN", "FLOGSKIN", "フロッグスキンズ", "フロッグスキンライト"],
    "ジョウブレイカー": ["ジョーブレイカー", "ジョウブレーカー"],
    "ジョウボーン": ["ジョーボーン"],
    "レーダーロック": ["レーダーロックパス"],
    "フラック2.0": ["フラック20", "FLAK20XL"],
    "モンスタードッグ": ["MONSTAR DOG", "モンスタードック"],
    "EVゼロ": ["EV ZERO", "イーブイゼロ"],
    "シールディング": ["シールド", "SHIELD"],
    # ティファニー（画面・型番欄に入力された表記を正規名に寄せる。照合自体は _TIFFANY_MODELS）
    "リターントゥティファニー": ["RTT", "リターントゥ"],
    "Tワン": ["T1", "ティーワン"],
    "Tスマイル": ["ティースマイル"],
    "ハードウェア": ["ハードウエア"],
    "ビクトリア": ["ヴィクトリア"],
    "ソリテール": ["ソリティア"],
    "ラブノット": ["フックアンドアイ", "フック&アイ"],
    "ビーン": ["ビーンズ"],
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
    "ヘルノ": [
        # 型番（PI064UL 等）はメルカリ売却済みのタイトルにほとんど載らない（6%）ためモデル名で照合する。
        # 対象はラミナーのダウンコートだけ（コートの種別条件は _TITLE_RULES）。
        # 表記ゆれ: ラミナー / ラミナ / ラミーナ / LAMINAR（「ラミナリア」等の別語は除く）
        ("ラミナー",             [r'ラミー?ナ(?:ー|(?![ァ-ヶ]))', _A + r'LAMINAR' + _Z]),
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
    "ラミナー": "LAMINAR",
}


# ── ルイ・ヴィトンのバッグ: モデル名（型番 M/N+5桁が無い出品だけモデル名で照合する）──────
# サイズで相場が大きく変わるモデル（スピーディ25/30/35、ネヴァーフルPM/MM/GM 等）は
# サイズ別に別モデルとして判定する（デュベティカのドゥエ/セイと同じ考え方）。
#   require_size=True : サイズが書かれていない出品はモデル名では照合しない（型番か説明文で照合する）
#   require_size=False: サイズが無ければサイズなしのモデル名（例: トロカデロ）として扱う
# バンドリエール（ショルダー付き）はタイトルのどこかに「バンドリエール」があれば別モデルにする。
# 照合はジャンルが「バッグ」のときだけ（_extract_lv_bag。サングラスの照合には使わない）
_LV_SEP = r'[\s・･\-_/]*'
_LV_BAND = r'(?:バンドリエ[ー\-]?ル|BANDOULI[EÈ]RE)'
_LV_HAS_BAND = r'(?s)^(?=.*' + _LV_BAND + r').*?'      # タイトルのどこかにバンドリエール
_LV_NO_BAND = r'(?s)^(?!.*' + _LV_BAND + r').*?'       # バンドリエールなし
_LV_MINI = ("ミニ", "MINI", r'(?:ミニ|MINI(?![A-Za-z]))')


def _lv_size(label: str) -> tuple[str, str, str]:
    """サイズ表記（25 / PM / MM 等）→ (カタカナ側の表記, 英字側の表記, 正規表現)"""
    if label.isdigit():
        return label, label, r'(?<!\d)' + label + r'(?!\d)'      # 30 / 30cm（300 等は除く）
    return label, label, label + r'(?![A-Za-z])'                  # PM / MM（PMS 等は除く）


def _lv_sized(canon: str, en: str, kana: str, eng: str, sizes: list, require_size: bool,
              cond: str = "", band: bool = False) -> list[tuple[str, str, list[str]]]:
    """サイズ別のモデル（スピーディ25 等）と、必要ならサイズなしのモデルを作る。
    kana / eng: モデル名の正規表現（英字は前に英字が続かないこと _A を付けて使う）。
    band: サイズの前に「バンドリエール」が入る表記（スピーディ・バンドリエール・25）も許す"""
    mid = _LV_SEP + (r'(?:' + _LV_BAND + _LV_SEP + r')?' if band else "")
    out = []
    for s in sizes:
        k_label, e_label, sre = _lv_size(s) if isinstance(s, str) else s
        pats = [cond + r'(?:' + kana + r')' + mid + r'(?:' + sre + r')',
                cond + _A + r'(?:' + eng + r')' + mid + r'(?:' + sre + r')']
        if k_label == "ミニ":    # 「ミニ・パームスプリングス」のようにサイズが前に来る表記
            pats += [cond + r'(?:' + sre + r')' + _LV_SEP + r'(?:' + kana + r')',
                     cond + _A + r'(?:' + sre + r')' + _LV_SEP + r'(?:' + eng + r')' + _Z]
        out.append((canon + k_label, f"{en} {e_label}", pats))
    if not require_size:
        out.append((canon, en, [cond + r'(?:' + kana + r')', cond + _A + r'(?:' + eng + r')' + _Z]))
    return out


def _lv(canon: str, en: str, kana: str, eng: str) -> list[tuple[str, str, list[str]]]:
    """サイズ違いを分けないモデル"""
    return [(canon, en, [kana, _A + r'(?:' + eng + r')' + _Z])]


_LV_ACC = r'アクセ(?:ソワール|ソアール|ソワ|サリー)'
_LV_BAG_MODELS: list[tuple[str, str, list[str]]] = [
    # スピーディ: ナノ → バンドリエール（サイズ別）→ 通常（サイズ別）の順
    *_lv(        "ナノスピーディ", "NANO SPEEDY", r'ナノ' + _LV_SEP + r'スピーディー?', r'NANO\s*SPEEDY'),
    *_lv(        "ミニスピーディ", "MINI SPEEDY", r'ミニ' + _LV_SEP + r'スピーディー?', r'MINI\s*SPEEDY'),
    *_lv_sized("スピーディバンドリエール", "SPEEDY BANDOULIERE", r'スピーディー?', r'SPEEDY',
               ["20", "25", "30", "35", "40"], require_size=True, cond=_LV_HAS_BAND, band=True),
    *_lv_sized("スピーディ", "SPEEDY", r'スピーディー?', r'SPEEDY',
               ["25", "30", "35", "40"], require_size=True, cond=_LV_NO_BAND),
    *_lv_sized("ネヴァーフル", "NEVERFULL", r'ネ(?:ヴァ|バ|ヴ)ー?フル', r'NEVER\s*FULL',
               ["BB", "PM", "MM", "GM"], require_size=True),
    *_lv(        "ナノアルマ", "NANO ALMA", r'ナノ' + _LV_SEP + r'アルマ', r'NANO\s*ALMA'),
    *_lv_sized("アルマ", "ALMA", r'(?<![ァ-ヶー])アルマ(?!ー?ニ)', r'ALMA',
               ["BB", "PM", "MM", "GM"], require_size=True),
    # ミュルティ・ポシェット → ミニ・ポシェット → ポシェット・アクセソワール の順（部分文字列を含むため）
    *_lv("ミュルティポシェットアクセソワール", "MULTI POCHETTE ACCESSOIRES",
         r'(?:ミュルティー?|マルチ)' + _LV_SEP + r'ポシェット', r'MULTI\s*-?\s*POCHETTE'),
    *_lv("ミニポシェットアクセソワール", "MINI POCHETTE ACCESSOIRES",
         r'ミニ' + _LV_SEP + r'ポシェット' + _LV_SEP + _LV_ACC, r'MINI\s*POCHETTE\s*ACCESSOIRES?'),
    *_lv("ポシェットアクセソワール", "POCHETTE ACCESSOIRES",
         r'ポシェット' + _LV_SEP + _LV_ACC + r'|(?<![ァ-ヶー])アクセソワール', r'POCHETTE\s*ACCESSOIRES?'),
    *_lv("ポシェットメティス", "POCHETTE METIS", r'ポシェット' + _LV_SEP + r'メティス|(?<![ァ-ヶー])メティス', r'(?:POCHETTE\s*)?M[EÉ]TIS'),
    *_lv("ポシェットフェリシー", "POCHETTE FELICIE", r'フェリシー', r'F[EÉ]LICIE'),
    *_lv_sized("オンザゴー", "ON THE GO", r'オン' + _LV_SEP + r'ザ' + _LV_SEP + r'ゴー?',
               r'ON\s*-?\s*THE\s*-?\s*GO|ONTHEGO', ["PM", "MM", "GM"], require_size=True),
    *_lv_sized("キーポルバンドリエール", "KEEPALL BANDOULIERE", r'キーポー?ル', r'KEEPALL',
               ["45", "50", "55", "60"], require_size=True, cond=_LV_HAS_BAND, band=True),
    *_lv_sized("キーポル", "KEEPALL", r'キーポー?ル', r'KEEPALL',
               ["45", "50", "55", "60"], require_size=True, cond=_LV_NO_BAND),
    *_lv_sized("ドーフィーヌ", "DAUPHINE", r'ドーフィーヌ', r'DAUPHINE', [_LV_MINI, "MM"], require_size=False),
    *_lv_sized("カプシーヌ", "CAPUCINES", r'カプシーヌ', r'CAPUCINES?',
               [_LV_MINI, "BB", "PM", "MM", "GM"], require_size=True),
    *_lv_sized("ツイスト", "TWIST", r'(?<![ァ-ヶー])ツイスト(?!' + _LV_SEP + r'ロック)', r'TWIST',
               [_LV_MINI, "PM", "MM"], require_size=False),
    # プチノエ・ネオノエ・ノエBB は通常のノエ（ノエGM）と相場が違うので別モデル
    *_lv("ナノノエ", "NANO NOE", r'ナノ' + _LV_SEP + r'ノエ', r'NANO\s*-?\s*NO[EÉ]'),
    *_lv("プチノエ", "PETIT NOE", r'プチ' + _LV_SEP + r'ノエ', r'PETIT\s*-?\s*NO[EÉ]'),
    *_lv("ネオノエ", "NEO NOE", r'ネオ' + _LV_SEP + r'ノエ', r'NEO\s*-?\s*NO[EÉ]'),
    *_lv_sized("ノエ", "NOE", r'(?<![ァ-ヶー])ノエ(?![ァ-ヶー])', r'NO[EÉ]', ["BB"], require_size=False),
    *_lv_sized("バティニョール", "BATIGNOLLES", r'バティニョール', r'BATIGNOLLES?',
               [("オリゾンタル", "HORIZONTAL", r'オリゾンタル|HORIZONTAL(?![A-Za-z])'),
                ("ヴェルティカル", "VERTICAL", r'(?:ヴェ|ベ)ルティカル|VERTICAL(?![A-Za-z])')], require_size=False),
    *_lv_sized("パームスプリングス", "PALM SPRINGS", r'パーム' + _LV_SEP + r'スプリングス?',
               r'PALM\s*SPRINGS?', [_LV_MINI, "PM", "MM"], require_size=True),
    *_lv_sized("モンスリ", "MONTSOURIS", r'モンスリ', r'MONTSOURIS',
               [_LV_MINI, "BB", "PM", "MM", "GM"], require_size=False),
    *_lv("ブローニュ", "BOULOGNE", r'ブローニュ', r'BOULOGNE'),
    *_lv("トロカデロ", "TROCADERO", r'トロカデロ', r'TROCADERO'),
    *_lv("エヴァ", "EVA", r'(?<![ァ-ヶー])エヴァ(?![ァ-ヶー])', r'EVA'),
    *_lv_sized("ルーピング", "LOOPING", r'ルーピング', r'LOOPING', ["PM", "MM", "GM"], require_size=False),
    *_lv("ディアヌ", "DIANE", r'ディアヌ', r'DIANE'),
    *_lv_sized("カルトシエール", "CARTOUCHIERE", r'カルトシエール', r'CARTOUCHI[EÈ]RE',
               ["PM", "MM", "GM"], require_size=False),
    *_lv("マレル", "MARELLE", r'(?<![ァ-ヶー])マレル', r'MARELLE'),
    *_lv("ソミュール", "SAUMUR", r'ソミュール', r'SAUMUR'),
    *_lv("ミュゼットタンゴ", "MUSETTE TANGO", r'ミュゼット' + _LV_SEP + r'タンゴ', r'MUSETTE\s*TANGO'),
    *_lv("ミュゼット", "MUSETTE", r'ミュゼット', r'MUSETTE'),
    *_lv("サックプラ", "SAC PLAT", r'サック' + _LV_SEP + r'プラ(?![ァ-ヶー])', r'SAC\s*PLAT'),
    *_lv("ポパンクール", "POPINCOURT", r'ポパンクール', r'POPINCOURT'),
    # ── 2026-10 追加: 定番のショルダー・トート・ポシェット・メンズ ──
    *_lv("カバメゾ", "CABAS MEZZO", r'カバ' + _LV_SEP + r'メゾ', r'CABAS\s*MEZZO'),
    *_lv("カバピアノ", "CABAS PIANO", r'カバ' + _LV_SEP + r'ピアノ', r'CABAS\s*PIANO'),
    *_lv_sized("バケット", "BUCKET", r'バケット(?!' + _LV_SEP + r'ハット)', r'BUCKET(?!\s*HAT)',
               ["PM", "GM"], require_size=True),     # 「バケット」だけだと宝石のバケットカット等と紛れる
    *_lv("ルコ", "LUCO", r'(?<![ァ-ヶー])ルコ(?![ァ-ヶー])', r'LUCO'),
    *_lv("ダヌーブ", "DANUBE", r'ダヌーブ', r'DANUBE'),
    *_lv("ナイル", "NIL", r'(?<![ァ-ヶー])ナイル(?![ァ-ヶー])', r'NILE?'),
    *_lv_sized("リポーター", "REPORTER", r'[リレ]ポーター', r'REPORTER', ["PM", "GM"], require_size=False),
    *_lv("ソローニュ", "SOLOGNE", r'ソローニュ', r'SOLOGNE'),
    *_lv("ブロワ", "BLOIS", r'(?<![ァ-ヶー])ブロワ(?![ァ-ヶー])', r'BLOIS'),
    *_lv_sized("ジュヌフィーユ", "JEUNE FILLE", r'ジュヌ' + _LV_SEP + r'フィーユ', r'JEUNE\s*FILLE',
               ["PM", "MM", "GM"], require_size=False),
    *_lv_sized("シャンティ", "CHANTILLY", r'シャンティイ?', r'CHANTILLY', ["PM", "MM", "GM"], require_size=False),
    *_lv("ドルーオ", "DROUOT", r'ドルーオ', r'DROUOT'),
    *_lv_sized("ポシェットツイン", "POCHETTE TWIN", r'ポシェット' + _LV_SEP + r'ツイン', r'POCHETTE\s*TWIN',
               ["PM", "GM"], require_size=False),
    *_lv("ポシェットフロランティーヌ", "POCHETTE FLORENTINE",
         r'(?:ポシェット' + _LV_SEP + r')?フロランティーヌ', r'(?:POCHETTE\s*)?FLORENTINE'),
    *_lv("ポシェットガンジュ", "POCHETTE GANGE", r'(?:ポシェット' + _LV_SEP + r')?ガンジュ', r'(?:POCHETTE\s*)?GANGE'),
    *_lv_sized("マルリードラゴンヌ", "MARLY DRAGONNE", r'マルリー' + _LV_SEP + r'ドラゴンヌ', r'MARLY\s*DRAGONNE',
               ["PM", "GM"], require_size=False),
    *_lv("ポシェットマルリーバンドリエール", "POCHETTE MARLY BANDOULIERE",
         r'ポシェット' + _LV_SEP + r'マルリー', r'POCHETTE\s*MARLY'),
    *_lv_sized("コンピエーニュ", "COMPIEGNE", r'コンピエーニュ', r'COMPI[EÈ]GNE', ["23", "28"], require_size=False),
    *_lv("モンソー", "MONCEAU", r'モンソー', r'MONCEAU'),
    *_lv("サックショッピング", "SAC SHOPPING", r'サック' + _LV_SEP + r'ショッピング', r'SAC\s*SHOPPING'),
    *_lv("サックシャス", "SAC CHASSE", r'サック' + _LV_SEP + r'シャス', r'SAC\s*CHASSE'),
    *_lv_sized("ヴァヴァン", "VAVIN", r'(?:ヴァ|バ)(?:ヴァ|バ)ン', r'VAVIN', ["PM", "GM"], require_size=False),
    *_lv("ドーヴィル", "DEAUVILLE", r'ドー(?:ヴィ|ビ)ル', r'DEAUVILLE'),
    *_lv_sized("エリプス", "ELLIPSE", r'エリプス', r'ELLIPSE', ["PM", "MM"], require_size=False),
    *_lv_sized("トータリー", "TOTALLY", r'トータリー', r'TOTALLY', ["PM", "MM", "GM"], require_size=False),
    *_lv_sized("アーツィー", "ARTSY", r'アーツィー?', r'ARTSY', ["MM", "GM"], require_size=False),
    *_lv_sized("ガリエラ", "GALLIERA", r'ガリエラ', r'GALLIERA', ["PM", "GM"], require_size=False),
    *_lv_sized("パレルモ", "PALERMO", r'パレルモ', r'PALERMO', ["PM", "GM"], require_size=False),
    *_lv_sized("オデオン", "ODEON", r'オデオン', r'OD[EÉ]ON', ["PM", "MM"], require_size=False),
    *_lv("サンジャック", "SAINT JACQUES", r'サン' + _LV_SEP + r'ジャック', r'SAINT\s*-?\s*JACQUES'),
    *_lv("ウィルシャー", "WILSHIRE", r'ウィルシャー', r'WILSHIRE'),
    *_lv("ヒューストン", "HOUSTON", r'ヒューストン', r'HOUSTON'),
    *_lv("スポンティーニ", "SPONTINI", r'スポンティーニ', r'SPONTINI'),
    *_lv_sized("パピヨン", "PAPILLON", r'パピヨン', r'PAPILLON', ["26", "30"], require_size=False),
    *_lv("ポンヌフ", "PONT NEUF", r'ポン' + _LV_SEP + r'ヌフ', r'PONT\s*-?\s*NEUF'),
    *_lv("ロックミー", "LOCKME", r'ロック' + _LV_SEP + r'ミー', r'LOCK\s*-?\s*ME'),
    *_lv("バビロン", "BABYLONE", r'バビロン', r'BABYLONE'),
    # メンズ（ダミエ・グラフィット / タイガ 等）
    *_lv("ナヴィグリオ", "NAVIGLIO", r'ナ(?:ヴィ|ビ)グリオ', r'NAVIGLIO'),
    *_lv("ジェロニモス", "GERONIMOS", r'ジェロニモス?', r'GERONIMOS'),
    *_lv_sized("ディストリクト", "DISTRICT", r'ディストリクト', r'DISTRICT', ["PM", "MM"], require_size=False),
    *_lv_sized("メッセンジャーボスフォール", "MESSENGER BOSPHORE",
               r'(?:メッセンジャー' + _LV_SEP + r')?ボスフォール', r'(?:MESSENGER\s*)?BOSPHORE',
               ["PM", "GM"], require_size=False),
    *_lv_sized("タダオ", "TADAO", r'タダオ', r'TADAO', ["PM", "MM"], require_size=False),
    *_lv("アヴェニュースリングバッグ", "AVENUE SLING BAG",
         r'ア(?:ヴェ|ベ)ニュー' + _LV_SEP + r'スリング', r'AVENUE\s*SLING'),
    *_lv("ソフトトランク", "SOFT TRUNK", r'ソフト' + _LV_SEP + r'トランク', r'SOFT\s*TRUNK'),
    *_lv_sized("シティスティーマー", "CITY STEAMER", r'シティ' + _LV_SEP + r'スティーマー',
               r'CITY\s*STEAMER', [_LV_MINI, "PM", "MM"], require_size=False),
]
_STRICT_MODELS["ルイヴィトン"] = [(name, pats) for name, _en, pats in _LV_BAG_MODELS]
_STRICT_EN.update({name: en for name, en, _pats in _LV_BAG_MODELS})


# ── ティファニー: モデル名（コレクション名）。メルカリの売却済みタイトル約3,500件（ログインなしで取得）から、
# 実際に売れているコレクション名を拾ったもの（2026-10 確認）。各行の件数は、そのうち config.json の
# 「ティファニー ジュエリー」の条件（required_words 等）を満たす約2,900件で、そのモデルと判定された件数。
# 売却済みが1〜2件でも、他と紛れない固有の名前（ドアノッカー・マラケシュ等）は候補に入れている。
# 「ハート」「キー」「クロス」「X」「ナロー」「ロック」だけでは他の商品・別コレクションと紛れるため、
# 前後の語で絞る（キーはキーリング・キーホルダーを除く、クロスは「クロス ネックレス」等の形だけ等）。
# 並びは優先度順（派生・限定ラインを先に）:
#   ハードウェアのボール/マイクロ/スモール/ラージ → ハードウェア（相場が 3万〜16万円と大きく違う）
#   ダブルハートタグ → ハートタグ → リターントゥティファニー
#   1837メイカーズ（ゴールド中心、約10万円）→ 1837（シルバー中心、約1.5万円）
#   ダブルロープ（センターノット、約13万円）→ ラブノット（フック&アイ、約2万円）→ ノット
# 「ハードウエア風」「Tiffany風」「〜似」等の類似品はモデル名として扱わない（_TF_NF / _TF_NOT_LIKE）。
# 売却済みに見当たらない名前（ジッパー・アトラスX 等）と、「ハート」「ナロー」「ハーフエタニティ」
# のような一般的な語だけの呼び方は入れていない。
_TF_NF = r'(?!\s*(?:風|似|タイプ))'                       # 類似品（ハードウエア風 等）を除く
_TF_NOT_LIKE = r'(?s)^(?!.*(?:ティファニー|TIFFANY)\s*(?:風|似))'   # 「Tiffany風 スマイルピアス」等
# シルバー・コンビ・ヴィンテージ等の語（現行のゴールドのノットと、昔のシルバーのノットを分ける）
_TF_VINTAGE = (r'(?:925|' + _A + r'(?:SV|SILVER|VINTAGE|OLD)' + _Z
               + r'|シルバー|コンビ|[ヴビ]ィ?ンテージ|オールド|ケルティック|ツイスト|パロマ)')
_TF_HW = (r'(?s)^(?=.*(?:ハードウ[ェエ]ア|' + _A + r'HARD\s*WEAR' + _Z + r')' + _TF_NF + r').*?')
_TF_RTT = r'(?s)^(?=.*(?:リターン\s*トゥ|' + _A + r'(?:RETURN\s*TO|RTT)' + _Z + r')).*?'
_TF_ITEM = r'(?=\s*(?:十字|ネックレス|ペンダント|ピアス|イヤリング|リング|トップ|チャーム|ブレス|バングル))'


def _tf(canon: str, en: str, kana: str, eng: str, cond: str = "",
        extra: list[str] | None = None) -> list[tuple[str, str, list[str]]]:
    """ティファニーのモデル。kana / eng: 正規表現（英字は前後に英字が続かないこと）。
    cond: タイトル全体の条件（ハードウェアのサブライン等）。extra: 追加の正規表現"""
    pats = [cond + r'(?:' + kana + r')' + _TF_NF,
            cond + _A + r'(?:' + eng + r')' + _Z + _TF_NF] + (extra or [])
    # 類似品（ブランド名の直後に「風」「似」）は除く。条件は先読みなので照合位置は変わらない
    return [(canon, en, [_TF_NOT_LIKE + r'(?=.*?(?:' + p.removeprefix('(?s)') + r'))' for p in pats])]


_TIFFANY_MODELS: list[tuple[str, str, list[str]]] = [
    # ── ティファニー T（売却済み中央値 12万〜23万円）──
    *_tf("Tバー", "T BAR", _A + r'T\s*バー|ティー\s*バー', r'T\s*BAR'),                                 # 19件
    *_tf("Tスクエア", "T SQUARE", _A + r'T\s*スクエア|ティー\s*スクエア', r'T\s*SQUARE'),              # 48件
    *_tf("Tフープ", "T HOOP", _A + r'T\s*フープ', r'T\s*HOOP'),                                         # 6件
    *_tf("Tトゥー", "T TWO", _A + r'T\s*トゥー', r'T\s*TWO'),                                           # 2件
    *_tf("Tチェーン", "T CHAIN", _A + r'T\s*(?:ナロー\s*)?チェーン', r'T\s*(?:NARROW\s*)?CHAIN'),       # 2件
    *_tf("Tワイヤー", "T WIRE",                                                                       # 109件
         _A + r'T\s*(?:ダイヤ(?:モンド)?\s*|ナロー\s*)?ワイヤー|ティー\s*ワイヤー|ナロー\s*ワイヤー',
         r'T\s*WIRE'),
    *_tf("Tワン", "T ONE", _A + r'T\s*ワン(?!ピース|ポイント)|ティー\s*ワン', r'T\s*ONE',                             # 22件
         # 「T1」は後ろにリング・ナロー等が続くときだけ（末尾の管理番号「…SV925 T1」を除く）
         extra=[r'(?<![A-Za-z0-9])T\s*-?\s*1(?!\d)(?=\s*(?:ナロー|ワイド|リング|バングル|フープ|ピアス'
                r'|ネックレス|ブレス|ダイヤ|ペンダント|RING|NARROW|WIDE))', r'^T\s*-?\s*1$']),
    *_tf("Tトゥルー", "T TRUE", _A + r'T\s*トゥルー|(?<![ァ-ヶー])トゥルー\s*(?:ナロー|ワイド|バンド)', r'T\s*TRUE'),                                    # 6件
    *_tf("Tスマイル", "T SMILE", r'(?:(?<![ァ-ヶー])|(?<=ティファニー))スマイル|ティー\s*スマイル', r'T?\s*SMILE'),          # 97件（T の付かない「スマイル」も T スマイル）
    # ── ハードウェア（ボール 2〜4万 / マイクロ 7〜14万 / スモール 13〜26万 / ラージ 15〜24万円）──
    *_tf("ハードウェアボール", "HARDWEAR BALL", r'ボール', r'BALL', cond=_TF_HW),                       # 7件
    # マイクロリンク・スモールリンクはハードウェアの語が無くてもハードウェア（他のラインに無い名前）
    *_tf("ハードウェアマイクロリンク", "HARDWEAR MICRO LINK", r'マイクロ', r'MICRO', cond=_TF_HW,       # 10件
         extra=[r'マイクロ\s*リンク', _A + r'MICRO\s*LINK']),
    *_tf("ハードウェアスモールリンク", "HARDWEAR SMALL LINK", r'スモール', r'SMALL', cond=_TF_HW,      # 22件
         extra=[r'スモール\s*リンク', _A + r'SMALL\s*LINK']),
    *_tf("ハードウェアラージリンク", "HARDWEAR LARGE LINK",                                           # 14件
         r'ラージ\s*(?:ダブル\s*)?リンク|エクストラ\s*ラージ|グラジュエイテッド|ダブル\s*リンク',
         r'LARGE\s*LINK|EXTRA\s*LARGE|GRADUATED|DOUBLE\s*LINK', cond=_TF_HW),
    *_tf("ハードウェア", "HARDWEAR", r'ハードウ[ェエ]ア', r'HARD\s*WEAR'),                             # 14件
    # ── ビクトリア（ダイヤ。キー・ヴァインも含む。中央値 約40万円）──
    *_tf("ビクトリア", "VICTORIA", r'[ビヴ]ィ?クトリア(?!\s*(?:シークレット|ズ))', r"VICTORIA(?!'?S)"),  # 77件
    # ── リターントゥティファニー（タグ・ハート。1〜3万円）──
    *_tf("ダブルハートタグ", "DOUBLE HEART TAG", r'ダブル\s*ハート\s*タグ', r'DOUBLE\s*HEART\s*TAG',  # 27件
         # リターントゥのダブルハート、またはダイヤ・ゴールドでない「ダブルハート」（シルバーのタグ）
         extra=[_TF_RTT + r'ダブル\s*ハート',
                r'(?s)^(?!.*(?:ダイヤ|' + _A + r'(?:PT|K18|DIAMOND)|プラチナ|750|ゴールド)).*?ダブル\s*ハート']),
    *_tf("ハートタグ", "HEART TAG", r'ハート\s*タグ', r'HEART\s*TAG'),                                # 47件
    *_tf("オーバルタグ", "OVAL TAG", r'オーバル\s*タグ', r'OVAL\s*TAG'),                              # 6件
    *_tf("リターントゥティファニー", "RETURN TO TIFFANY", r'リターン\s*トゥ',                          # 48件
         r'RETURN\s*TO(?:\s*TIFFANY)?|RTT'),
    # ── エンチャント（スクロールはダイヤ・プラチナで 25万〜140万円、ハート等は 2万〜6万円）──
    *_tf("エンチャントスクロール", "ENCHANT SCROLL", r'エンチャント.*?スクロール', r'ENCHANT.*?SCROLL'),  # 4件
    *_tf("エンチャント", "ENCHANT", r'エンチャント', r'ENCHANT'),                                      # 72件
    *_tf("フルールドリス", "FLEUR DE LIS", r'フルール\s*[・･]?\s*ド\s*[・･]?\s*リス', r'FLEUR\s*DE\s*LIS'),  # 16件
    # ── ティファニーキー（ハートキー・ラウンドキー・ピアスドキー等。キーリング・キーホルダーは除く）──
    *_tf("ティファニーキー", "TIFFANY KEYS",                                                          # 32件
         r'(?:(?<![ァ-ヶー])|(?<=ハート)|(?<=ラウンド)|(?<=オーバル)|(?<=デイジー)|(?<=モダン)|(?<=ティファニー))'
         r'キー(?!\s*(?:ホルダー|リング|チェーン|ケース|カバー))',
         r'KEYS?(?!\s*(?:RING|HOLDER|CHAIN))'),
    # ティファニー ロック（現行のゴールド。「ロックグラス」「ハートロック」「パドロック」等は除く）
    *_tf("ティファニーロック", "TIFFANY LOCK",                                                         # 3件
         r'ティファニー\s*ロック(?!\s*グラス)|(?<![ァ-ヶー])ロック\s*(?:スモール|ミディアム|ラージ|バングル|ブレス)',
         r'TIFFANY\s*LOCK|LOCK\s*BANGLE', cond=r'(?s)^(?!.*(?:カデナ|パドロック|南京錠|ハート\s*ロック)).*?'),
    # ── 1837（メイカーズはゴールド中心で相場が別）──
    *_tf("1837メイカーズ", "1837 MAKERS", r'メ[イー]カーズ', r'MAKERS'),                                # 8件
    *_tf("1837", "1837", r'(?<!\d)1837(?!\d)', r'1837'),                                              # 140件
    # ── エルサ・ペレッティ ──
    *_tf("オープンハート", "OPEN HEART", r'オープン\s*ハート', r'OPEN\s*HEART'),                        # 147件
    *_tf("ビーン", "BEAN", r'ビーン(?!ズ?\s*ライター)', r'BEANS?'),                                    # 87件
    *_tf("ティアドロップ", "TEARDROP", r'ティア\s*ー?\s*ドロップ|涙型', r'TEAR\s*DROP'),                    # 21件
    *_tf("フルハート", "FULL HEART", r'フル\s*ハート', r'FULL\s*HEART'),                              # 11件
    *_tf("バイザヤード", "BY THE YARD", r'バイ\s*(?:ザ\s*ヤ|ヤ\s*ザ)ード', r'BY\s*THE\s*YARD'),                   # 113件
    *_tf("エターナルサークル", "ETERNAL CIRCLE", r'エターナル\s*サークル', r'ETERNAL\s*CIRCLE'),       # 7件
    *_tf("スネーク", "SNAKE", r'スネーク(?!\s*チェーン)', r'SNAKE(?!\s*CHAIN)'),                      # 6件（チェーンの種類を除く）
    *_tf("スターオブダビデ", "STAR OF DAVID", r'ダビデ', r'STAR\s*OF\s*DAVID'),                       # 3件
    *_tf("スターフィッシュ", "STARFISH", r'スターフィッシュ|フィッシュ\s*スター|ヒトデ', r'STAR\s*FISH'),  # 69件
    *_tf("ボーン", "BONE", r'ボーン\s*(?:カフ|リング|バングル|ブレス)|^ボーン$', r'BONE\s*(?:CUFF|RING)|^BONE$',
         cond=r'(?s)^(?!.*(?:バンブー|BAMBOO)).*?'),   # バンブー ボーン はバンブー  # 4件
    *_tf("ウェーブ", "WAVE", r'ウェーブ\s*(?:シングル|ダブル|トリプル|ファイブ)?\s*ロウ|^ウェーブ$',          # 3件
         r'WAVE\s*(?:SINGLE|DOUBLE|TRIPLE)?\s*ROW|^WAVE$'),
    *_tf("スプリットリング", "SPLIT RING", r'スプリット\s*リング', r'SPLIT\s*RING'),                  # 1件
    *_tf("シーホース", "SEAHORSE", r'シーホース', r'SEA\s*HORSE'),                                     # 1件
    *_tf("マドンナ", "MADONNA", r'マドンナ', r'MADONNA'),                                             # 2件
    *_tf("アップルハート", "APPLE", r'アップル(?!\s*ウォッチ)', r'APPLE(?!\s*WATCH)'),                  # 4件
    # ── パロマ・ピカソ ──
    *_tf("ラビングハート", "LOVING HEART", r'ラ[ビヴ]ィ?ング\s*ハート', r'LOVING\s*HEART'),                  # 77件
    *_tf("オリーブリーフ", "OLIVE LEAF", r'オリーブ\s*リーフ', r'OLIVE\s*LEAF'),                      # 9件
    *_tf("キス", "KISS", r'(?<![ァ-ヶー])キス(?![ァ-ヶー])', r'KISS'),                                # 48件（X キス・グラフィティ キス）
    *_tf("グラフィティラブ", "GRAFFITI LOVE",                                                         # 16件（ラブリング 15万円前後）
         r'グラフィティ\s*ー?\s*(?:ラブ|LOVE)|(?:(?<![ァ-ヶー])|(?<=ティファニー))ラブ\s*リング', r'GRAFFITI\s*LOVE|LOVE\s*RING',
         extra=[r'(?s)^(?=.*(?:パロマ|ピカソ|PALOMA|PICASSO)).*?(?:(?<![A-Za-z])LOVE(?![A-Za-z])|ラブ(?!\s*ノット))']),
    *_tf("ハートアロー", "HEART & ARROW",                                                             # 6件
         r'ハート\s*(?:&|と矢)?\s*アロー|グラフィティ\s*アロー', r'HEART\s*(?:&|AND)?\s*ARROW'),
    # パロマの X（「X」だけでは他と紛れるので、パロマ・ピカソ・グラフィティの語があるときだけ）
    *_tf("グラフィティX", "GRAFFITI X", r'(?<![A-Za-z0-9])X(?![A-Za-z0-9])', r'X',                    # 7件
         cond=r'(?s)^(?=.*(?:パロマ|ピカソ|グラフィティ|' + _A + r'(?:PALOMA|PICASSO|GRAFFITI)' + _Z + r')).*?',
         extra=[r'^グラフィティX$', r'^GRAFFITI\s*X$']),
    *_tf("ゾディアック", "ZODIAC",                                                                     # 5件（星座）
         r'ゾディアック|(?:牡羊|牡牛|双子|蟹|獅子|乙女|天秤|蠍|射手|山羊|水瓶|魚)座', r'ZODIAC'),
    *_tf("テンダネスハート", "TENDERNESS HEART", r'テンダネス', r'TENDERNESS'),                       # 60件
    *_tf("クレセントムーン", "CRESCENT MOON", r'クレセント\s*ムーン', r'CRESCENT\s*MOON'),            # 2件
    *_tf("ジグザグ", "ZIGZAG", r'ジグザグ', r'ZIG\s*ZAG'),                                            # 3件
    *_tf("マラケシュ", "MARRAKESH", r'マラケシュ', r'MARRAKESH'),                                     # 1件
    *_tf("ヴェネチアゴルドーニ", "VENEZIA GOLDONI", r'ゴルドーニ', r'GOLDONI'),                        # 1件
    *_tf("グルーブド", "GROOVED", r'グルー[ブヴ]', r'GROOVED?'),                                       # 10件（パロマ グルーブを含む）
    *_tf("シグネチャー", "SIGNATURE", r'シグネチャー?', r'SIGNATURE'),                                # 22件
    # ── ノット（ダブルロープ → ラブノット → その他のノット）──
    *_tf("ダブルロープ", "DOUBLE ROPE", r'ダブル\s*ロープ|センター\s*ノット', r'DOUBLE\s*ROPE|CENTER\s*KNOT'),  # 11件
    *_tf("ラブノット", "LOVE KNOT",                                                                   # 70件
         r'ラブ\s*ノット|フック\s*(?:&|アンド|and)?\s*アイ', r'LOVE\s*KNOT|HOOK\s*(?:&|AND)?\s*EYE'),
    *_tf("サマセット", "SOMERSET", r'サマセット', r'SOMERSET'),                                        # 15件
    *_tf("メッシュ", "MESH", r'メッシュ', r'MESH'),                                                    # 1件（サマセット メッシュは除く）
    # ノットは現行のティファニー ノット（ゴールド・ダイヤ、14万〜63万円）だけ。シルバー・コンビ・ヴィンテージの
    # 「ノット リング」等（1万〜4万円の別物）は除く
    *_tf("ケルティックノット", "CELTIC KNOT", r'ケルティック', r'CELTIC'),                            # 4件
    *_tf("ノット", "KNOT", r'(?:(?<![ァ-ヶー])|(?<=ティファニー))ノット', r'KNOT', cond=r'(?s)^(?!.*' + _TF_VINTAGE + r').*?'),  # 8件
    # ── その他の定番 ──
    *_tf("アトラス", "ATLAS", r'アトラス', r'ATLAS'),                                                 # 133件
    *_tf("インフィニティ", "INFINITY", r'インフィニティ', r'INFINITY'),                                # 20件
    *_tf("バンブー", "BAMBOO", r'バンブー', r'BAMBOO'),                                               # 26件
    *_tf("ベネチアン", "VENETIAN", r'[ベヴ]ェ?ネチアン', r'VENETIAN'),                                 # 41件
    *_tf("カデナ", "PADLOCK", r'カデナ|パドロック|ハート\s*ロック', r'PADLOCK|HEART\s*LOCK'),           # 18件
    *_tf("インターロッキングサークル", "INTERLOCKING CIRCLES", r'インターロッキング', r'INTERLOCKING'),  # 6件
    *_tf("リボン", "BOW", r'リボン(?!\s*(?:付|レイ))', r'BOW',                                          # 19件
         cond=r'(?s)^(?!.*(?:ケース|空箱|ギフトボックス)).*?'),          # 箱・ケースのリボンを除く
    *_tf("トグル", "TOGGLE", r'トグル', r'TOGGLE'),                                                   # 7件
    *_tf("ノーツ", "NOTES", r'(?<![ァ-ヶー])ノーツ', r'NOTES'),                                       # 14件
    *_tf("ドッツ", "DOTS", r'ドッツ', r'DOTS'),                                                       # 33件（ドッツハート・ドッツクロスも含む）
    *_tf("ミルグレインハート", "MILGRAIN HEART", r'ミルグレイン\s*(?:ラブ\s*)?ハート', r'MILGRAIN\s*HEART'),  # 3件
    *_tf("ミルグレイン", "MILGRAIN", r'ミルグレイン', r'MILGRAIN'),                                    # 51件
    *_tf("クラウンオブハート", "CROWN OF HEARTS", r'クラウン\s*オブ\s*ハート', r'CROWN\s*OF\s*HEARTS?'),  # 3件
    *_tf("トリプルハート", "TRIPLE HEART", r'トリプル\s*ハート', r'TRIPLE\s*HEART'),                  # 3件
    *_tf("パフハート", "PUFFED HEART", r'パフ\s*ハート', r'PUFF(?:ED)?\s*HEART'),                      # 2件
    *_tf("フィリグリーハート", "FILIGREE HEART", r'フィリグリー', r'FILIGREE'),                        # 2件
    *_tf("ハートリンク", "HEART LINK", r'ハート\s*リンク', r'HEART\s*LINK'),                          # 2件
    *_tf("スターハート", "STAR HEART", r'スター\s*(?:&|と)?\s*ハート', r'STAR\s*(?:&|AND)?\s*HEART'),  # 2件
    *_tf("ツイストハート", "TWIST HEART", r'ツイスト\s*ハート', r'TWIST\s*HEART'),                    # 2件
    *_tf("クローバーハート", "CLOVER HEART", r'クローバー\s*ハート', r'CLOVER\s*HEART'),              # 1件
    *_tf("カットアウトハート", "CUTOUT HEART", r'カット\s*アウト\s*ハート', r'CUT\s*OUT\s*HEART'),  # 1件
    *_tf("ラリアット", "LARIAT", r'ラリアット', r'LARIAT'),                                            # 1件
    *_tf("ドアノッカー", "DOOR KNOCKER", r'ドア\s*ノッカー', r'DOOR\s*KNOCKER'),                      # 1件
    *_tf("メナード", "MENARD", r'メナード', r'MENARD'),                                               # 2件（コラボ）
    *_tf("ボールダングル", "BALL DANGLE", r'ボール\s*ダングル', r'BALL\s*DANGLE'),                    # 3件
    *_tf("ダブルループ", "DOUBLE LOOP", r'ダブル\s*ループ', r'DOUBLE\s*LOOP'),                        # 2件
    *_tf("ペーパーフラワー", "PAPER FLOWERS", r'ペーパー\s*フラワー', r'PAPER\s*FLOWERS?'),            # 4件
    *_tf("サークレット", "CIRCLET", r'サークレット', r'CIRCLET'),                                      # 2件
    *_tf("メトロ", "METRO", r'メトロ', r'METRO'),                                                     # 3件
    *_tf("セビアナ", "SEVILLANA", r'セビアナ', r'SEVILLANA'),                                          # 3件
    *_tf("フランクゲーリー", "FRANK GEHRY", r'ゲーリー|エクウス', r'GEHRY|EQUUS'),                     # 1件
    *_tf("ドッグタグ", "DOG TAG", r'ドッグ\s*タグ', r'DOG\s*TAG'),                                    # 1件
    *_tf("バードクロス", "BIRD CROSS", r'バード\s*クロス', r'BIRD\s*CROSS'),                          # 3件
    *_tf("ペンギン", "PENGUIN", r'ペンギン', r'PENGUIN'),                                             # 2件
    *_tf("スワン", "SWAN", r'スワン', r'SWAN'),                                                       # 1件
    *_tf("フェザー", "FEATHER", r'フェザー', r'FEATHER'),                                             # 1件
    *_tf("チューリップ", "TULIP", r'チューリップ', r'TULIP'),                                          # 1件
    *_tf("エレメント", "ELEMENT", r'(?<![ァ-ヶー])エレメント', r'ELEMENT'),                             # 2件
    *_tf("ナローフープ", "NARROW HOOP", r'ナロー\s*フープ', r'NARROW\s*HOOP'),                        # 7件（1837 の付くものは 1837）
    *_tf("コイン", "COIN", r'(?<![ァ-ヶー])コイン', r'COIN'),                                          # 3件
    *_tf("ボールチャーム", "BALL CHARM", r'ボール\s*チャーム', r'BALL\s*CHARM'),                      # 2件
    *_tf("トリニティ", "TRINITY", r'トリニティ', r'TRINITY'),                                          # 2件（3連リング）
    *_tf("シングルダイヤモンド", "SINGLE DIAMOND", r'シングル\s*ダイヤ', r'SINGLE\s*DIAMOND'),          # 3件
    *_tf("ナイフエッジ", "KNIFE EDGE", r'ナイフ\s*エッジ', r'KNIFE\s*EDGE'),                          # 7件
    *_tf("オープンウェーブ", "OPEN WAVE", r'オープン\s*ウェーブ', r'OPEN\s*WAVE'),                    # 3件
    *_tf("バンドウィズ", "BAND WITH", r'バンド\s*ウィズ', r'BAND\s*WITH'),                            # 4件
    *_tf("フォージ", "FORGE", r'フォージ', r'FORGE'),                                                 # 79件
    *_tf("ダイアゴナル", "DIAGONAL", r'ダイアゴナル', r'DIAGONAL'),                                    # 6件
    *_tf("トリプルスター", "TRIPLE STAR", r'トリプル\s*スター', r'TRIPLE\s*STAR'),                    # 4件
    *_tf("センチメンタルハート", "SENTIMENTAL HEART", r'センチメンタル\s*ハート', r'SENTIMENTAL\s*HEART'),  # 6件
    *_tf("カーブドバンド", "CURVED BAND", r'カーブド\s*(?:バンド|リング)', r'CURVED\s*(?:BAND|RING)'),                    # 8件
    *_tf("ハーモニー", "HARMONY", r'ハーモニー', r'HARMONY'),                                         # 15件
    *_tf("ソレスト", "SOLESTE", r'ソレスト', r'SOLESTE'),                                              # 34件
    *_tf("ルシダ", "LUCIDA", r'ルシダ(?!\s*スクリプト)', r'LUCIDA(?!\s*SCRIPT)'),                     # 66件
    *_tf("ノヴォ", "NOVO", r'ノ[ヴボ]ォ', r'NOVO'),                                                   # 5件
    *_tf("ジャズ", "JAZZ", r'ジャズ', r'JAZZ'),                                                       # 7件
    *_tf("フォーエバー", "FOREVER", r'フォーエ[バヴ]ー', r'FOREVER'),                                   # 4件
    *_tf("スタッキングバンド", "STACKING BAND", r'スタッキング', r'STACKING'),                          # 8件
    *_tf("セッティング", "SETTING", r'セッティング', r'SETTING'),                                     # 8件
    *_tf("ソリテール", "SOLITAIRE", r'ソリ(?:テール|ティア|テア|テイル)', r'SOLITAIRE'),               # 26件
    # クロスは「クロス ネックレス」「オープン クロス」等の形だけ（クロス付き=磨き布、クロスステッチ等を除く）
    *_tf("クロス", "CROSS", r'(?<!バード)クロス(?!\s*ステッチ)' + _TF_ITEM + r'|オープン\s*クロス|^クロス$',  # 13件
         r'CROSS(?=\s*(?:NECKLACE|PENDANT|RING|EARRING))|^CROSS$'),
]
_STRICT_MODELS["ティファニー"] = [(name, pats) for name, _en, pats in _TIFFANY_MODELS]
_STRICT_EN.update({name: en for name, en, _pats in _TIFFANY_MODELS})


# ── タトラス: モデル名（品番 LTA13A4301 等が無い出品だけモデル名で照合する）──────────────
# メルカリ売却済み（ログインなし。2026-10 調査: 134回検索・タトラスのタイトル 3,909件）で
# ダウンの売却が4件以上あったモデル。品番がタイトルに載る出品は売却済みの約3%しかないため、
# モデル名を主に照合する。モデル名は男女で別の名前なので、性別では分けない。
# 派生モデルを先に判定する（右の件数はダウンの売却件数・中央値）:
#   エンリ（ドミツィアーノのナイロン版。「ドミツィアーノ エンリ」と書かれる）29件 ¥100,000 ← ドミツィアーノ 77件 ¥65,100
#   ドミツィアーノ シルク（シルク混）24件 ¥82,500 は通常のドミツィアーノと別モデルにする
#   オルサバ（コサヴァの別注版。「別注KOSAVA オルサバ」と書かれる）14件 ¥94,000 ← コサヴァ 82件 ¥67,950
#   クラッツ（ベルボの前身。「KRAZ ベルボ同型」と書かれる）108件 ¥34,450 ← ベルボ 97件 ¥53,000
#   アカシア（ノコの別注版。「NOKO ACACIA」と書かれる）24件 ¥36,500 ← ノコ 69件 ¥49,800
# Rライン（上位ライン）はチーマ・ドミツィアーノで通常品と相場がほぼ同じ（¥41,500 / ¥46,000 等）なので分けない。
# 類似品（「タトラス風」「TATRAS系」「ポリテアマ風」等）は除く。半角カナ（ﾎﾟﾘﾃｱﾏ）は NFKC で整えてから照合する
# 「ポリテアマ風」「ベルボと同じ形」「ベルボ同型」等（そのモデルそのものではない）と、
# 「ロコちゃん様専用」「タロ様」等の購入者名を除く
_TT_NF = r'(?!\s*(?:風|似|タイプ|系|と同じ|と同型|同型|みたい|のよう|っぽ|様|さん|ちゃん|くん))'
_TT_NOT_LIKE = r'(?s)^(?!.*(?:タトラス|TATRAS)\s*(?:風|似|タイプ|系|っぽ|好き))'   # 「タトラス風 ダウン」等
_KB = r'(?<![ァ-ヶー])'                                                     # 直前がカタカナでない
_KE = r'(?![ァ-ヶー])'                                                      # 直後がカタカナでない


def _tt(canon: str, en: str, kana: str, eng: str, cond: str = "") -> list[tuple[str, str, list[str]]]:
    """タトラスのモデル。kana / eng: 正規表現（英字は前後に英字が続かないこと）。
    cond: タイトル全体の条件（ドミツィアーノ シルクのシルク等）"""
    pats = [r'(?:' + kana + r')' + _TT_NF, _A + r'(?:' + eng + r')' + _Z + _TT_NF]
    return [(canon, en, [_TT_NOT_LIKE + cond + r'(?=.*?(?:' + p + r'))' for p in pats])]


# 英字の綴り違い（メルカリ売り切れのタイトル。ログインなし 2026-10 確認）: DOMIZINO 2件
_TT_DOMIZIANO = (r'ドミッ?ツィ?アーノ', r'DOMIZIA?NO')
_TT_SILK = r'(?=.*(?:シルク|' + _A + r'SILK' + _Z + r'))'

_TATRAS_MODELS: list[tuple[str, str, list[str]]] = [
    # ── 派生モデル（親モデルより先に判定）──
    *_tt("エンリ", "ENRI", _KB + r'エンリ' + _KE, r'ENRI'),                                       # 29件 ¥100,000
    # 正規名は空白入り（メルカリ等の検索語「タトラス ドミツィアーノ シルク」にするため。照合は空白を無視する）
    *_tt("ドミツィアーノ シルク", "DOMIZIANO SILK", *_TT_DOMIZIANO, cond=_TT_SILK),              # 24件 ¥82,500
    *_tt("ドミツィアーノ", "DOMIZIANO", *_TT_DOMIZIANO),                                          # 77件 ¥65,100
    *_tt("オルサバ", "OLSAVA", r'オルサ[バヴ]ァ?', r'O[LR]SAVA'),                                # 14件 ¥94,000
    *_tt("コサヴァ", "KOSAVA", r'コサ[ヴバ]ァ?', r'KOSAVA'),                                      # 82件 ¥67,950
    *_tt("クラッツ", "KRAZ", r'クラッツ|クラーズ', r'KRAZ'),                                      # 108件 ¥34,450
    *_tt("ベルボ", "BELBO", r'ベルボ(?!トム)', r'BE[LR]BO'),                                      # 97件 ¥53,000 （BERBO 1件）
    # ── レディース ──
    *_tt("アカシア", "ACACIA", r'アカシア|アカッチャ', r'ACACIA'),                               # 24件 ¥36,500
    *_tt("ポリテアマ", "POLITEAMA", r'ポリテア?ー?マ|ポリアテマ|ポルテアマ', r'POLITEA?MA'),       # 129件 ¥38,000 （POLITEMA 1件）
    *_tt("ラビアナ", "LAVIANA", r'ラ[ビヴ]ィ?アナ', r'LAVIANA'),                                   # 98件 ¥46,040
    *_tt("ヴァレナ", "VARENA", r'ヴァレナ|' + _KB + r'バレナ', r'V[AE]RENA'),                      # 79件 ¥61,000 （VERENA 5件）
    *_tt("バビラ", "BABILA", r'バビラ', r'BABILA'),                                               # 66件 ¥23,250
    *_tt("サルマ", "SARMA", _KB + r'サルマ' + _KE, r'SARU?MA'),                                   # 57件 ¥70,000 （SARUMA 4件）
    *_tt("ノコ", "NOKO", _KB + r'ノコ' + _KE, r'NOKO'),                                           # 69件 ¥49,800
    *_tt("チーマ", "CIMA", _KB + r'チーマ' + _KE, r'CH?IMA'),                                     # 60件 ¥43,950 （CHIMA 1件）
    *_tt("サリン", "SARIN", _KB + r'サリン' + _KE, r'SARIN'),                                     # 51件 ¥18,200
    *_tt("アゴーニャ", "AGOGNA", r'アゴーニャ', r'AGOGNA|AGONY?A'),                               # 50件 ¥21,000 （AGONYA 1件）
    *_tt("クルサ", "CURSA", _KB + r'クルサ' + _KE, r'CURSA'),                                     # 39件 ¥23,000
    *_tt("ロレンツァーナ", "LORENZANA", r'ロレンツァーナ', r'LORENZANA'),                         # 37件 ¥23,760
    *_tt("スピノサ", "SPINOSA", r'スピノサ', r'SPINOSA'),                                         # 38件 ¥35,750
    *_tt("ルセラ", "LUSERA", _KB + r'ルセラ' + _KE, r'LUSERA'),                                   # 30件 ¥30,000
    *_tt("イセラ", "ISERA", _KB + r'イセラ' + _KE, r'ISERA'),                                     # 30件 ¥38,950
    *_tt("オルシエラ", "ORSIERA", r'オルシエラ', r'ORSIERA'),                                     # 28件 ¥37,750
    *_tt("スペランツァ", "SPERANZA", r'スペランツァ', r'SPERANZA'),                               # 22件 ¥38,500
    *_tt("フォンド", "FONDO", _KB + r'フォンド' + _KE, r'FONDO'),                                 # 17件 ¥50,000
    *_tt("サッビア", "SABBIA", r'サッビア', r'SABBIA'),                                           # 13件 ¥40,000
    *_tt("プリムラ", "PRIMULA", r'プリムラ', r'PRIMULA'),                                         # 7件 ¥38,000
    *_tt("ゼヤ", "ZEJA", _KB + r'ゼ[ヤジ]ャ?' + _KE, r'ZEJA'),                                    # 6件 ¥34,950
    *_tt("サブリナ", "SABRINA", r'サブリナ', r'SABRINA'),                                         # 20件 ¥27,000
    *_tt("コルマ", "COLMA", _KB + r'コルマ' + _KE, r'COLMA'),                                     # 35件 ¥25,000
    *_tt("アリセオ", "ALISEO", r'アリセオ', r'ALISEO'),                                           # 9件 ¥31,000
    *_tt("ロコ", "LOKO", _KB + r'ロコ' + _KE, r'LOKO'),                                           # 5件 ¥40,000
    *_tt("アレン", "AREN", _KB + r'アレン' + _KE, r'AREN'),                                       # 5件 ¥30,000
    # ── メンズ ──
    *_tt("ボルボレ", "BORBORE", r'ボルボレ', r'BORBORE'),                                         # 101件 ¥46,000
    *_tt("アゴルド", "AGORDO", r'アゴルド', r'AGORDO'),                                           # 92件 ¥47,250
    *_tt("ジェッソ", "GESSO", r'ジェッソ', r'GESSO'),                                             # 82件 ¥35,000
    *_tt("ポセイドーネ", "POSEIDONE", r'ポセイドー?ネ|ポセイドン', r'POSEIDONE?'),                # 77件 ¥36,000 （POSEIDON 1件）
    *_tt("ボエシオ", "BOESIO", r'ボエシオ', r'BOESIO'),                                           # 60件 ¥38,650
    *_tt("セラミカ", "CERAMICA", r'セラミカ', r'CERAMICA'),                                       # 34件 ¥38,750
    *_tt("ブレヴァ", "BREVA", r'ブレ[ヴバ]ァ?' + _KE, r'BREVA'),                                  # 32件 ¥47,450
    *_tt("クローネ", "CURONE", _KB + r'クローネ' + _KE, r'CURONE'),                               # 32件 ¥33,250
    *_tt("ディオメデ", "DIOMEDE", r'ディオメデ', r'DIOMEDE'),                                     # 27件 ¥37,500
    *_tt("グラミ", "GRAMI", _KB + r'グラミ' + _KE, r'GRAMI'),                                     # 18件 ¥69,700
    *_tt("アヴェチ", "AVECI", r'アヴェチ', r'AVECI'),                                             # 17件 ¥80,000
    *_tt("フォラート", "FORATO", r'フォラート', r'FORATO'),                                       # 16件 ¥31,500
    *_tt("ケイティー", "KEITIE", r'ケイティー?', r'KEITIE'),                                      # 15件 ¥79,800
    *_tt("シツィオ", "CIZIO", r'シツィオ', r'CIZIO'),                                             # 13件 ¥139,800
    *_tt("オソド", "OSODO", r'オソド', r'OSODO'),                                                 # 12件 ¥82,000
    *_tt("エルコーレ", "ERCOLE", r'エルコーレ', r'ERCOLE'),                                       # 12件 ¥30,600
    *_tt("ブレッジョ", "BLEGGIO", r'ブレッ?ー?ジョ', r'BLEGGIO'),                                 # 11件 ¥46,000
    *_tt("デメトリオ", "DEMETRIO", r'デメトリオ', r'DEMETRIO'),                                   # 11件 ¥50,000
    *_tt("カシマーゾ", "CASSIMASO", r'カシマーゾ', r'CASSIMASO'),                                 # 9件 ¥86,000
    *_tt("イザール", "IZAR", r'イザール', r'IZAR'),                                               # 9件 ¥22,980
    *_tt("タロ", "TARO", _KB + r'タロ' + _KE, r'TARO'),                                           # 6件 ¥50,000
    *_tt("サルガス", "SARGAS", r'サルガス', r'SARGAS'),                                           # 20件 ¥29,400
    *_tt("ディナンド", "DINANDO", r'ディナンド', r'DINANDO'),                                     # 4件 ¥79,100
]
# 検索語に加える英字の綴り違い（ブランド → {正規名: [綴り]}）。メルカリ売り切れ（ログインなし 2026-10 確認）で
# 正しい英字名を含まないタイトルが一定数あったもの。他の綴り違い（SARUMA・CHIMA 等）は1〜4件で、
# どれもカタカナ名も併記されているため照合（正規表現）にだけ加え、検索語にはしない
_SEARCH_VARIANTS: dict[str, dict[str, list[str]]] = {
    "タトラス": {"ヴァレナ": ["VERENA"]},   # 「TATRAS VERENA ダウンジャケット」等 5件（英字のみのタイトル）
}
_STRICT_MODELS["タトラス"] = [(name, pats) for name, _en, pats in _TATRAS_MODELS]
_STRICT_EN.update({name: en for name, en, _pats in _TATRAS_MODELS})


# ── カナダグース: モデル名（品番 3438JM 等が無い出品だけモデル名で照合する）──────────────
# メルカリ売り切れ（ログインなし。2026-10 調査: 271回検索・カナダグースのタイトル 4,490件）で
# 売却がおおむね5件以上あったダウンのモデル（ローズ=RHODES は4件だが ¥130,000 と高額なので入れる）。品番がタイトルに載る出品は売却済みの約13%しかないため、
# モデル名を主に照合する（品番があれば品番を優先し、品番の相場が無ければモデル名で取り直す）。
# フリースのキンロス・ライトジャケットのミンデン・ニットのウィンドブリッジ等（ダウンでないもの）は入れない。
# 右の件数・中央値は売り切れのタイトル（¥8,000 以上。ファーだけ等の部品を除くため）。
# ブラックレーベル（ブラックディスク。上位ライン）は通常品より2〜8割高いモデルが多いため、
# ブラックレーベルの売却が8件以上あり相場が15%以上違うモデルは「<モデル名> ブラックレーベル」を別モデルにする
# （タイトルのどこかに「ブラックレーベル」「BLACK LABEL」等、または品番の B（3804MB 等）があるもの）。
# 次の派生も親モデルより先に判定する:
#   ハイブリッジライト（軽量版）40件 ¥29,999 ← ハイブリッジ（ニット切替・ベース等）85件 ¥55,000
#   クロフトンベスト 6件 ¥56,500 ← クロフトン（パッファー）通常 33件 ¥90,000
# 別モデルにしないもの: フュージョンフィット（アジア向けの細身。シャトー FF 11件 ¥52,000 / 全体 ¥55,000 等、
# 相場の差が一定しない）、ヘリテージ（現行の定番ラインの呼び名）、PBI（シロクマ保護の限定色。5件以下）、
# HUMANATURE（2件）、Rライン/丈（R=レギュラー）。ブラックレーベルの差が小さいクレストン（BL 47件 ¥70,000 /
# 通常 45件 ¥70,000）・ロレッタ・ブライデン・ケントン等も分けない。
# 誤判定対策: 「カナダグース風」「CANADA GOOSE系」「ジャスパー好きへ」（他社品の宣伝）、「ファーのみ」「フードのみ」
# 「フード用ファー」等の部品だけの出品は除く。
# 短い名前（ロッジ・キャンプ・ローズ・バンフ・リドー 等）は直前がカタカナでないこと（「アローズ」「フローレンス」
# 「ブロッジ」を除く。「カナダグースロッジ」のようにブランド名に続けて書いたものは通す）、
# 一般語と同じ名前（キャンプ・ポーター・アプローチ・ローズ）は「キャンプフーディ」「ポーターボンバー」
# 「アプローチジャケット」「ローズパーカ」のように商品名の形のときだけモデル名とする。
# 「ギャルソンベスト」はガーソンベストの別表記（コムデギャルソンとのコラボは除く）
_CG_NF = r'(?!\s*(?:風|似|タイプ|系|と同じ|と同型|同型|みたい|のよう|っぽ|好き|様|さん|ちゃん|くん))'
_CG_NOT_LIKE = (r'(?s)^(?!.*(?:カナダ\s*グース|CANADA\s*GOOSE)\s*(?:風|似|タイプ|系|っぽ|好き))'
                r'(?!.*(?:(?:ファー|フード|ワッペン|タグ)\s*(?:のみ|だけ|単体|単品)|(?:フード|ファー)\s*用))')
_CG_KB = r'(?:(?<![ァ-ヶー])|(?<=グース))'                     # 直前がカタカナでない（ブランド名の直後は可）
_CG_A = r'(?:(?<![A-Za-z])|(?<=GOOSE))'
_CG_Z = r'(?:(?![A-Za-z])|(?=(?:PARKA|JACKET|HOOD|VEST|BOMBER|PUFFER|COAT)))'   # JASPERPARKA 等
_CG_BL = (r'(?=.*(?:ブラック\s*(?:レーベル|ラベル|ラペル|ディスク)|ブラレ|' + _A + r'BLACK\s*(?:LABEL|DISC)'
          + r'|BLACKLABEL|(?<![A-Za-z0-9])[1-9]\d{3}J?[ML]J?B))')
_CG_VEST = r'(?=.*(?:ベスト|' + _A + r'VEST' + _Z + r'))'


def _cg(canon: str, en: str, kana: str, eng: str, cond: str = "") -> list[tuple[str, str, list[str]]]:
    """カナダグースのモデル。kana / eng: 正規表現（英字は前後に英字が続かないこと）。
    cond: タイトル全体の条件（ブラックレーベル・ベスト）"""
    pats = [r'(?:' + kana + r')' + _CG_NF, _CG_A + r'(?:' + eng + r')' + _CG_Z + _CG_NF]
    # モデル名そのもの（「ポーター」「CAMP」）は商品名の形でなくても正規名にする（main.py・app.py が
    # extract_name(モデル名) で正規名かどうかを確かめるため。キャンプ・ポーター・アプローチ・ローズ用）
    exact = r'(?i)^\s*(?:' + re.escape(canon) + r'|' + re.escape(en) + r')\s*$'
    return [(canon, en, [_CG_NOT_LIKE + cond + r'(?=.*?(?:' + p + r'))' for p in pats] + [exact])]


def _cg_bl(canon: str, en: str, kana: str, eng: str) -> list[tuple[str, str, list[str]]]:
    """ブラックレーベルを別モデルにするモデル（ブラックレーベル → 通常品の順に判定する）。
    正規名は空白入り（検索語「カナダグース ジャスパー ブラックレーベル」にするため。照合は空白を無視する）"""
    return [*_cg(f"{canon} ブラックレーベル", f"{en} BLACK LABEL", kana, eng, cond=_CG_BL),
            *_cg(canon, en, kana, eng)]


_CANADAGOOSE_MODELS: list[tuple[str, str, list[str]]] = [
    # ── メンズ（エクスペディションは男女共通）──
    *_cg_bl("ジャスパー", "JASPER", r'ジャスパー|ジャスパ(?!ー)', r'JASPER'),  # BL 19件 ¥71,600 / 通常 152件 ¥49,850
    *_cg_bl("シャトー", "CHATEAU", _CG_KB + r'シャトー', r'CHATEAU'),  # BL 38件 ¥81,950 / 通常 68件 ¥55,000
    *_cg_bl("ウィンダム", "WYNDHAM", r'ウ[ィイ]ンダム', r'WYNDHAM'),  # BL 28件 ¥121,500 / 通常 105件 ¥89,000
    *_cg_bl("マクミラン", "MACMILLAN", r'マッ?クミラン', r'MAC\s*MILLAN|MCMILLAN'),  # BL 45件 ¥100,000 / 通常 79件 ¥66,300
    *_cg_bl("ロッジ", "LODGE", _CG_KB + r'ロッジ', r'LODGE'),  # BL 47件 ¥85,000 / 通常 63件 ¥65,000
    *_cg_bl("ラングフォード", "LANGFORD", r'ラングフォード', r'LANGFORD'),  # BL 17件 ¥99,000 / 通常 45件 ¥69,800
    *_cg_bl("カーソン", "CARSON", _CG_KB + r'カーソン', r'CARSON'),  # BL 8件 ¥82,000 / 通常 78件 ¥61,500
    *_cg("クロフトンベスト", "CROFTON VEST", r'クロフトン', r'CROFTON', cond=_CG_VEST),  # 6件 ¥56,500
    *_cg_bl("クロフトン", "CROFTON", r'クロフトン', r'CROFTON'),  # BL 25件 ¥100,000 / 通常 33件 ¥90,000
    *_cg_bl("ガーソン", "GARSON", _CG_KB + r'ガーソン|(?<!コムデ)(?<!コム・デ・)(?<!コム デ )ギャルソン\s*(?:ダウン\s*)?ベスト',
            r'GARSON'),  # BL 17件 ¥60,000 / 通常 57件 ¥46,000
    *_cg("ハイブリッジライト", "HYBRIDGE LITE", r'ハイブリッ[ジドト]\s*ライト', r'HYBRIDGE\s*LITE'),  # 40件 ¥29,999
    # ブラックレーベル（13件 ¥58,888）と通常品（72件 ¥53,000）の差が15%未満なので分けない
    *_cg("ハイブリッジ", "HYBRIDGE", r'ハイブリッ[ジドト]', r'HYBRIDGE'),  # 85件 ¥55,000
    *_cg("カムループス", "KAMLOOPS", r'カムループス|カムループ(?!ス)', r'KAMLOOPS?'),  # 135件 ¥32,000
    *_cg("チリワック", "CHILLIWACK", r'チリワック', r'CHILL?IWACK'),  # 106件 ¥53,900
    *_cg("ラブラドール", "LABRADOR", r'ラブラドール', r'LABRADOR'),  # 104件 ¥49,999
    *_cg("エクスペディション", "EXPEDITION", r'エクスペデ[ィ]?ション', r'EXPEDITION'),  # 68件 ¥39,650
    *_cg("クレストン", "CRESTON", r'クレストン', r'CRESTON'),  # 80件 ¥71,000
    *_cg("ラッセル", "RUSSELL", _CG_KB + r'ラッセル', r'RUSSELL?'),  # 74件 ¥54,000
    *_cg("フリースタイル", "FREESTYLE", r'フリー\s*スタイル', r'FREE\s*STYLE'),  # 80件 ¥33,000
    *_cg("シタデル", "CITADEL", r'シタデル', r'CITADEL'),  # 52件 ¥37,000
    *_cg("フレイザー", "FRASIER", r'フレイザー', r'FRAS(?:IE|E)R'),  # 31件 ¥85,000
    *_cg("アームストロング", "ARMSTRONG", r'アームストロング', r'ARMSTRONG'),  # 32件 ¥54,500
    *_cg("オーフォード", "ORFORD", r'オーフォード', r'ORFORD'),  # 26件 ¥45,500
    *_cg("コンスタブル", "CONSTABLE", r'コンスタブル', r'CONSTABLE'),  # 25件 ¥27,900
    *_cg("ヨークトン", "YORKTON", r'ヨークトン', r'YORKTON'),  # 22件 ¥40,250
    *_cg("ウールフォード", "WOOLFORD", r'ウールフォード', r'WOOLFORD'),  # 19件 ¥48,000
    *_cg("エモリー", "EMORY", r'エモリー', r'EMORY'),  # 16件 ¥60,000
    *_cg("エバレット", "EVERETT", r'エ[バヴ]ェ?レット', r'EVERETT'),  # 17件 ¥77,000
    *_cg("ケントン", "KENTON", _CG_KB + r'ケントン', r'KENTON'),  # 15件 ¥98,800
    *_cg("ジャンクション", "JUNCTION", r'ジャンクション', r'JUNCTION'),  # 13件 ¥110,000
    *_cg("ハミルトン", "HAMILTON", r'ハミルトン', r'HAMILTON'),  # 12件 ¥33,000
    *_cg("ポーター", "PORTER", _CG_KB + r'ポーター\s*ボンバー', r'PORTER\s*BOMBER'),  # 10件 ¥91,499
    *_cg("ブライデン", "BRYDEN", r'ブライデン', r'BRYDEN'),  # 11件 ¥155,000
    *_cg("ブルックベール", "BROOKVALE", r'ブルックベール', r'BROOKVALE'),  # 10件 ¥39,000
    *_cg("ブルックフィールド", "BROOKFIELD", r'ブルックフィールド', r'BROOKFIELD'),  # 6件 ¥42,500
    *_cg("マクリーン", "MACLEAN", r'マクリーン', r'MAC\s*LEAN'),  # 9件 ¥98,000
    *_cg("フォレスター", "FORESTER", r'フォレスター', r'FORESTER'),  # 10件 ¥55,500
    *_cg("ボーデン", "BORDEN", _CG_KB + r'ボーデン', r'BORDEN'),  # 10件 ¥65,500
    *_cg("アプローチ", "APPROACH", r'アプローチ\s*(?:ダウン\s*)?(?:ジャケット|パーカ|フーディ)',
         r'APPROACH\s*(?:JACKET|HOOD)'),
    *_cg("スノーマントラ", "SNOW MANTRA", r'スノー\s*マントラ', r'SNOW\s*MANTRA'),  # 8件 ¥66,500
    *_cg("サボナ", "SAVONA", r'サ[ボバヴ]ォ?ナ', r'SAVONA'),  # 9件 ¥60,000
    *_cg("ローズ", "RHODES", _CG_KB + r'ローズ\s*(?:パーカ|フーディ|ジャケット)', r'RHODES'),  # 4件 ¥130,000
    *_cg("ペンビナ", "PEMBINA", r'ペンビナ', r'PEMBINA'),  # 6件 ¥56,220
    *_cg("ラングレー", "LANGLEY", r'ラングレー', r'LANGLEY'),  # 5件 ¥44,000
    *_cg("オズボーン", "OSBORNE", r'オズボーン', r'OSBORNE'),  # 12件 ¥85,190
    *_cg("バンクロフト", "BANCROFT", r'バンクロフト', r'BANCROFT'),  # 13件 ¥39,700
    *_cg("モントリオール", "MONTREAL", r'モントリオール', r'MONTREAL'),  # 52件 ¥19,800
    *_cg("ウィンザー", "WINDSOR", r'ウ[ィイ]ンザー', r'WINDSOR'),  # 10件 ¥23,900
    *_cg("ハドソン", "HUDSON", r'ハドソン', r'HUDSON'),  # 6件 ¥39,500
    # ── レディース ──
    *_cg_bl("シェルバーン", "SHELBURNE", r'シェルバーン', r'SHELBURNE?'),  # BL 35件 ¥75,000 / 通常 70件 ¥40,000
    *_cg_bl("メイトランド", "MAITLAND", r'メイトランド', r'MAITLAND'),  # BL 71件 ¥58,100 / 通常 36件 ¥50,000
    *_cg_bl("ロスクレア", "ROSSCLAIR", r'ロスクレア', r'ROSSCLAIR?'),  # BL 9件 ¥66,999 / 通常 31件 ¥41,000
    *_cg_bl("チェルシー", "CHELSEA", r'チェルシー', r'CHELSEA'),  # BL 13件 ¥118,000 / 通常 25件 ¥63,200
    *_cg("マッケンジー", "MACKENZIE", r'マッケンジー', r'MAC\s*KENZIE|MCKENZIE'),  # 104件 ¥37,000
    *_cg("ブロンテ", "BRONTE", _CG_KB + r'[ブプ]ロンテ', r'BRONTE'),  # 99件 ¥35,000
    *_cg("ケンジントン", "KENSINGTON", r'ケンジントン', r'KENSINGTON'),  # 49件 ¥37,500
    *_cg("トリリウム", "TRILLIUM", r'トリリウム', r'TRILLIUM'),  # 38件 ¥42,900
    *_cg("ヴィクトリア", "VICTORIA", r'[ヴビ]ィ?クトリア', r'VICTORIA'),  # 58件 ¥30,000
    *_cg("モンテベロ", "MONTEBELLO", r'モンテベッ?ロ', r'MONTEBELLO'),  # 50件 ¥39,450
    *_cg("シャーロット", "CHARLOTTE", r'シャーロット', r'CHARLOTTE'),  # 48件 ¥37,500
    *_cg("ロレッタ", "LORETTE", r'ロレッ[タト]', r'LORETT[EA]'),  # 33件 ¥61,000
    *_cg("ミスティーク", "MYSTIQUE", r'ミスティー?ク|ミスティック', r'MYSTIQUE'),  # 30件 ¥79,000
    *_cg("ソラリス", "SOLARIS", r'ソラリス', r'SOLARIS'),  # 29件 ¥42,100
    *_cg("アリストン", "ALLISTON", r'アリストン', r'ALLISTON'),  # 23件 ¥68,000
    *_cg("サイプレス", "CYPRESS", r'サイプレス', r'CYPRESS'),  # 14件 ¥66,500
    *_cg("キャンプ", "CAMP", _CG_KB + r'キャンプ\s*(?:フーディ|ダウン|ジャケット)', r'CAMP\s*(?:HOOD|DOWN|JACKET)'),  # 8件 ¥33,460
    *_cg("オーロラ", "AURORA", _CG_KB + r'オーロラ(?!\s*(?:カラー|色))', r'AURORA'),  # 9件 ¥118,000
    *_cg("ローレンス", "LAWRENCE", _CG_KB + r'ローレンス', r'LAWRENCE'),  # 12件 ¥86,000
    *_cg("リドー", "RIDEAU", _CG_KB + r'リドー', r'RIDEAU'),  # 11件 ¥39,000
    *_cg("バイワード", "BYWARD", r'バイワード', r'BYWARD'),  # 7件 ¥137,100
    *_cg("エルズミア", "ELLESMERE", r'エルズミア', r'ELLESMERE'),  # 6件 ¥42,750
    *_cg("バンフ", "BANFF", _CG_KB + r'バンフ', r'BANFF'),  # 5件 ¥40,000
    *_cg("エッジウッド", "EDGEWOOD", r'エッジウッド', r'EDGEWOOD'),  # 5件 ¥65,000
    *_cg("ベルビル", "BELLEVILLE", r'ベルビル', r'BELLEVILLE'),  # 44件 ¥40,000
]
_SEARCH_VARIANTS["カナダグース"] = {
    "ガーソン": ["ギャルソンベスト"],   # 「カナダグース ギャルソンベスト」13件（ガーソンと書かないものが多い）
    "ハイブリッジ": ["ハイブリッド"],   # 「ハイブリッド ライトダウン」等 14件
    "ラッセル": ["RUSSEL"],            # 「CANADA GOOSE Russel Parka」等 5件（英字のみのタイトル）
}
_STRICT_MODELS["カナダグース"] = [(name, pats) for name, _en, pats in _CANADAGOOSE_MODELS]
_STRICT_EN.update({name: en for name, en, _pats in _CANADAGOOSE_MODELS})

# ── モンクレール: モデル名（品番 4036605・1A53600 等が無い出品だけモデル名で照合する）──────────────
# メルカリ売り切れ（ログインなし。2026-10 調査: 370回検索・モンクレールのタイトル 6,891件）で
# 大人用のダウンの売却がおおむね5件以上あったモデル（ロクステルは4件だが ¥260,000 と高額なので入れる）。
# 品番がタイトルに載る売り切れは約1%しかないため、モデル名を主に照合する（品番があれば品番を優先し、
# 品番の相場が無ければモデル名で取り直す。name_fallback）。右の件数・中央値は売り切れのタイトル
# （¥10,000 以上・キッズサイズを除く）。モンクレールはメンズとレディースで別の名前を付けるため、性別では分けない。
# 派生モデルは親モデルより先に判定する（相場が大きく違うため別モデルにする）:
#   マヤ70（70周年。VEZERE=ヴェゼールも同じモデル）29件 ¥194,600 / マヤファー（MAYAF）36件 ¥217,450 ← マヤ 182件 ¥120,200
#   バディファー（BADYFUR・BADYF）46件 ¥144,999 ← バディ 53件 ¥50,000
#   ボエディック 16件 ¥268,800 ← ボエド 47件 ¥235,000、アボセット ← アヴォーチェ、ミリエロン ← ミリエル、
#   エルミファー ← エルミンヌ は別の名前なので並びだけ気をつける
# 別モデルにしないもの: TIBB（レディースの TIBB GILET。カタカナではどちらもチブと書かれ区別できないため
# チブに含める。TIBB 28件 ¥68,000 / 合計 78件 ¥57,500）、ジーニアス・グルノーブル・ガムブルー（ラインの呼び名。
# 中のモデルはそれぞれのモデル名で照合する）、MONTCLAR（4件。MONTCLA=モンクラーとは別のモデルなので照合しない）、
# CLUNYE（7件 ¥200,000。カタカナ表記が無いため登録せず、CLUNY=クルーニーとも照合しない）。
# 入れないもの: ダウンでないモデル（CARLES・GRIMPEURS=ナイロンジャケット、BASILE=サンダル、DURANCE=バッグ）、
# 「メール」（MAIRE。一般語と同じで照合できない）。
# フラグメント（藤原ヒロシとのコラボ。ジーニアスのライン）は変更前から登録済みのため残す（ダウンの売却 18件 ¥74,500）。
# 誤判定対策:
#   「モンクレール風」「MONCLER系」「マヤ好きへ」等、「ワッペンのみ」「ファーのみ」「タグだけ」「フード用ファー」等の
#   部品だけの出品、キッズ（キッズ・KIDS・子供・ジュニア・ベビー・ANNI、4A〜14A のサイズ表記、ライン 95x の品番）は
#   モデル名と照合しない（マヤ 14A は ¥30,000 前後で大人用の相場と混ぜられないため。アベル・シャーパル・ボエデットの
#   12A・14A は大人も着るため高いが、同じ理由で照合しない）。
#   コラボ（HYKE・sacai・Palm Angels・JW Anderson・Rick Owens・adidas・KITH・UNDEFEATED・Craig Green・
#   Simone Rocha・ALYX・JUNYA WATANABE・visvim 等）は通常品と相場が違うため、フラグメント以外のモデル名と照合しない。
#   短い名前（マヤ・バディ・チブ・ジエ・ランス・イルマ・アベル・アルル・クロエ 等）は直前がカタカナでないこと
#   （「フランス」→ランス、「ウィリアム」→リアム を除く。「モンクレールマヤ」のようにブランド名に続けたものは通す）、
#   直後がカタカナでないこと（「バディア」→バディ、「イルマファー」→イルマ を除く）を条件にする。
#   バジーレ（BAZILLE。ダウン）はサンダルの BASILE と同じカタカナで書かれるため、サンダル・スライドの出品を除く
_MC_NF = r'(?!\s*(?:風|似|タイプ|系|と同じ|と同型|同型|みたい|のよう|っぽ|好き|様|さん|ちゃん|くん))'
_MC_BRAND = r'(?:モンクレール|モンクレ|MONCLER)'
_MC_KIDS = (r'キッズ|KIDS|子供|子ども|こども|ジュニア|JUNIOR|ベビー|BABY|ガールズ|ボーイズ|' + _A + r'ANNI' + _Z
            + r'|(?<![0-9A-Z.\-])(?:[2-9]|1[0-4])A(?![A-Z0-9])'
            + r'|(?<![A-Z0-9])(?:[A-M]|\d{1,2})[12][\s\-]?95\d')            # キッズの品番（F29541A58520 等）
_MC_COLLAB = (r'HYKE|ハイク|SACAI|サカイ|PALM\s*ANGELS|パーム\s*エンジェルス|J\.?\s*W\.?\s*ANDERSON|JWアンダーソン|'
              r'RICK\s*OWENS|リック\s*オウエンス|ADIDAS|アディダス|' + _A + r'KITH' + _Z + r'|UNDEFEATED|'
              r'CRAIG\s*GREEN|クレイグ\s*グリーン|SIMONE\s*ROCHA|シモーネ\s*ロシャ|ALYX|アリクス|JUNYA|ジュンヤ|'
              r'VISVIM|ビズビム|ヴィズヴィム|COMME\s*DES|コムデギャルソン|コム\s*デ\s*ギャルソン')
_MC_NOT_LIKE = (r'(?s)^(?!.*' + _MC_BRAND + r'\s*(?:風|似|タイプ|系|っぽ|好き))'
                r'(?!.*(?:(?:ファー|フード|ワッペン|タグ|ハンガー)\s*(?:のみ|だけ|単体|単品)|(?:フード|ファー)\s*用))'
                r'(?!.*(?:' + _MC_KIDS + r'))')
_MC_NO_COLLAB = r'(?!.*(?:' + _MC_COLLAB + r'))'
_MC_KB = r'(?:(?<![ァ-ヶー])|(?<=モンクレール)|(?<=モンクレ))'      # 直前がカタカナでない（ブランド名の直後は可）
# 直後がカタカナでない（長音は可）。ただし「バディダウン」「マヤブラック」「バディジュボット」のように
# 続けて書かれる語は許す
_MC_KE = (r'(?:(?![ァ-ヶ])|(?=(?:ダウン|ジャケット|ジュ[ボブ]ッ?ト|ジレ|ベスト|コート|パーカ|ブルゾン|'
          r'ブラック|ネイビー|ホワイト|グレー|ベージュ|カーキ|ブラウン|レッド|ブルー)))')
_MC_EA = r'(?:(?<![A-Za-z])|(?<=MONCLER))'
_MC_EZ = r'(?:(?![A-Za-z])|(?=(?:GIUBBOTTO|GILET|JACKET|DOWN|VEST|COAT|PARKA)))'   # MAYAGIUBBOTTO 等
_MC_SHORT = lambda k: _MC_KB + r'(?:' + k + r')' + _MC_KE


def _mc(canon: str, en: str, kana: str, eng: str, cond: str = "",
        collab_ok: bool = False) -> list[tuple[str, str, list[str]]]:
    """モンクレールのモデル。kana / eng: 正規表現（英字は前後に英字が続かないこと）。
    cond: タイトル全体の条件。collab_ok: コラボの出品も照合する（フラグメント）"""
    pats = [r'(?:' + kana + r')' + _MC_NF, _MC_EA + r'(?:' + eng + r')' + _MC_EZ + _MC_NF]
    head = _MC_NOT_LIKE + ("" if collab_ok else _MC_NO_COLLAB) + cond
    # モデル名そのもの（「マヤ」「MAYA 70」）は正規名にする（main.py・app.py が extract_name(モデル名) で確かめるため）
    exact = r'(?i)^\s*(?:' + re.escape(canon) + r'|' + re.escape(en) + r')\s*$'
    return [(canon, en, [head + r'(?=.*?(?:' + p + r'))' for p in pats] + [exact])]


_MONCLER_MODELS: list[tuple[str, str, list[str]]] = [
    # ── コラボ（変更前から登録済み）──
    *_mc("フラグメント", "FRAGMENT", r'フラグメント', r'FRAGMENT|FRGMT', collab_ok=True),            # ダウン 18件 ¥74,500
    # ── 派生モデル（親モデルより先に判定）──
    *_mc("マヤ70", "MAYA 70", _MC_KB + r'マヤ\s*70|[ヴブ]ェゼール', r'MAYA\s*70|VEZERE'),             # 29件 ¥194,600
    *_mc("マヤファー", "MAYAF", _MC_KB + r'マヤ\s*ファー', r'MAYA\s*F(?:UR)?'),                       # 36件 ¥217,450
    *_mc("バディファー", "BADYFUR", _MC_KB + r'バディー?\s*ファー', r'BADY\s*F(?:UR)?'),               # 46件 ¥144,999
    *_mc("ボエディック", "BOEDIC", r'ボエディック', r'BOEDIC'),                                       # 16件 ¥268,800
    *_mc("アボセット", "AVOCETTE", r'ア[ボヴ]ォ?セッ[トテ]', r'AVOCETTE'),                            # 21件 ¥175,000
    *_mc("ミリエロン", "MIRIELON", r'ミリエロン', r'MIRIE[LR]ON'),                                    # 37件 ¥98,000
    *_mc("エルミファー", "HERMIFUR", r'エルミー?\s*ファー', r'HERMIFUR'),                              # 93件 ¥130,000
    # ── メンズ ──
    *_mc("マヤ", "MAYA", _MC_SHORT(r'マヤ'), r'MAYA'),                                                # 182件 ¥120,200
    *_mc("モンジュネーブル", "MONTGENEVRE", r'モンジュネー?[ブヴ]ル?', r'MONT?GENEVRE'),               # 140件 ¥115,000
    *_mc("モンクラー", "MONTCLA", _MC_KB + r'モンクラー?(?!ル)' + _MC_KE, r'MONTCLA'),                 # 114件 ¥120,000
    *_mc("マウレス", "MAURES", r'マウレス|モーレス', r'MAURES'),                                      # 84件 ¥145,500
    *_mc("クルーニー", "CLUNY", r'クルーニー', r'CLUNY'),                                             # 82件 ¥139,000
    *_mc("ブラマント", "BRAMANT", r'ブラマント', r'BRAMANT'),                                         # 49件 ¥115,000
    *_mc("アミオット", "AMIOT", r'アミオット|エミオット|' + _MC_SHORT(r'アミオ'), r'AMIOT'),           # 55件 ¥95,000
    *_mc("ライアン", "RYAN", _MC_SHORT(r'ライアン'), r'RYAN'),                                        # 78件 ¥61,780
    *_mc("ヒマラヤ", "HIMALAYA", r'ヒマラヤ', r'HIMALAYA|HIMARAYA|HYMALAYA?'),                         # 58件 ¥58,700
    *_mc("エベレスト", "EVEREST", r'エベレスト|エヴェレスト', r'EVEREST'),                             # 72件 ¥38,750
    *_mc("カラコルム", "KARAKORUM", r'カラコ[ルラ]ム', r'KARAKORUM'),                                  # 49件 ¥38,999
    *_mc("チブ", "TIB", _MC_SHORT(r'チブ|ティブ'), r'TIBB?'),                                          # 78件 ¥57,500（TIBB を含む）
    *_mc("ギュイ", "GUI", _MC_SHORT(r'ギュイ'), r'GUI'),                                               # 68件 ¥37,000
    *_mc("ブランソン", "BRANSON", r'ブランソン', r'BRANSON'),                                         # 54件 ¥47,750
    *_mc("ゲイリー", "GARY", _MC_SHORT(r'ゲイリー|ギャリー'), r'GARY'),                                # 14件 ¥80,050
    *_mc("マティアス", "MATHIAS", r'マティアス', r'MATHIAS'),                                         # 5件 ¥67,070
    *_mc("アコルス", "ACORUS", r'アコ[ルラ]ス|アルコス', r'ACORUS'),                                   # 39件 ¥55,000
    *_mc("デュボア", "DUBOIS", r'デュ[ボポ]ア', r'DUBOIS'),                                           # 38件 ¥87,000
    *_mc("ランス", "LANS", _MC_SHORT(r'ランス'), r'LANS'),                                             # 29件 ¥67,580（別モデルの REIMS もカタカナではランスと書かれ区別できない）
    *_mc("アルネブ", "ARNEB", r'アルネブ', r'ARNEB'),                                                 # 16件 ¥177,500
    *_mc("シュバリエ", "CHEVALIER", r'シュ[バヴ]ァ?リエ', r'CHEVALIER'),                               # 13件 ¥40,000
    *_mc("モンジュテック", "MONTGETECH", r'モンジュテック', r'MONT?GETECH'),                           # 17件 ¥189,000
    *_mc("バジーレ", "BAZILLE", r'バジーレ|バジール', r'BAZILLE',
         cond=r'(?!.*(?:サンダル|SANDAL|スライド|SLIDE))'),                                         # 44件 ¥42,750
    *_mc("リアム", "LIAM", _MC_SHORT(r'リアム'), r'LIAM'),                                             # 6件 ¥107,100
    *_mc("ボルメス", "BORMES", r'ボルメス|' + _MC_SHORT(r'ボルム'), r'BORMES'),                         # 45件 ¥89,800
    *_mc("トレオンパン", "TREOMPAN", r'トレオンパン|トロンパン', r'TREOMPAN'),                          # 10件 ¥75,490
    *_mc("モンタナ", "MONTANA", r'モンタナ', r'MONTANA'),                                             # 48件 ¥19,350（古いモデル）
    *_mc("ゲルト", "GELT", _MC_SHORT(r'ゲルト'), r'GELT'),                                             # 11件 ¥145,000（ダウンシャツ）
    *_mc("ウォラストン", "WOLLASTON", r'ウォラストン', r'WOLLASTON'),                                  # 8件 ¥150,850
    *_mc("ユベール", "HUBERT", r'ユベール|ヒューベル', r'HUBERT'),                                    # 31件 ¥76,720
    *_mc("ジャコブ", "JACOB", r'ジャコブ|ジェイコブ|' + _MC_SHORT(r'ヤコブ'), r'JACOB'),                # 14件 ¥48,999
    *_mc("グラフトン", "GRAFTON", r'グラフトン', r'GRAFTON'),                                         # 6件 ¥44,400
    *_mc("モントルイユ", "MONTREUIL", r'モント[ルレ]イユ', r'MONTREUIL'),                             # 21件 ¥106,100
    *_mc("アルヴアン", "ARVOUIN", r'アル[ヴブ][アワ]ン', r'ARVOUIN'),                                 # 29件 ¥150,000
    *_mc("タルノス", "TARNOS", r'タルノス', r'TARNOS'),                                               # 16件 ¥115,000
    *_mc("マデイラ", "MADEIRA", r'マデイラ', r'MADEIRA'),                                             # 11件 ¥200,000
    *_mc("オーベール", "AUBERT", r'オーベー?ル', r'AUBERT'),                                           # 16件 ¥51,500
    *_mc("エルブ", "HERBE", _MC_SHORT(r'エルブ'), r'HERBE'),                                           # 31件 ¥130,880
    *_mc("ウィルムス", "WILMS", r'ウィルムス', r'WILMS'),                                             # 18件 ¥105,950
    *_mc("アルマン", "ALLEMAND", _MC_SHORT(r'アルマン|アレマン'), r'ALLEMAND'),                        # 7件 ¥130,000
    *_mc("ガストン", "GASTON", r'ガストン', r'GASTON'),                                               # 11件 ¥47,000
    *_mc("モランデレス", "MORANDIERES", r'モランデ(?:ィエー?ル|レ)ス?', r'MORANDIERES'),                 # 32件 ¥82,950
    *_mc("チベット", "TIBET", _MC_KB + r'チベット', r'TIBET'),                                         # 43件 ¥48,000
    *_mc("ヴァノワーズ", "VANOISE", r'[ヴバ]ァ?ノワーズ', r'VANOISE'),                                 # 31件 ¥100,000
    # ── レディース ──
    *_mc("バディ", "BADY", _MC_SHORT(r'バディー?'), r'BADY'),                                 # 53件 ¥50,000
    *_mc("バディア", "BADIA", r'バディア', r'BADIA'),                                                 # 108件 ¥35,000
    *_mc("エルミンヌ", "HERMINE", r'エルミ[ンー]ヌ', r'HERMINE'),                                      # 130件 ¥110,000
    *_mc("フラメッテ", "FLAMMETTE", r'フラメッ[テト]', r'FLAMMETTE'),                                  # 84件 ¥104,500
    *_mc("ジエ", "GIE", _MC_SHORT(r'ジエ'), r'GIE'),                                                   # 46件 ¥152,500
    *_mc("ガーニー", "GHANY", r'ガーニー', r'GHANY'),                                                 # 46件 ¥43,100
    *_mc("ボエド", "BOED", r'ボエド|' + _MC_SHORT(r'ボエ'), r'BOED'),                                   # 48件 ¥232,500
    *_mc("アヴォーチェ", "AVOCE", r'ア[ヴボ]ォ?ー?チェ', r'AVOCE'),                                     # 23件 ¥199,000
    *_mc("スイエン", "SUYEN", r'スイエン', r'SUYEN'),                                                 # 50件 ¥70,000
    *_mc("タレヴ", "TALEV", r'タレ[ヴブ]', r'TALEVE?'),                                                # 122件 ¥119,450（TALEVE を含む）
    *_mc("アルモワーズ", "ARMOISE", r'アルモワーズ', r'ARMOISE'),                                      # 30件 ¥84,750
    *_mc("ミリエル", "MIRIEL", r'ミリエル', r'MIRIEL'),                                               # 39件 ¥95,000
    *_mc("シャーパル", "CHARPAL", r'シャー?パー?ル', r'CHARPA?L'),                                     # 48件 ¥73,450
    *_mc("バーベル", "BARBEL", r'バーベル(?!\s*ファー)', r'BARBEL'),                                   # 69件 ¥91,000
    *_mc("アベル", "ABELLE", _MC_SHORT(r'アベル'), r'ABELLE'),                                         # 9件 ¥104,900（12A・14A を除く）
    *_mc("リアーヌ", "LIANE", r'リア[ーン][ヌネ]', r'LIANE'),                                           # 46件 ¥38,000
    *_mc("ネセア", "NESEA", r'ネセア', r'NESEA'),                                                     # 31件 ¥80,000
    *_mc("オロフィン", "OROPHIN", r'オロフィン', r'OROPHIN'),                                         # 24件 ¥83,750
    *_mc("イルマ", "HIRMA", _MC_SHORT(r'イルマ'), r'HIRMA'),                                           # 14件 ¥206,500
    *_mc("フルマルス", "FULMARUS", r'フルマ[ルラ]ス', r'FULMARUS'),                                    # 56件 ¥189,050
    *_mc("ロクステル", "LOCUSTELLE", r'ロクステル', r'LOCUSTELLE'),                                    # 4件 ¥260,000
    *_mc("クロエ", "KHLOE", _MC_SHORT(r'クロエ'), r'KHLOE'),                                           # 27件 ¥129,000
    *_mc("ヴーラン", "VOUGLANS", _MC_KB + r'[ヴブ]ーラン', r'VOUGLANS'),                               # 27件 ¥60,000
    *_mc("ロマラン", "ROMARIN", r'ロマラン', r'ROMARIN'),                                             # 11件 ¥59,500
    *_mc("アドクサ", "ADOXA", r'アドクサ', r'ADOXA'),                                                 # 11件 ¥63,500
    *_mc("ベレンジェール", "BERENGERE", r'ベ[レラ]ンジェール', r'BERENGERE'),                           # 15件 ¥27,000
    *_mc("ペトレア", "PETREA", r'ペトレア', r'PETREA'),                                               # 13件 ¥53,500
    *_mc("ヴァレンティーヌ", "VALENTINE", r'[ヴバ]ァ?レンティーヌ', r'VALENTINE'),                       # 69件 ¥25,700
    *_mc("ガーナ", "GHANA", _MC_SHORT(r'ガーナ'), r'GHANA'),                                           # 8件 ¥22,500
    *_mc("デュプレス", "DUPRES", r'デュプレ', r'DUPRES'),                                             # 22件 ¥49,000
    *_mc("カルダミン", "CARDAMINE", r'カルダミン', r'CARDAMINE'),                                      # 68件 ¥132,500
    *_mc("ラセット", "LACET", r'ラセット', r'LACET'),                                                 # 13件 ¥78,500
    *_mc("ジード", "GIDE", _MC_SHORT(r'ジード'), r'GIDE'),                                             # 6件 ¥45,400
    *_mc("メッシーナ", "MESSINA", r'メッシーナ', r'MESSINA'),                                         # 10件 ¥40,450
    *_mc("カーデーレ", "CARDERE", r'カーデーレ', r'CARDERE'),                                         # 63件 ¥158,800
    *_mc("アガテロン", "AGATELON", r'アガテロン', r'AGATELON'),                                       # 10件 ¥84,494
    *_mc("アルル", "ARLES", _MC_SHORT(r'アルル'), r'ARLES'),                                           # 14件 ¥91,645
    *_mc("セリット", "SERITTE", r'セリット', r'SERITTE'),                                             # 10件 ¥94,250
    *_mc("アンジェ", "ANGERS", _MC_SHORT(r'アンジェ'), r'ANGERS'),                                     # 10件 ¥38,465
    *_mc("シトロニエ", "CITRONNIER", r'シトロニエ', r'CITRONNIER'),                                    # 6件 ¥196,500
    *_mc("ヴォージュ", "VOSGES", r'[ヴボ]ォ?ージュ', r'VOSGES'),                                        # 7件 ¥75,000
    *_mc("アルビジア", "ALBIZIA", r'アルビジア', r'ALBIZIA'),                                         # 11件 ¥97,000
    *_mc("ブラベット", "BLAVET", r'ブラベット', r'BLAVET'),                                           # 26件 ¥172,450
]
_SEARCH_VARIANTS["モンクレール"] = {
    "マヤ70": ["VEZERE"],          # 「MONCLER "VEZERE" MAYA70」等（70周年のマヤの品名）
    "チブ": ["TIBB"],              # レディースの TIBB GILET（28件。英字のみのタイトルが多い）
    "タレヴ": ["TALEVE"],          # 「TALEVE」47件（TALEV より多い）
    "マウレス": ["モーレス"],      # カタカナの別表記
}
_STRICT_MODELS["モンクレール"] = [(name, pats) for name, _en, pats in _MONCLER_MODELS]
# 英字名はブランドごとに持つ（モンクレールの ランス=LANS とピレネックスの ランス=REIMS のように、
# 同じカタカナ名で英字名が違うモデルがあるため。_STRICT_EN は全ブランド共通の表）
_STRICT_EN_BY_BRAND: dict[str, dict[str, str]] = {
    "モンクレール": {name: en for name, en, _pats in _MONCLER_MODELS},
}


def _strict_en(brand: str, name: str) -> str:
    return _STRICT_EN_BY_BRAND.get(brand, {}).get(name) or _STRICT_EN[name]


# モンクレールの品番 → モデル名（タイトルにモデル名が無く品番だけの出品も、モデル名の相場で判定するため）。
# 仕入れ先（ベクトルパーク・ヤフオク・ラクマ・トレファク）とメルカリ売り切れのタイトルで、同じ品番に同じモデル名が
# 2回以上書かれ、別のモデル名と書かれたことが無いもの（2026-10 確認。58品番）。品番は _normalize_moncler の形
_MONCLER_NUMBER_NAMES: dict[str, str] = {
    **dict.fromkeys(["4135499", "H10911A00153"], "アコルス"),
    **dict.fromkeys(["4992505"], "アドクサ"),
    **dict.fromkeys(["4030205"], "アミオット"),
    **dict.fromkeys(["4593715"], "アルモワーズ"),
    **dict.fromkeys(["4032685"], "アルル"),
    **dict.fromkeys(["I20911A00001"], "ウォラストン"),
    **dict.fromkeys(["H20931A00093"], "エルブ"),
    **dict.fromkeys(["4930005", "4933905"], "エルミンヌ"),
    **dict.fromkeys(["4985305"], "オロフィン"),
    **dict.fromkeys(["1A52500", "4830105", "4830150", "4830405"], "ガーニー"),
    **dict.fromkeys(["4332105", "4332150", "4336199"], "ギュイ"),
    **dict.fromkeys(["4138025"], "クルーニー"),
    **dict.fromkeys(["4988425"], "クロエ"),
    **dict.fromkeys(["4182005", "4190785"], "ゲイリー"),
    **dict.fromkeys(["L20971G00004"], "ゲルト"),
    **dict.fromkeys(["4937205"], "シャーパル"),
    **dict.fromkeys(["4931949"], "スイエン"),
    **dict.fromkeys(["4936285", "4937085"], "タレヴ"),
    **dict.fromkeys(["4335005"], "チブ"),
    **dict.fromkeys(["4337085"], "デュプレス"),
    **dict.fromkeys(["4131505", "4131585"], "デュボア"),
    **dict.fromkeys(["I20911A00170", "J20911A00107"], "トレオンパン"),
    **dict.fromkeys(["4538450", "4594205"], "バディ"),
    **dict.fromkeys(["4931299"], "バーベル"),
    **dict.fromkeys(["4993105"], "フラメッテ"),
    **dict.fromkeys(["1C56502"], "フルマルス"),
    **dict.fromkeys(["4181149"], "ブラマント"),
    **dict.fromkeys(["4696300"], "ペトレア"),
    **dict.fromkeys(["1B54410"], "マウレス"),
    **dict.fromkeys(["4139480"], "マティアス"),
    **dict.fromkeys(["4036605"], "マヤ"),
    **dict.fromkeys(["4685205"], "ミリエル"),
    **dict.fromkeys(["4999005"], "ミリエロン"),
    **dict.fromkeys(["1B56900"], "モンクラー"),
    **dict.fromkeys(["4033805"], "モンジュネーブル"),
    **dict.fromkeys(["4932800"], "モンタナ"),
    **dict.fromkeys(["4193215"], "ユベール"),
    **dict.fromkeys(["4139285"], "ライアン"),
    **dict.fromkeys(["4537999"], "ランス"),
    **dict.fromkeys(["4090805"], "リアム"),
    **dict.fromkeys(["1A10200", "4830399", "4832105"], "リアーヌ"),
    **dict.fromkeys(["4936505"], "ロマラン"),
    **dict.fromkeys(["4935397"], "ヴァレンティーヌ"),
    **dict.fromkeys(["4994780"], "ヴーラン"),
}

# モデル名の照合をこのジャンルに限るブランド（ルイヴィトンのサングラスにバッグのモデル名を使わない）
_NAME_CATEGORIES = {"ルイヴィトン": "バッグ"}
# モデル名の照合前に全角英数字等を整える（NFKC）ブランド
_NFKC_NAME_BRANDS = {"ルイヴィトン", "ティファニー", "タトラス", "カナダグース", "モンクレール"}

for _brand, _models in _STRICT_MODELS.items():
    _MODEL_NAMES[_brand] = [x for _name, _ in _models for x in (_name, _strict_en(_brand, _name))]

_STRICT_COMPILED = {
    b: [(name, [re.compile(p, re.IGNORECASE) for p in pats]) for name, pats in models]
    for b, models in _STRICT_MODELS.items()
}


# ── 厳密照合ブランドのうち、商品の種別もタイトルで絞るもの ───────────────────
# ヘルノは「ラミナーのダウンコート」だけを対象にする（メルカリ売却済みの調査 2026-10）:
#   ラミナー + ダウン + コート       18件 中央値 ¥39,400  IQR/中央値 0.40（最も安定）
#   ラミナー + ダウン + ジャケット等 22件 中央値 ¥42,800  IQR/中央値 0.88（ばらつきが大きい）
#   ラミナー + コート（ダウンなし）  16件 中央値 ¥22,290  ← ゴアテックスのシェルだけのコートが中心
# そのため「ダウン/DOWN」と「コート/COAT」の両方を必須にする（ダウンなしのゴアテックスコート、
# ダウンジャケット・ダウンベストは対象外）。ベスト・ジレと子供服（8A・130 等のサイズ表記）は除外する。
# 照合はタイトルだけで行う（説明文の「コートの下に…」等で誤って採用しないため。uses_description 参照）
def _herno_rule(title: str) -> bool:
    t = unicodedata.normalize("NFKC", title or "").upper()
    if not re.search(r'ダウン|(?<![A-Z])DOWN(?![A-Z])', t):
        return False
    if not re.search(r'コート|(?<![A-Z])COAT(?![A-Z])', t):
        return False
    if re.search(r'ベスト|ジレ|(?<![A-Z])(?:VEST|GILET)(?![A-Z])', t):
        return False
    # 子供服: キッズ等の語、8A・10A・14A（イタリアの子供服サイズ）、110〜160（cm）
    if re.search(r'キッズ|KIDS|子供|子ども|こども|ジュニア|JUNIOR|ベビー|BABY|ガールズ|ボーイズ'
                 r'|(?<![0-9A-Z.\-])(?:[2-9]|1[0-6])A(?![A-Z])'
                 r'|(?<![0-9.,¥￥\-])1[1-6]0\s*(?:CM|センチ)?(?![0-9.,%万円])', t):
        return False
    return True


_TITLE_RULES = {
    "ヘルノ": _herno_rule,
}
# 種別条件のあるブランドで、モデル名そのもの（「ラミナー」「LAMINAR」）が渡されたときは
# 条件なしで正規名を返す（main.py が extract_name(model) で正規名かどうかを確かめるため）
_STRICT_NAME_KEYS = {
    b: {_k: name for name, _ in _STRICT_MODELS[b] for _k in (name, _strict_en(b, name))}
    for b in _TITLE_RULES
}


# 型番の相場が取れないときに、同じ出品のモデル名で相場を取り直すブランド（main.py）。
# タトラスは品番が売却済みのタイトルに載ることが少なく（約3%）、品番で検索すると0件になりやすいため。
# カナダグースも同様（品番が載るのは約13%。3438JM・3438M・3438MB のように記号違いの品番が多い）。
# モンクレールも同様（品番が載るのは約1%。シーズンごとに品番の頭の記号が変わる）
_NAME_FALLBACK_BRANDS = {"タトラス", "カナダグース", "モンクレール"}


def name_fallback(brand: str) -> bool:
    return brand in _NAME_FALLBACK_BRANDS


def uses_description(brand: str) -> bool:
    """タイトルで型番・モデル名が取れなかった出品に、商品説明文での照合を使うか。
    種別までタイトルで絞るブランド（ヘルノ）は使わない"""
    return brand not in _TITLE_RULES


# 写真から品番を読む機能（photo_reader）で Claude に見せる品番の例。
# 各ブランドの正規表現（_PATTERNS）・ジャンル別の照合（_CATEGORY_EXTRACTORS）のコメントにある実例から選んだもの
_NUMBER_EXAMPLES: dict = {
    "IWC": ["IW325101", "IW510101"],
    "オメガ": ["210.30.42.20.01.001", "2531.80", "311.30.42"],
    "タグホイヤー": ["CAR2111.BA0724", "CAR2111", "WAY2110"],
    "カルティエ": ["WSTA0002", "W69009Z3", "CRQZ0020"],
    "ブライトリング": ["A13356121B1A1", "A17321"],
    "パネライ": ["PAM00312", "PAM01564"],
    "レイバン": ["RB2140", "RB2140F", "RB3025"],
    "グッチ": ["GG0061S"],
    ("グッチ", "時計"): ["YA126402", "YA1264155", "1500L", "5500M"],
    ("グッチ", "サングラス"): ["GG0061S", "GG0022S"],
    "セリーヌ": ["CL40046U"],
    ("セリーヌ", "サングラス"): ["CL40046U", "CL40034F"],
    "プラダ": ["SPR17W", "SPR06W"],
    ("プラダ", "サングラス"): ["SPR17W", "PR 17WS", "SPR14G", "SPS03Y"],
    ("ミュウミュウ", "サングラス"): ["SMU01T", "MU 09WS"],
    ("ルイヴィトン", "サングラス"): ["Z1578E", "Z0165U"],
    ("シャネル", "サングラス"): ["5417-A", "4017-D", "CH5380"],
    ("オークリー", "サングラス"): ["OO9102", "OO9244-02"],
    ("トムフォード", "サングラス"): ["FT0237", "TF237", "FT0237-F"],
    ("バーバリー", "サングラス"): ["BE4216", "B 4216"],
    ("クリスチャンディオール", "サングラス"): ["0049/S", "CD2289"],
    ("ヴェルサーチ", "サングラス"): ["VE4361", "MOD.4361", "MOD.424"],
    ("ロエベ", "サングラス"): ["LW40001I", "SLW384"],
    ("バレンシアガ", "サングラス"): ["BB0096S", "BB0102SA"],
    ("ジャンポールゴルチエ", "サングラス"): ["56-1271", "55-7109"],
    ("EYEVAN", "サングラス"): ["7285-319", "7285-770"],
    "ルイヴィトン": ["Z1502W"],
    ("ルイヴィトン", "バッグ"): ["M40995", "N41358", "M41526"],
    "シャネル": ["5380", "CH5380"],
    "オークリー": ["OO9102"],
    "トムフォード": ["TF237", "TF5401"],
    "バーバリー": ["BE4216"],
    "クリスチャンディオール": ["CD3182S", "0175S"],
    "カナダグース": ["2409L", "3426M", "3438JM", "2302JL", "3804MB"],
    "モンクレール": ["D20914036605 68950", "132-091-40366-05", "4036605 68950", "H20911A00001 595ZZ", "F20931A53901"],
    "ピレネックス": ["HMS011", "HWY012", "HMI055"],
    "タトラス": ["LTA13A4301", "MTA15S4468", "MTK20A4201", "MTAT21A4754-D", "LTA2BM4694", "MTA-460-12"],
    "ノースフェイス": ["ND91950", "NDW91952", "NP62130"],
}


def has_number_format(brand: str, category: str = "") -> bool:
    """正規表現・ジャンル別の照合で型番（品番）を判定できるブランドか（写真からの品番読み取りの対象）。
    モデル名だけで照合するブランド（モンクレール等）は False"""
    return (brand, category) in _CATEGORY_EXTRACTORS or bool(_PATTERNS.get(brand))


def is_number(model: str | None, brand: str) -> bool:
    """extract() の結果が型番（品番）か。モデル名（ポリテアマ・スピーディ30 等）なら False"""
    return bool(model) and extract_name(model, brand) != model


def number_examples(brand: str, category: str = "") -> list[str]:
    """写真から品番を読むときに Claude に見せる、このブランドの品番の例"""
    return (_NUMBER_EXAMPLES.get((brand, category)) or _NUMBER_EXAMPLES.get(brand)
            or KNOWN_MODEL_NUMBERS.get(brand, [])[:5])

# レイバンの代表型番（型番 → 愛称）。並びは画面の表示順（相場の高さ・売却件数の多い順）
# 2026-11 拡充（39件 → 下の表）: メルカリ売り切れ（ログインなし）をレイバンの検索語22種×最大10ページ集め、
# タイトル・説明文・写真のOCR（Mac内・無料）で型番が取れたもののうち2件以上売れている型番を追加した。
# 並びは相場の中央値が高い順、コメントは「件数・¥中央値」（2026-11-08 集計）。メガネの RB5xxx〜7xxx・RX は入れない。
# W0365 等はボシュロム（B&L）期のヴィンテージの型番
_RAYBAN_MODELS.update({
    "RB4335": "",                           # 2件・¥31,000
    "RB4376": "",                           # 2件・¥15,999
    "RB0316S": "",                          # 2件・¥14,400
    "RB3119M": "オリンピアン",                    # 3件・¥14,300
    "RB3547N": "",                          # 2件・¥13,550
    "RB3565": "ジャック",                       # 23件・¥12,999
    "RB4430F": "",                          # 3件・¥12,000
    "RB3636": "",                           # 2件・¥12,000
    "W1116": "",                            # 3件・¥11,900
    "RB2213": "",                           # 3件・¥11,500
    "RB4277F": "",                          # 4件・¥11,250
    "RB4264": "",                           # 2件・¥11,250
    "RB2016": "",                           # 2件・¥11,000
    "RB3397": "",                           # 2件・¥10,900
    "RB4392D": "",                          # 11件・¥10,500
    "RB3681": "",                           # 4件・¥10,500
    "RB4089": "バロラマ",                       # 3件・¥10,490
    "RB3447N": "ラウンドメタル",                   # 4件・¥10,099
    "RB2140F": "ウェイファーラー",                  # 54件・¥10,000
    "RB4391D": "",                          # 15件・¥9,800
    "RB4379D": "",                          # 4件・¥9,750
    "RB4258": "",                           # 1件・¥9,580
    "RB4401D": "",                          # 8件・¥9,250
    "RB3539": "エリカメタル",                     # 71件・¥9,000
    "RB2132F": "ニューウェイファーラー",               # 29件・¥9,000
    "RB3183": "",                           # 13件・¥9,000
    "RB3548N": "ヘキサゴナル",                    # 11件・¥9,000
    "RB3724D": "",                          # 2件・¥9,000
    "RB3682F": "",                          # 2件・¥8,935
    "RB4439D": "",                          # 4件・¥8,800
    "RB4461D": "",                          # 4件・¥8,750
    "W0366": "",                            # 2件・¥8,750
    "RB4387": "",                           # 2件・¥8,500
    "RB3136": "キャラバン",                      # 9件・¥8,200
    "RB4333D": "",                          # 6件・¥8,200
    "RB4259F": "",                          # 132件・¥8,000
    "RB4258F": "",                          # 78件・¥8,000
    "RB4105": "フォールディングウェイファーラー",           # 6件・¥8,000
    "W0365": "",                            # 3件・¥8,000
    "RB3716": "クラブマスターメタル",                 # 3件・¥8,000
    "RB2139": "",                           # 2件・¥7,950
    "RB3542": "",                           # 4件・¥7,900
    "RB3561": "",                           # 2件・¥7,850
    "RB4423D": "",                          # 3件・¥7,800
    "RB3154": "",                           # 3件・¥7,800
    "RB3647N": "ダブルブリッジ",                   # 16件・¥7,750
    "RB8301": "",                           # 3件・¥7,700
    "RB3119": "オリンピアン",                     # 10件・¥7,600
    "RB3447": "ラウンドメタル",                    # 23件・¥7,500
    "RB4334D": "",                          # 14件・¥7,500
    "RB3647": "ダブルブリッジ",                    # 5件・¥7,500
    "RB2140A": "ウェイファーラー",                  # 4件・¥7,500
    "RB3592": "",                           # 3件・¥7,500
    "RB3648M": "",                          # 2件・¥7,500
    "RB2183": "",                           # 3件・¥7,200
    "RB4418D": "",                          # 2件・¥7,100
    "RB2210F": "",                          # 2件・¥7,027
    "RB4253": "",                           # 5件・¥7,000
    "RB4261D": "",                          # 3件・¥6,990
    "RB4348D": "",                          # 3件・¥6,981
    "RB4175": "",                           # 2件・¥6,976
    "RB4171F": "エリカ",                       # 76件・¥6,900
    "RB2447": "",                           # 1件・¥6,900
    "RB4260D": "",                          # 6件・¥6,700
    "RB2241F": "",                          # 2件・¥6,650
    "RB3016": "クラブマスター",                    # 53件・¥6,500
    "RB2140": "ウェイファーラー",                   # 19件・¥6,500
    "RB3387": "",                           # 2件・¥6,400
    "W1946": "",                            # 2件・¥6,388
    "RB4165F": "ジャスティン",                    # 70件・¥6,300
    "RB2132": "ニューウェイファーラー",                # 10件・¥6,250
    "RB2177D": "",                          # 2件・¥6,250
    "RB4171": "エリカ",                        # 26件・¥6,221
    "RB3691": "",                           # 2件・¥6,040
    "RB4256F": "",                          # 2件・¥6,010
    "RB3025": "アビエーター",                     # 38件・¥6,000
    "RB2180": "",                           # 3件・¥6,000
    "RB3548": "ヘキサゴナル",                     # 3件・¥6,000
    "RB4259": "",                           # 4件・¥5,990
    "RB4165": "ジャスティン",                     # 20件・¥5,950
    "RB3502": "",                           # 2件・¥5,950
    "RB4306F": "",                          # 3件・¥5,900
    "RB3225": "",                           # 2件・¥5,890
    "RB4187": "クリス",                        # 9件・¥5,800
    "RB3016F": "クラブマスター",                   # 8件・¥5,749
    "RB4187F": "クリス",                       # 70件・¥5,550
    "RB4305F": "",                          # 5件・¥5,500
    "RB3155": "",                           # 2件・¥5,500
    "RB3123": "",                           # 2件・¥5,400
    "RB4101F": "",                          # 2件・¥5,400
    "RB3476": "",                           # 2件・¥5,165
    "RB2180F": "",                          # 28件・¥5,050
    "RB4181F": "",                          # 2件・¥5,050
    "RB3362": "コックピット",                     # 7件・¥5,000
    "RB4246": "クラブラウンド",                    # 5件・¥5,000
    "RB3179": "",                           # 5件・¥5,000
    "RB3498": "",                           # 3件・¥5,000
    "RB2156": "",                           # 3件・¥5,000
    "RB3174": "",                           # 2件・¥4,949
    "RB4147": "",                           # 6件・¥4,850
    "RB3682": "",                           # 2件・¥4,833
    "W3232": "",                            # 2件・¥4,800
    "RB3026": "アビエーター ラージメタルII",            # 3件・¥4,750
    "RB3509": "",                           # 2件・¥4,677
    "RB3578": "",                           # 2件・¥4,600
    "RB3386": "",                           # 31件・¥4,500
    "RB3612D": "",                          # 5件・¥4,500
    "RB4184F": "",                          # 2件・¥4,250
    "W2544": "",                            # 2件・¥4,226
    "RB3396": "",                           # 3件・¥4,200
    "RB3164": "",                           # 2件・¥3,950
    "RB3149": "",                           # 4件・¥3,940
    "RB3327": "",                           # 2件・¥3,890
    "RB4118": "",                           # 3件・¥3,800
    "RB4278": "",                           # 3件・¥3,600
    "RB3150": "",                           # 2件・¥3,600
    "RB2176": "クラブマスターフォールディング",            # 1件・¥3,600
    "RB3549": "",                           # 2件・¥3,500
    "RB3177": "",                           # 2件・¥3,499
    "RB3186": "",                           # 2件・¥3,066
    "RB3395": "",                           # 2件・¥2,989
    "RB3689": "アビエーター",                     # 売り切れ集計で0件（2026-10 登録分）
    "RB4305": "",                           # 売り切れ集計で0件（2026-10 登録分）
    "RB4306": "",                           # 売り切れ集計で0件（2026-10 登録分）
    "RB4195": "",                           # 売り切れ集計で0件（2026-10 登録分）
})
# 「RB」が付かない 2140 / 2140F は代表型番に限って拾う（年・価格・サイズ等の4桁と区別するため、
# 前後が数字・英字・小数点・桁区切り・円/年でないこと）
# 2026-11: 版違いをハイフンで区切る表記（4258-F）も拾う。1900〜2099 は年と区別できないため数字だけでは拾わない
# （RB2016・RB1970 等は「RB」付きの表記だけ）。0 始まりの新しい型番（RB0840S）も「RB」付きの表記だけ
_PATTERNS["レイバン"].append(
    r'(?<![A-Za-z0-9.,¥￥\-/])(?:'
    + "|".join(sorted({m[2:6] for m in _RAYBAN_MODELS
                       if m.startswith("RB") and m[2] != "0" and not 1900 <= int(m[2:6]) <= 2099}))
    + r')(?:-?[A-Z])?(?![A-Za-z0-9.,円年\-/])'
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


# ── グッチのサングラス: 仕入れ対象の代表型番と愛称（メルカリ売却済みで相場が付くもの。2026-10 確認）──
# 型番の形式（フレーム内側の刻印。メルカリの表記ゆれ: GG0061S / GG 0061S / gg0061s / GG-0061S /
# GG0637SK 001 / GG0637SK-001 / GG0637SK001 のようにカラー番号が続くもの）:
#   現行品: GG + 4桁 + S（GG0061S）。末尾の K/A/N（SK・SA=アジアンフィット、SN 等）は別型番として扱う
#           （レイバンの F 付きと同じ。メルカリ相場で GG0061S と GG0061SK 等の差があるため。下の表参照）
#   旧型:   「GG 2409/N/S」「GG 1799/S」「GG3525/K/S」（スラッシュ区切り）。旧型は /N/S・/F/S・/K/S 等の
#           版違いで相場差がはっきりせず、表記もばらつく（GG2409/N/S・GG 2409 N/S・GG2409NS）ため、
#           GG + 4桁（GG2409）にまとめる。2000〜9999 番台は旧型だけなので常に4桁にまとめる。
#   O / OA / OJ 等（GG1470OJ）はメガネ（光学フレーム）の型番なので、サングラスでは型番として扱わない。
# 並びは画面の表示順（メルカリ相場の中央値が高い順）
# 2026-11 拡充（38件 → 下の表）: メルカリ売り切れをグッチのサングラスの検索語15種×最大10ページ集め、タイトル・説明文・
# 写真のOCRで型番が取れたもののうち2件以上売れている型番を追加（コメントは「件数・¥中央値」、2026-11-08 集計）
_GUCCI_SUN_MODELS: dict[str, str] = {}   # 型番 → 愛称・シェイプ名（無いものは空文字）。下で設定
_GUCCI_SUN_MODELS.update({
    "GG1470S": "",                          # 2件・¥34,000
    "GG1089SA": "チェーン",                     # 3件・¥30,000
    "GG1661S": "",                          # 2件・¥29,500
    "GG2118": "",                           # 2件・¥27,750
    "GG1826SK": "",                         # 2件・¥26,950
    "GG1662SA": "オーバル",                     # 3件・¥26,900
    "GG0818SA": "",                         # 6件・¥23,000
    "GG0396S": "スクエア",                      # 6件・¥22,000
    "GG1279S": "",                          # 2件・¥21,500
    "GG1157": "オーバル・旧型",                    # 6件・¥20,250
    "GG1498SK": "",                         # 1件・¥20,000
    "GG1142SA": "",                         # 2件・¥19,550
    "GG1690": "アンダーテンプル・旧型",                # 3件・¥19,000
    "GG1180SK": "",                         # 2件・¥18,249
    "GG0225S": "",                          # 3件・¥18,000
    "GG0765SA": "",                         # 1件・¥18,000
    "GG0065SK": "",                         # 2件・¥17,805
    "GG2457": "",                           # 4件・¥17,400
    "GG0852SK": "",                         # 3件・¥17,000
    "GG2425": "",                           # 2件・¥16,750
    "GG1339SK": "ボストン",                     # 7件・¥16,500
    "GG0918S": "ホースビット",                    # 3件・¥16,500
    "GG0370SK": "",                         # 2件・¥16,499
    "GG0022S": "ビー（蜂）",                     # 2件・¥16,250
    "GG1023S": "",                          # 3件・¥16,000
    "GG1071S": "",                          # 3件・¥16,000
    "GG0854SK": "",                         # 4件・¥15,999
    "GG3644": "",                           # 2件・¥15,650
    "GG0337S": "",                          # 2件・¥15,500
    "GG1784": "",                           # 3件・¥15,000
    "GG1818SK": "",                         # 3件・¥14,999
    "GG0830SK": "",                         # 2件・¥14,900
    "GG0544S": "",                          # 2件・¥14,899
    "GG3046": "",                           # 2件・¥14,600
    "GG1582SK": "",                         # 2件・¥14,200
    "GG0814SK": "",                         # 2件・¥14,100
    "GG0637SK": "スクエア GGロゴ",                # 27件・¥14,000
    "GG1073SK": "GGロゴ",                     # 4件・¥14,000
    "GG0371SK": "",                         # 2件・¥13,944
    "GG2456": "",                           # 9件・¥13,900
    "GG0950SA": "",                         # 2件・¥13,850
    "GG0022SA": "スクエア",                     # 2件・¥13,820
    "GG3726": "",                           # 3件・¥13,800
    "GG2461": "",                           # 2件・¥13,750
    "GG0077SK": "シェリーライン",                  # 6件・¥13,600
    "GG2900": "",                           # 4件・¥13,500
    "GG1550SK": "",                         # 4件・¥13,500
    "GG0206SK": "",                         # 2件・¥13,500
    "GG1338SK": "",                         # 2件・¥13,500
    "GG1430SK": "",                         # 3件・¥13,499
    "GG1158SK": "",                         # 2件・¥13,499
    "GG1825": "",                           # 2件・¥13,290
    "GG0746S": "",                          # 2件・¥13,250
    "GG2402": "",                           # 2件・¥13,000
    "GG0518S": "",                          # 1件・¥13,000
    "GG2424": "",                           # 3件・¥12,900
    "GG3098": "",                           # 3件・¥12,900
    "GG0746SA": "",                         # 2件・¥12,650
    "GG1426S": "",                          # 2件・¥12,600
    "GG0586SA": "",                         # 2件・¥12,500
    "GG0638SK": "",                         # 2件・¥12,000
    "GG0076SK": "",                         # 2件・¥12,000
    "GG3173": "",                           # 2件・¥11,900
    "GG0677SK": "",                         # 2件・¥11,500
    "GG0154SA": "",                         # 4件・¥11,490
    "GG2989": "",                           # 2件・¥11,400
    "GG1673": "",                           # 2件・¥11,400
    "GG3659": "",                           # 2件・¥11,390
    "GG0960SA": "",                         # 2件・¥11,305
    "GG1715": "リムレス・旧型",                    # 4件・¥11,250
    "GG0044SA": "",                         # 2件・¥11,155
    "GG0416SK": "",                         # 2件・¥11,080
    "GG3525": "ハート・旧型",                     # 11件・¥11,000
    "GG0636SK": "",                         # 7件・¥11,000
    "GG1622": "",                           # 6件・¥11,000
    "GG3174": "",                           # 4件・¥11,000
    "GG0266SA": "",                         # 2件・¥10,950
    "GG2418": "",                           # 3件・¥10,900
    "GG0369S": "",                          # 2件・¥10,800
    "GG0001SN": "",                         # 2件・¥10,750
    "GG2430": "",                           # 2件・¥10,750
    "GG0061S": "",                          # 10件・¥10,749
    "GG0079SK": "シェリーライン",                  # 2件・¥10,527
    "GG1000SK": "",                         # 6件・¥10,500
    "GG3660": "",                           # 6件・¥10,500
    "GG2455": "",                           # 3件・¥10,500
    "GG0057SK": "",                         # 3件・¥10,500
    "GG1710": "",                           # 2件・¥10,500
    "GG0714SA": "",                         # 2件・¥10,500
    "GG1153": "",                           # 2件・¥10,500
    "GG3172": "",                           # 2件・¥10,499
    "GG2739": "",                           # 3件・¥10,480
    "GG1640": "",                           # 3件・¥10,000
    "GG2515": "",                           # 3件・¥10,000
    "GG3113": "",                           # 3件・¥9,900
    "GG0263S": "",                          # 4件・¥9,845
    "GG2547": "",                           # 2件・¥9,750
    "GG1001SK": "",                         # 5件・¥9,600
    "GG3595": "",                           # 2件・¥9,590
    "GG1711": "",                           # 2件・¥9,500
    "GG2413": "",                           # 9件・¥9,400
    "GG1175SK": "",                         # 3件・¥9,380
    "GG2935": "",                           # 2件・¥9,240
    "GG1346SK": "",                         # 5件・¥9,200
    "GG3712": "",                           # 2件・¥9,045
    "GG2409": "スクエア・旧型",                    # 7件・¥9,000
    "GG2432": "",                           # 6件・¥8,999
    "GG0404S": "",                          # 4件・¥8,994
    "GG0082SK": "",                         # 2件・¥8,930
    "GG0261SA": "",                         # 2件・¥8,890
    "GG1663": "",                           # 3件・¥8,800
    "GG3178": "",                           # 4件・¥8,750
    "GG2969": "",                           # 5件・¥8,700
    "GG2419": "",                           # 4件・¥8,694
    "GG2378": "",                           # 2件・¥8,600
    "GG1714": "",                           # 6件・¥8,500
    "GG0080SK": "",                         # 5件・¥8,500
    "GG2151": "",                           # 2件・¥8,500
    "GG2407": "",                           # 4件・¥8,475
    "GG3026": "",                           # 2件・¥8,400
    "GG3732": "",                           # 2件・¥8,277
    "GG1691": "",                           # 2件・¥8,250
    "GG1420": "",                           # 3件・¥8,100
    "GG2415": "",                           # 2件・¥8,090
    "GG1927": "",                           # 3件・¥8,000
    "GG1709": "",                           # 3件・¥8,000
    "GG0081SK": "",                         # 2件・¥8,000
    "GG0002S": "",                          # 2件・¥8,000
    "GG2801": "",                           # 3件・¥7,980
    "GG3792": "",                           # 4件・¥7,950
    "GG1799": "",                           # 12件・¥7,945
    "GG1423": "",                           # 7件・¥7,800
    "GG0081": "",                           # 2件・¥7,799
    "GG1371": "",                           # 2件・¥7,747
    "GG1552": "",                           # 2件・¥7,700
    "GG0404SA": "",                         # 2件・¥7,650
    "GG1801": "",                           # 2件・¥7,600
    "GG2598": "",                           # 2件・¥7,600
    "GG0091S": "",                          # 3件・¥7,500
    "GG3550": "",                           # 2件・¥7,500
    "GG2827": "",                           # 2件・¥7,500
    "GG2991": "",                           # 2件・¥7,500
    "GG1638": "",                           # 3件・¥7,470
    "GG1155": "",                           # 2件・¥7,300
    "GG2400": "",                           # 4件・¥7,250
    "GG2913": "",                           # 2件・¥7,240
    "GG2865": "",                           # 2件・¥7,220
    "GG3593": "",                           # 2件・¥7,150
    "GG2451": "",                           # 3件・¥7,100
    "GG1641": "",                           # 2件・¥7,070
    "GG1488": "",                           # 3件・¥7,000
    "GG2195": "",                           # 2件・¥7,000
    "GG0763S": "",                          # 3件・¥6,980
    "GG1192": "",                           # 2件・¥6,964
    "GG0595S": "",                          # 2件・¥6,900
    "GG2768": "",                           # 2件・¥6,800
    "GG2782": "",                           # 2件・¥6,750
    "GG1775": "",                           # 2件・¥6,700
    "GG1797": "",                           # 4件・¥6,625
    "GG1838": "",                           # 6件・¥6,570
    "GG0271SA": "",                         # 2件・¥6,540
    "GG1689": "",                           # 2件・¥6,540
    "GG2417": "",                           # 4件・¥6,500
    "GG1674": "",                           # 2件・¥6,465
    "GG2743": "",                           # 8件・¥6,400
    "GG1794": "",                           # 2件・¥6,345
    "GG1827": "",                           # 4件・¥6,250
    "GG3730": "",                           # 2件・¥6,250
    "GG1777": "",                           # 2件・¥6,250
    "GG1614": "",                           # 6件・¥6,240
    "GG2609": "",                           # 2件・¥6,110
    "GG0321S": "",                          # 2件・¥6,100
    "GG2744": "",                           # 3件・¥6,000
    "GG2951": "",                           # 5件・¥5,980
    "GG1615": "",                           # 2件・¥5,950
    "GG2602": "",                           # 2件・¥5,900
    "GG2412": "",                           # 2件・¥5,849
    "GG2411": "",                           # 7件・¥5,800
    "GG1653": "",                           # 3件・¥5,800
    "GG1643": "",                           # 2件・¥5,695
    "GG2767": "",                           # 2件・¥5,590
    "GG2766": "",                           # 4件・¥5,500
    "GG2657": "",                           # 2件・¥5,315
    "GG3145": "",                           # 2件・¥5,150
    "GG2410": "",                           # 2件・¥4,900
    "GG0382S": "",                          # 2件・¥4,420
    "GG2408": "",                           # 2件・¥4,265
    "GG3500": "",                           # 3件・¥4,200
    "GG2401": "",                           # 3件・¥3,800
    "GG1403SK": "",                         # 売り切れ集計で0件（2026-10 登録分）
    "GG1169S": "",                          # 売り切れ集計で0件（2026-10 登録分）
    "GG0546SK": "",                         # 売り切れ集計で0件（2026-10 登録分）
})

_GUCCI_SUN_RE = re.compile(
    # GG + 4桁、続いて旧型の「/N/S」「\S」「|S」、または現行品の末尾（S・SK・SA・O・OA 等）
    r'(?<![A-Za-z0-9])GG[\s\-]?(\d{4})(?!\d)((?:\s?[/\\|]\s?[A-Z](?![A-Za-z]))*)([A-Z]{0,2})(?![A-Za-z])',
    re.IGNORECASE)
# 4桁の数字 → 代表型番（GG0061 のように末尾 S 等を省いた表記を、版違いが1つしかない代表型番に寄せる）
_GUCCI_SUN_BY_NUM: dict[str, list[str]] = {}
for _m in _GUCCI_SUN_MODELS:
    _GUCCI_SUN_BY_NUM.setdefault(_m[2:6], []).append(_m)
# 「GG」が付かない数字だけの表記（0637SK）は、現行品の代表型番に限り、末尾の S まで書かれているときだけ拾う
# （年・価格・サイズ等の数字や、他ブランドの型番と誤認しないため）
_GUCCI_SUN_BARE_RE = re.compile(
    r'(?<![A-Za-z0-9.,¥￥\-/])('
    + "|".join(sorted((m[2:] for m in _GUCCI_SUN_MODELS if len(m) > 6 and m[6] == "S"),
                      key=len, reverse=True))
    + r')(?![A-Za-z0-9])',
    re.IGNORECASE)


def _gucci_sun_key(num: str, slash: str, suf: str) -> str | None:
    suf = suf.upper()
    if slash or num[0] not in "01":
        # 旧型（GG 2409/N/S・GG1799/S）は4桁にまとめる。0番台にスラッシュが付く表記（GG0061/S）は現行品
        if num[0] == "0":
            suf = re.sub(r'[^A-Z]', '', slash.upper()) + suf
        else:
            return "GG" + num
    if suf.startswith("O"):
        return None                 # メガネ（光学フレーム）の型番
    cands = _GUCCI_SUN_BY_NUM.get(num, [])
    if cands == ["GG" + num]:
        return cands[0]             # 代表型番が旧型だけの番号（GG1799S・GG1690 S 等の表記も旧型にまとめる）
    if suf.startswith("S"):
        return "GG" + num + (suf[:2] if len(suf) >= 2 and suf[1] in "AKN" else "S")
    if not suf:
        # 末尾なし（GG0061 / GG1799）: 版違いが1つだけの代表型番ならそれに寄せる
        if len(cands) == 1:
            return cands[0]
    # 末尾なし、または旧型の版違い（GG1620J・GG1927F 等）は4桁にまとめる
    return "GG" + num


def _extract_gucci_sunglasses(title: str) -> str | None:
    """グッチのサングラスの型番（GG0061S / GG0637SK / 旧型 GG2409）。時計の YA126402 等は見ない"""
    t = unicodedata.normalize("NFKC", title or "")
    for m in _GUCCI_SUN_RE.finditer(t):
        key = _gucci_sun_key(m.group(1), m.group(2), m.group(3))
        if key:
            return key
    m = _GUCCI_SUN_BARE_RE.search(t)
    if m:
        return "GG" + m.group(1).upper()
    return None

# ── セリーヌのサングラス: 仕入れ対象の代表型番と愛称（メルカリ売却済みで相場が付くもの。2026-10 確認）──
# 型番の形式（メルカリの表記ゆれ: CL40194U / CL 40194U / cl40194u / CL-40194U / CL40194U 01A /
# CL40269U01A のようにカラー番号が続くもの）:
#   現行品: CL + 5桁 + U/I/F（CL40194U・CL40233I・CL40061F）。末尾の1文字は別型番として扱う
#           （レイバンの F 付きと同じ扱い。F=アジアンフィット）。「CL402321」のように I を 1 と書いた表記は、
#           代表型番に CL40232I があるときだけ I として扱う
#           4桁 + 2文字の旧い現行品（CL4002UN・CL4003IN）も同じ扱い
#   CL50xxx（CL50049I 等）はメガネ（光学フレーム）の型番なので、サングラスでは型番として扱わない
#   旧型:   CL 41xxx/S・CL41061/F/S（フィービー期）。/S・/F/S 等の版違いは流通が少なく相場差もはっきりしないため、
#           CL + 5桁（CL41373）にまとめる
# 並びは画面の表示順（メルカリ相場の中央値が高い順）
# 2026-11 拡充（24件 → 下の表）: メルカリ売り切れをセリーヌのサングラスの検索語10種×最大10ページ集め、タイトル・説明文・
# 写真のOCRで型番が取れたもののうち2件以上売れている型番を追加（コメントは「件数・¥中央値」、2026-11-08 集計）
_CELINE_SUN_MODELS: dict[str, str] = {}   # 型番 → 愛称（無いものは空文字）。下で設定
_CELINE_SUN_MODELS.update({
    "CL40198F": "",                         # 6件・¥35,666
    "CL40273U": "",                         # 2件・¥35,500
    "CL40253F": "",                         # 7件・¥35,000
    "CL40194U": "トリオンフ",                    # 39件・¥33,000
    "CL40235U": "",                         # 3件・¥32,800
    "CL40282U": "トリオンフ",                    # 16件・¥30,606
    "CL40238U": "",                         # 3件・¥28,800
    "CL40308U": "",                         # 2件・¥26,500
    "CL40249U": "",                         # 2件・¥23,500
    "CL40193I": "",                         # 3件・¥23,000
    "CL40061F": "",                         # 4件・¥21,650
    "CL40232I": "",                         # 8件・¥21,250
    "CL40057F": "",                         # 3件・¥20,000
    "CL40100U": "",                         # 3件・¥20,000
    "CL40248I": "",                         # 1件・¥20,000
    "CL4002UN": "",                         # 8件・¥19,150
    "CL40233I": "",                         # 5件・¥19,000
    "CL40034F": "ボストン",                     # 3件・¥19,000
    "CL41031": "フィービー期",                    # 2件・¥18,900
    "CL4003IN": "",                         # 4件・¥16,150
    "CL40197U": "",                         # 4件・¥16,000
    "CL41394": "フィービー期",                    # 2件・¥15,550
    "CL41061": "フィービー期",                    # 2件・¥14,200
    "CL41090": "フィービー期",                    # 2件・¥13,500
    "CL40011U": "",                         # 2件・¥12,500
    "CL41755": "キャットアイ・フィービー期",             # 2件・¥10,550
    "CL41440": "フィービー期",                    # 1件・¥9,600
    "CL40223F": "",                         # 2件・¥8,850
    "CL41732": "フィービー期",                    # 4件・¥8,820
    "CL41373": "",                          # 3件・¥8,200
    "CL41040": "",                          # 2件・¥7,777
    "CL41053": "",                          # 2件・¥6,000
    "CL41080": "",                          # 2件・¥5,800
})

_CELINE_SUN_RE = re.compile(
    r'(?<![A-Za-z0-9])CL[\s\-]?(?:'
    # 5桁: 「CL402321」（I を 1 と書いた表記）、または旧型の「/S」「/F/S」・現行品の末尾 U/I/F
    # （カラー番号 01A 等が続いてもよい）
    r'(\d{5})(?:(1)(?![0-9A-Za-z])|(?!\d)((?:\s?/\s?[A-Z]{1,2}(?![A-Za-z]))*)(?:([A-Z])(?![A-Za-z]))?)'
    # 4桁 + 2文字（CL4002UN）
    r'|(\d{4})([UIF][A-Z])(?![A-Za-z])'
    r')',
    re.IGNORECASE)
_CELINE_SUN_BY_NUM: dict[str, list[str]] = {}
for _m in _CELINE_SUN_MODELS:
    _CELINE_SUN_BY_NUM.setdefault(re.match(r'CL(\d+)', _m).group(1), []).append(_m)
# 「CL」が付かない数字だけの表記（40194U）は、現行品の代表型番に限り、末尾の文字まで書かれているときだけ拾う
_CELINE_SUN_BARE_RE = re.compile(
    r'(?<![A-Za-z0-9.,¥￥\-/])('
    + "|".join(sorted((m[2:] for m in _CELINE_SUN_MODELS if m[-1].isalpha()), key=len, reverse=True))
    + r')(?![A-Za-z0-9])',
    re.IGNORECASE)


def _extract_celine_sunglasses(title: str) -> str | None:
    """セリーヌのサングラスの型番（CL40194U / CL4002UN / 旧型 CL41373）"""
    t = unicodedata.normalize("NFKC", title or "")
    for m in _CELINE_SUN_RE.finditer(t):
        if m.group(5):                               # CL4002UN
            return "CL" + m.group(5) + m.group(6).upper()
        num, one, slash, suf = m.group(1), m.group(2), m.group(3), (m.group(4) or "").upper()
        if num[0] == "5":
            continue                                  # CL50xxx はメガネ（光学フレーム）の型番
        cands = _CELINE_SUN_BY_NUM.get(num, [])
        if slash or cands == ["CL" + num]:
            return "CL" + num                         # 旧型（CL41373/S）は5桁にまとめる
        if one:                                       # CL402321 → CL40232I（代表型番にあるときだけ）
            return "CL" + num + "I" if "CL" + num + "I" in cands else None
        if not suf and len(cands) == 1:
            return cands[0]                           # 末尾なし（CL40194）: 版違いが1つだけの代表型番に寄せる
        return "CL" + num + suf
    m = _CELINE_SUN_BARE_RE.search(t)
    if m:
        return "CL" + m.group(1).upper()
    return None


# ══ サングラスの型番（2026-11 追加）: メルカリ売り切れ（ログインなし）のタイトル・説明文・写真のOCRで
# 型番が取れたもので、2件以上売れている型番を代表型番にする（1件だけのものは相場が安定しないため入れない）。
# 代表型番に無い型番も、形式に当てはまれば型番として照合する（相場は同じ型番の売り切れだけで出す。_EXACT_NUMBER_BRANDS）。
# 各表の並びは画面の表示順（メルカリ相場の中央値が高い順）。コメントの「n件・¥中央値」は 2026-11-08 の集計 ══


def _sun_bare_re(keys, cut: int) -> re.Pattern | None:
    """接頭辞（GG・CL 等）を省いた表記を、代表型番に限って拾う正規表現（年・価格・サイズ等の数字と誤認しないよう、
    前後が数字・英字・小数点・桁区切り・円/年でないこと）。cut: 代表型番の先頭から省く文字数"""
    nums = sorted({k[cut:] for k in keys}, key=len, reverse=True)
    if not nums:
        return None
    return re.compile(r'(?<![A-Za-z0-9.,¥￥\-/])(' + "|".join(map(re.escape, nums))
                      + r')(?![A-Za-z0-9.,円年\-/])', re.IGNORECASE)


# ── プラダ・ミュウミュウ（ルックスオティカ）: 型番の形式 ──
#   旧表記: SPR 17W（S=サングラス、PR=プラダ / PS=リネアロッサ / PL、17W=モデル）。メガネは VPR 16M（V=メガネ）
#   新表記: PR 17WS（末尾 S=サングラス・V=メガネ）、PR A16S（英字 + 2桁の新しい番号）、末尾 F・A はアジアンフィット
#   先頭に 0 を付ける表記（0PR 17WS）、0 を英字の O と書く表記（OPR 17WSF）もある
# どの表記も SPR17W・SPS03Y・SPRA16 の形にまとめる（タイトルは SPR 表記が多い）。アジアンフィットは件数が少なく
# 相場差もはっきりしないため同じ型番にまとめる。メガネ（V）の型番は拾わない
def _lux_sun_re(fams: str) -> re.Pattern:
    return re.compile(
        r'(?<![A-Za-z0-9])[0O]?([SV])?(' + fams + r')[\s\-]?(\d{2}[A-Z]|[A-Z]\d{2})'
        r'(?:[\s\-]?([SV])(?=[AF]?(?![A-Za-z])))?(?:[\s\-]?[AF])?(?![A-Za-z])',
        re.IGNORECASE)


def _lux_sun(rx: re.Pattern, title: str) -> str | None:
    t = unicodedata.normalize("NFKC", title or "")
    for m in rx.finditer(t):
        lead, fam, code, tail = (m.group(1) or "").upper(), m.group(2).upper(), m.group(3).upper(), \
            (m.group(4) or "").upper()
        if lead == "V" or tail == "V":
            continue                      # メガネ（光学フレーム）の型番
        return "S" + fam + code
    return None


_PRADA_SUN_RE = _lux_sun_re("PR|PS|PL")
_MIUMIU_SUN_RE = _lux_sun_re("MU")
_PRADA_SUN_MODELS: dict[str, str] = {
    "SPR55T": "",                           # 6件・¥53,500
    "SPR53F": "",                           # 3件・¥49,000
    "SPR09Z": "",                           # 9件・¥45,800
    "SPRA19": "",                           # 4件・¥44,400
    "SPRA14": "",                           # 4件・¥43,500
    "SPRA17": "",                           # 2件・¥39,000
    "SPR10Z": "",                           # 14件・¥38,000
    "SPRC07": "",                           # 2件・¥36,240
    "SPRB06": "",                           # 2件・¥36,150
    "SPR13Z": "",                           # 2件・¥35,550
    "SPR26Z": "",                           # 13件・¥32,000
    "SPR17Y": "",                           # 12件・¥30,400
    "SPR17W": "",                           # 35件・¥30,000
    "SPR25Y": "",                           # 2件・¥30,000
    "SPRA50": "",                           # 2件・¥29,000
    "SPR14W": "",                           # 2件・¥28,400
    "SPR57Y": "",                           # 2件・¥27,750
    "SPR03Z": "",                           # 4件・¥27,500
    "SPRB18": "",                           # 3件・¥26,000
    "SPR22Z": "",                           # 5件・¥25,000
    "SPRA06": "",                           # 3件・¥25,000
    "SPR24Z": "",                           # 8件・¥24,500
    "SPR68A": "",                           # 2件・¥24,000
    "SPR27Z": "",                           # 3件・¥22,600
    "SPR08Y": "",                           # 3件・¥22,000
    "SPS03Y": "",                           # 2件・¥20,950
    "SPRA16": "",                           # 8件・¥20,750
    "SPRB09": "",                           # 3件・¥20,000
    "SPR15W": "",                           # 3件・¥19,800
    "SPR02V": "",                           # 3件・¥18,700
    "SPR61A": "",                           # 3件・¥18,615
    "SPS12U": "",                           # 2件・¥18,590
    "SPR14G": "",                           # 41件・¥18,000
    "SPRA13": "",                           # 2件・¥18,000
    "SPR19S": "",                           # 2件・¥17,950
    "SPR59E": "",                           # 2件・¥17,900
    "SPRA60": "",                           # 2件・¥17,500
    "SPR27N": "",                           # 9件・¥16,700
    "SPRB05": "",                           # 3件・¥16,300
    "SPR04N": "",                           # 2件・¥14,350
    "SPS01U": "",                           # 4件・¥14,250
    "SPR17T": "",                           # 3件・¥13,990
    "SPR50T": "",                           # 2件・¥13,990
    "SPR65X": "",                           # 2件・¥13,439
    "SPR50D": "",                           # 2件・¥13,237
    "SPR66A": "",                           # 2件・¥12,650
    "SPR66T": "",                           # 7件・¥12,500
    "SPR21S": "",                           # 2件・¥12,500
    "SPR13V": "",                           # 2件・¥12,100
    "SPR08O": "",                           # 7件・¥12,000
    "SPR56G": "",                           # 3件・¥12,000
    "SPR13S": "",                           # 2件・¥12,000
    "SPR17S": "",                           # 4件・¥11,950
    "SPR04M": "",                           # 2件・¥11,945
    "SPR27L": "",                           # 6件・¥11,500
    "SPR03M": "",                           # 3件・¥11,000
    "SPR58G": "",                           # 3件・¥11,000
    "SPR01R": "",                           # 2件・¥10,999
    "SPR07P": "",                           # 4件・¥10,750
    "SPR05X": "",                           # 2件・¥10,700
    "SPR10M": "",                           # 5件・¥10,400
    "SPR58U": "",                           # 2件・¥10,400
    "SPR09S": "",                           # 7件・¥10,000
    "SPR31N": "",                           # 7件・¥10,000
    "SPR53S": "",                           # 5件・¥10,000
    "SPR16L": "",                           # 3件・¥10,000
    "SPR18U": "",                           # 3件・¥10,000
    "SPR10V": "",                           # 2件・¥9,900
    "SPR20S": "",                           # 3件・¥9,800
    "SPR54G": "",                           # 4件・¥9,650
    "SPR01O": "",                           # 5件・¥9,600
    "SPR71C": "",                           # 5件・¥9,500
    "SPR50A": "",                           # 2件・¥9,500
    "SPS53N": "",                           # 2件・¥9,350
    "SPR55D": "",                           # 2件・¥9,300
    "SPR23Q": "",                           # 2件・¥9,250
    "SPR05P": "",                           # 3件・¥9,000
    "SPR05T": "",                           # 3件・¥9,000
    "SPR26L": "",                           # 2件・¥9,000
    "SPR17P": "",                           # 2件・¥9,000
    "SPR11N": "",                           # 2件・¥8,749
    "SPR53E": "",                           # 2件・¥8,578
    "SPR18Q": "",                           # 2件・¥8,500
    "SPR24H": "",                           # 2件・¥8,500
    "SPR56T": "",                           # 2件・¥8,350
    "SPR10R": "",                           # 2件・¥8,350
    "SPR57G": "",                           # 2件・¥8,300
    "SPR25G": "",                           # 2件・¥8,150
    "SPR24Q": "",                           # 2件・¥8,000
    "SPR51H": "",                           # 3件・¥7,777
    "SPR08L": "",                           # 3件・¥7,700
    "SPR05L": "",                           # 2件・¥7,700
    "SPS03O": "",                           # 5件・¥7,500
    "SPR09I": "",                           # 2件・¥7,500
    "SPR15L": "",                           # 2件・¥7,250
    "SPR02R": "",                           # 2件・¥6,850
    "SPR52C": "",                           # 3件・¥6,800
    "SPR21H": "",                           # 2件・¥6,705
    "SPR27M": "",                           # 4件・¥6,000
    "SPS58Q": "",                           # 2件・¥5,990
    "SPR60C": "",                           # 2件・¥5,925
    "SPR25N": "",                           # 2件・¥5,499
    "SPR75G": "",                           # 2件・¥5,450
    "SPR16U": "",                           # 2件・¥5,250
    "SPR12R": "",                           # 3件・¥5,000
    "SPR56E": "",                           # 2件・¥5,000
    "SPR51S": "",                           # 4件・¥4,750
    "SPR70G": "",                           # 2件・¥4,195
}
_MIUMIU_SUN_MODELS: dict[str, str] = {
    "SMU01Y": "",                           # 2件・¥47,450
    "SMUA54": "",                           # 7件・¥45,000
    "SMUB09": "",                           # 2件・¥43,750
    "SMU01Z": "",                           # 4件・¥40,400
    "SMU10Z": "",                           # 3件・¥39,900
    "SMU07Y": "",                           # 3件・¥39,000
    "SMUA03": "",                           # 12件・¥38,750
    "SMUB07": "",                           # 4件・¥36,500
    "SMU54Y": "",                           # 4件・¥36,000
    "SMUA51": "",                           # 7件・¥35,000
    "SMU11W": "",                           # 38件・¥34,450
    "SMU06Z": "",                           # 6件・¥33,999
    "SMU04Z": "",                           # 23件・¥33,000
    "SMU09W": "",                           # 21件・¥32,000
    "SMUB05": "",                           # 3件・¥30,000
    "SMUA06": "",                           # 4件・¥29,000
    "SMU52T": "",                           # 3件・¥18,000
    "SMU54R": "",                           # 3件・¥14,000
    "SMU03N": "",                           # 4件・¥13,700
    "SMU52W": "",                           # 2件・¥13,638
    "SMU08R": "",                           # 2件・¥13,450
    "SMU05G": "",                           # 3件・¥12,800
    "SMU13W": "",                           # 3件・¥12,100
    "SMU62F": "",                           # 2件・¥11,065
    "SMU21G": "",                           # 5件・¥10,800
    "SMU10N": "",                           # 2件・¥10,545
    "SMU10Q": "",                           # 2件・¥9,200
    "SMU08U": "",                           # 4件・¥8,900
    "SMU13N": "",                           # 2件・¥8,844
    "SMU08L": "",                           # 2件・¥8,350
    "SMU63G": "",                           # 2件・¥8,340
    "SMU23I": "",                           # 2件・¥7,799
    "SMU05M": "",                           # 4件・¥7,650
    "SMU19G": "",                           # 2件・¥6,999
    "SMU01S": "",                           # 2件・¥6,700
    "SMU07M": "",                           # 2件・¥6,650
    "SMU10M": "",                           # 2件・¥6,650
    "SMU06H": "",                           # 2件・¥5,316
    "SMU03V": "",                           # 2件・¥5,076
    "SMU06M": "",                           # 2件・¥5,050
    "SMU20I": "",                           # 2件・¥4,800
}


def _extract_prada_sunglasses(title: str) -> str | None:
    """プラダのサングラスの型番（SPR17W / SPS03Y / SPRA16。PR 17WS・PR A16S も同じ形にまとめる）"""
    return _lux_sun(_PRADA_SUN_RE, title)


def _extract_miumiu_sunglasses(title: str) -> str | None:
    """ミュウミュウのサングラスの型番（SMU01T。MU 09WS も SMU09W にまとめる）"""
    return _lux_sun(_MIUMIU_SUN_RE, title)


# ── バーバリー・ヴェルサーチ（ルックスオティカ）: BE4216 / VE4361 ──
#   4桁の1桁目: バーバリー 4=サングラス（アセテート）・3=サングラス（メタル）・2/1=メガネ。
#   ヴェルサーチ 4=サングラス（アセテート）・2=サングラス（メタル）・3/1=メガネ。メガネの型番は拾わない
#   表記ゆれ: BE4216 / BE 4216 / be-4216 / B4216 / B 4216（テンプルの刻印は「B 4216」）/ 0BE4216 /
#   ヴェルサーチの MOD.4361（テンプルの刻印）。末尾 F・D・A（アジアンフィット等。バーバリーは F・D・A、ヴェルサーチは F）は
#   別型番として扱う（レイバンと同じ）。
#   続くカラー番号（3001/13 等）は型番に含めない
_BURBERRY_SUN_RE = re.compile(
    r'(?<![A-Za-z0-9])[0O]?BE?[\s\-]?([1-4]\d{3})(?:[\s\-]?([FDA])(?![A-Za-z0-9]))?(?![0-9]|\s?[円年])', re.IGNORECASE)
_VERSACE_SUN_RE = re.compile(
    r'(?<![A-Za-z0-9])(?:[0O]?VE|MOD\.?)[\s\-]?([1-4]\d{3})(?:[\s\-]?(F)(?![A-Za-z0-9]))?(?![0-9]|\s?[円年])', re.IGNORECASE)
_BURBERRY_SUN_MODELS: dict[str, str] = {
    "BE4403F": "",                          # 2件・¥19,250
    "BE4389F": "",                          # 7件・¥17,500
    "BE3125": "",                           # 2件・¥17,000
    "BE4358": "",                           # 2件・¥15,400
    "BE4349F": "",                          # 3件・¥14,900
    "BE4357F": "",                          # 4件・¥14,750
    "BE3127D": "",                          # 2件・¥14,250
    "BE4447D": "",                          # 5件・¥13,000
    "BE4394F": "",                          # 3件・¥13,000
    "BE4389": "",                           # 3件・¥12,500
    "BE4270F": "",                          # 3件・¥12,000
    "BE3101": "",                           # 2件・¥11,850
    "BE4396": "",                           # 4件・¥11,250
    "BE4345F": "",                          # 3件・¥11,200
    "BE4351D": "",                          # 4件・¥11,027
    "BE4289D": "",                          # 3件・¥11,000
    "BE4291": "",                           # 8件・¥10,600
    "BE4337F": "",                          # 8件・¥10,500
    "BE4376": "",                           # 6件・¥9,850
    "BE4348F": "",                          # 3件・¥9,600
    "BE4364F": "",                          # 2件・¥9,500
    "BE4333F": "",                          # 4件・¥9,150
    "BE4274D": "",                          # 2件・¥8,815
    "BE4035": "",                           # 2件・¥8,650
    "BE4434F": "",                          # 2件・¥8,649
    "BE4266": "",                           # 2件・¥8,249
    "BE4323F": "",                          # 3件・¥8,000
    "BE3094": "",                           # 2件・¥6,950
    "BE4410": "",                           # 2件・¥6,000
    "BE4418": "",                           # 2件・¥5,750
    "BE4041": "",                           # 1件・¥4,590
}
# 「BE」「B」が付かない 4291 / 3136D は代表型番だけ拾う（年・価格・サイズ等の数字と区別するため）
_BURBERRY_SUN_BARE_RE = _sun_bare_re([k for k in _BURBERRY_SUN_MODELS if k[2] in "34"], 2)
_VERSACE_SUN_MODELS: dict[str, str] = {
    "VE4044": "",                           # 4件・¥21,999
    "VE2064": "",                           # 2件・¥21,850
    "VE4361": "",                           # 35件・¥20,500
    "VE4425": "",                           # 4件・¥19,250
    "VE4465": "",                           # 3件・¥18,000
    "VE2028": "",                           # 9件・¥17,100
    "MOD.421": "",                          # 3件・¥17,000
    "MOD.S71": "",                          # 3件・¥16,000
    "VE2019": "",                           # 3件・¥15,400
    "VE2006": "",                           # 2件・¥15,250
    "VE4307": "",                           # 6件・¥15,200
    "VE4447": "",                           # 2件・¥15,100
    "MOD.S65": "",                          # 2件・¥14,750
    "MOD.413": "",                          # 6件・¥13,950
    "MOD.S74": "",                          # 2件・¥13,850
    "VE4296": "",                           # 4件・¥13,650
    "MOD.414": "",                          # 2件・¥12,800
    "MOD.372": "",                          # 10件・¥12,750
    "MOD.418": "",                          # 2件・¥12,550
    "MOD.S73": "",                          # 2件・¥12,400
    "MOD.S68": "",                          # 4件・¥12,300
    "MOD.S61": "",                          # 3件・¥12,000
    "VE2021": "",                           # 5件・¥11,000
    "MOD.460": "",                          # 3件・¥10,700
    "MOD.420": "",                          # 6件・¥10,510
    "MOD.480": "",                          # 2件・¥10,350
    "MOD.424": "",                          # 18件・¥9,650
    "MOD.S54": "",                          # 2件・¥8,900
    "MOD.S64": "",                          # 2件・¥8,490
    "MOD.417": "",                          # 2件・¥7,150
    "MOD.479": "",                          # 2件・¥7,150
    "VE2017": "",                           # 2件・¥6,900
    "MOD.S72": "",                          # 2件・¥6,775
    "MOD.389": "",                          # 2件・¥6,179
}
_VERSACE_SUN_BARE_RE = _sun_bare_re([k for k in _VERSACE_SUN_MODELS if k.startswith("VE") and k[2] in "24"
                                     and not 1900 <= int(k[2:6]) <= 2099], 2)


def _extract_burberry_sunglasses(title: str) -> str | None:
    """バーバリーのサングラスの型番（BE4216 / BE3080 / BE4291F / BE3136D）。メガネの BE2xxx・BE1xxx は拾わない"""
    t = unicodedata.normalize("NFKC", title or "")
    optical = False
    for m in _BURBERRY_SUN_RE.finditer(t):
        if m.group(1)[0] in "34":
            return "BE" + m.group(1) + (m.group(2) or "").upper()
        optical = True
    if _BURBERRY_SUN_BARE_RE is not None and not optical:     # メガネの型番がある出品の他の数字は見ない
        m = _BURBERRY_SUN_BARE_RE.search(t)
        if m:
            return "BE" + m.group(1).upper()
    return None


# ジャンニ・ヴェルサーチ期（1990年代まで）の旧型: MOD.424 / MOD 413 / MOD.479/A（3桁。/A 等はカラー）→ MOD.424
_VERSACE_OLD_RE = re.compile(r'(?<![A-Za-z0-9])MOD\.?\s?(\d{3}|S\d{2})(?:\s?/\s?[A-Z]{1,2})?(?![0-9A-Za-z])',
                             re.IGNORECASE)


def _extract_versace_sunglasses(title: str) -> str | None:
    """ヴェルサーチのサングラスの型番（VE4361 / VE2150 / VE4361F / 旧型 MOD.424）。メガネの VE3xxx・VE1xxx は拾わない"""
    t = unicodedata.normalize("NFKC", title or "")
    optical = False
    for m in _VERSACE_SUN_RE.finditer(t):
        if m.group(1)[0] in "24":
            return "VE" + m.group(1) + (m.group(2) or "").upper()
        optical = True
    if optical:
        return None                       # メガネの型番がある出品の他の数字は見ない
    m = _VERSACE_OLD_RE.search(t)
    if m:
        return "MOD." + m.group(1).upper()
    if _VERSACE_SUN_BARE_RE is not None:
        m = _VERSACE_SUN_BARE_RE.search(t)    # 「VE」「MOD」が付かない 4361（代表型番だけ）
        if m:
            return "VE" + m.group(1).upper()
    return None


# ── トムフォード: TF237（旧表記）/ FT0237（新表記）は同じ型番。タイトルは TF 表記が多いため TF + 番号（先頭の 0 を除く）にまとめる ──
#   TF5xxx・FT5xxx はメガネ（光学フレーム）、TF6xxx も眼鏡（ブルーライトカット -B 等）として売られているため拾わない。
#   末尾 -F・-D・-K（アジアンフィット等）は別型番として扱う（レイバンの F と同じ）。2桁の古い型番（TF58 ウェリントン）もある
#   表記ゆれ: TF237 / TF 237 / tf-237 / FT0237 / FT 0237 / TF0237 / FT0237-F / TF237F / TF1048-D
_TOMFORD_SUN_RE = re.compile(
    r'(?<![A-Za-z0-9])(?:TF|FT)[\s\-]?(\d{2,4})(?:[\s\-]?([FDK])(?![A-Za-z0-9]))?(?![0-9])', re.IGNORECASE)
_TOMFORD_SUN_MODELS: dict[str, str] = {
    "TF1248K": "",                          # 2件・¥56,700
    "TF1042D": "",                          # 9件・¥49,500
    "TF1395D": "",                          # 3件・¥45,800
    "TF1394D": "",                          # 2件・¥45,750
    "TF1233D": "",                          # 4件・¥45,000
    "TF1131K": "",                          # 2件・¥43,250
    "TF1151D": "",                          # 5件・¥39,900
    "TF971K": "",                           # 3件・¥38,500
    "TF1136K": "",                          # 2件・¥38,250
    "TF436": "",                            # 4件・¥37,500
    "TF1119D": "",                          # 2件・¥36,400
    "TF1230D": "",                          # 3件・¥36,000
    "TF1392D": "",                          # 2件・¥35,950
    "TF1277D": "",                          # 4件・¥35,550
    "TF672": "",                            # 8件・¥35,500
    "TF1232D": "",                          # 3件・¥35,000
    "TF1042": "",                           # 2件・¥34,500
    "TF1231D": "",                          # 7件・¥34,000
    "TF892K": "",                           # 3件・¥34,000
    "TF1149D": "",                          # 4件・¥32,000
    "TF1274D": "",                          # 5件・¥31,700
    "TF1234D": "",                          # 2件・¥31,500
    "TF1238K": "",                          # 2件・¥31,197
    "TF1050D": "",                          # 2件・¥30,550
    "TF1275D": "",                          # 2件・¥28,800
    "TF1148": "",                           # 2件・¥28,749
    "TF1040D": "",                          # 16件・¥28,000
    "TF1123D": "",                          # 6件・¥27,500
    "TF673": "",                            # 5件・¥27,500
    "TF1240K": "",                          # 3件・¥27,000
    "TF978D": "",                           # 2件・¥26,900
    "TF977D": "",                           # 4件・¥26,750
    "TF1277": "",                           # 2件・¥26,500
    "TF1148D": "",                          # 3件・¥26,000
    "TF1097": "",                           # 5件・¥25,740
    "TF1048D": "",                          # 6件・¥25,650
    "TF1122D": "",                          # 7件・¥25,500
    "TF904F": "",                           # 3件・¥25,000
    "TF1049D": "",                          # 3件・¥24,000
    "TF877D": "",                           # 4件・¥23,940
    "TF1048": "",                           # 14件・¥23,500
    "TF962D": "",                           # 2件・¥23,200
    "TF1041D": "",                          # 7件・¥23,000
    "TF961D": "",                           # 6件・¥23,000
    "TF891": "",                            # 2件・¥22,500
    "TF638K": "",                           # 2件・¥22,500
    "TF879D": "",                           # 2件・¥22,250
    "TF876D": "",                           # 3件・¥21,800
    "TF884": "",                            # 2件・¥20,650
    "TF891K": "",                           # 2件・¥20,450
    "TF959D": "",                           # 2件・¥20,400
    "TF751": "",                            # 7件・¥20,000
    "TF897K": "",                           # 3件・¥20,000
    "TF804K": "",                           # 3件・¥20,000
    "TF237": "",                            # 27件・¥19,900
    "TF649": "",                            # 2件・¥19,750
    "TF237F": "",                           # 50件・¥18,700
    "TF248": "",                            # 23件・¥18,700
    "TF548K": "",                           # 2件・¥18,300
    "TF892": "",                            # 2件・¥18,000
    "TF1132K": "",                          # 2件・¥18,000
    "TF144": "",                            # 8件・¥17,750
    "TF336": "",                            # 5件・¥17,500
    "TF816": "",                            # 2件・¥16,490
    "TF963D": "",                           # 2件・¥15,500
    "TF211": "",                            # 5件・¥15,100
    "TF9256": "",                           # 2件・¥15,100
    "TF475D": "",                           # 2件・¥15,100
    "TF9336": "",                           # 2件・¥15,000
    "TF590": "",                            # 3件・¥14,900
    "TF539": "",                            # 3件・¥14,800
    "TF9257": "",                           # 5件・¥14,500
    "TF1044": "",                           # 2件・¥14,500
    "TF544K": "",                           # 4件・¥14,325
    "TF752": "",                            # 2件・¥13,900
    "TF522F": "",                           # 4件・¥13,850
    "TF521": "",                            # 6件・¥13,750
    "TF623": "",                            # 2件・¥13,750
    "TF542K": "",                           # 4件・¥13,450
    "TF878D": "",                           # 3件・¥13,000
    "TF613": "",                            # 2件・¥12,250
    "TF400": "",                            # 2件・¥12,000
    "TF35": "",                             # 4件・¥11,980
    "TF236": "",                            # 2件・¥11,960
    "TF58": "",                             # 8件・¥10,150
    "TF198": "",                            # 2件・¥10,000
    "TF298": "",                            # 3件・¥9,999
    "TF289": "",                            # 2件・¥7,200
    "TF163": "",                            # 3件・¥5,100
}


def _extract_tomford_sunglasses(title: str) -> str | None:
    """トムフォードのサングラスの型番（TF237 / TF237F / TF1048D）。メガネの TF5xxx・TF6xxx は拾わない"""
    t = unicodedata.normalize("NFKC", title or "")
    for m in _TOMFORD_SUN_RE.finditer(t):
        num = str(int(m.group(1)))
        if len(num) == 4 and num[0] in "56":
            continue
        return "TF" + num + (m.group(2) or "").upper()
    return None


# ── オークリー: OO9102（O + O=サングラス）。OX=メガネ・OJ=キッズは拾わない ──
#   表記ゆれ: OO9102 / OO 9102 / oo-9102 / 0OO9102 / OO9102-D5（品番 = 型番 + カラー番号）/ 9102-D5（型番抜きの品番）
#   アジアンフィットは別の型番（ホルブルック OO9102 → OO9244 等）なので、そのまま別型番になる
_OAKLEY_SUN_RE = re.compile(r'(?<![A-Za-z0-9])[0O]?O([OXJ])[\s\-]?(\d{4})(?![0-9])', re.IGNORECASE)
_OAKLEY_SKU_RE = re.compile(r'(?<![A-Za-z0-9.,¥￥\-/])(9\d{3})-[0-9A-Z]{2,4}(?![A-Za-z0-9])', re.IGNORECASE)
_OAKLEY_SUN_MODELS: dict[str, str] = {
    "OO9437": "",                           # 2件・¥20,650
    "OO9039": "",                           # 3件・¥19,882
    "OO6046": "",                           # 2件・¥19,450
    "OO9455": "",                           # 3件・¥18,000
    "OO9161": "",                           # 9件・¥17,500
    "OO9242": "",                           # 2件・¥16,546
    "OO9438": "",                           # 7件・¥16,000
    "OO9465": "",                           # 2件・¥16,000
    "OO9495": "",                           # 3件・¥15,000
    "OO9463": "",                           # 5件・¥14,500
    "OO9290": "",                           # 12件・¥14,250
    "OO9250": "",                           # 4件・¥13,250
    "OO9406": "",                           # 7件・¥13,000
    "OO9127": "",                           # 2件・¥12,250
    "OO9403": "",                           # 2件・¥11,999
    "OO9506": "",                           # 2件・¥11,749
    "OO9454": "",                           # 3件・¥11,000
    "OO9245": "",                           # 16件・¥10,949
    "OO9014": "",                           # 4件・¥10,150
    "OO9236": "",                           # 2件・¥10,008
    "OO9528": "",                           # 2件・¥10,000
    "OO9206": "",                           # 14件・¥9,900
    "OO9271": "",                           # 10件・¥9,600
    "OO9313": "",                           # 6件・¥9,041
    "OO9102": "",                           # 7件・¥9,000
    "OO9238": "",                           # 2件・¥8,600
    "OO9208": "",                           # 5件・¥8,000
    "OO9349": "",                           # 7件・¥7,500
    "OO9096": "",                           # 2件・¥7,500
    "OO9153": "",                           # 11件・¥7,000
    "OO9417": "",                           # 3件・¥7,000
    "OO9275": "",                           # 2件・¥7,000
    "OO2039": "",                           # 2件・¥7,000
    "OO9181": "",                           # 2件・¥6,900
    "OO9264": "",                           # 2件・¥6,750
    "OO9448": "",                           # 2件・¥6,750
    "OO9449": "",                           # 2件・¥6,750
    "OO9244": "",                           # 7件・¥6,500
    "OO9188": "",                           # 7件・¥6,000
    "OO9374": "",                           # 4件・¥6,000
    "OO9212": "",                           # 2件・¥5,500
    "OO9175": "",                           # 2件・¥3,999
}


def _extract_oakley_sunglasses(title: str) -> str | None:
    """オークリーのサングラスの型番（OO9102）、無ければモデル名（ホルブルック 等）。メガネの OX・キッズの OJ は拾わない"""
    t = unicodedata.normalize("NFKC", title or "")
    for m in _OAKLEY_SUN_RE.finditer(t):
        if m.group(1).upper() == "O":
            return "OO" + m.group(2)
    m = _OAKLEY_SKU_RE.search(t)
    if m:
        return "OO" + m.group(1)
    return extract_name(t, "オークリー")


# ── ロエベ（テリオス。セリーヌと同じ形式）: LW40001I ──
#   LW + 5桁 + U/I/F。LW50xxx はメガネ（光学フレーム）の型番なので拾わない。カラー番号（01A 等）は型番に含めない
_LOEWE_SUN_RE = re.compile(r'(?<![A-Za-z0-9])LW[\s\-]?(\d{5})(?:([UIF])(?![A-Za-z]))?(?![0-9])', re.IGNORECASE)
# デリーゴ期（2020年頃まで）の旧型: SLW 384G / SLW384 / slw-099（S=サングラス・V=メガネ + LW + 3桁。末尾の G・S・M 等は
# 版違いで件数が少ないため SLW384 にまとめる）
_LOEWE_OLD_RE = re.compile(r'(?<![A-Za-z0-9])([SV])LW[\s\-]?(\d{3})(?:[A-Z](?![A-Za-z]))?(?![0-9A-Za-z])', re.IGNORECASE)
_LOEWE_SUN_MODELS: dict[str, str] = {
    "SLW937": "",                           # 2件・¥13,500
    "SLW773": "",                           # 2件・¥11,600
    "SLW617": "",                           # 2件・¥10,350
    "SLW931": "",                           # 2件・¥10,250
    "SLW612": "",                           # 3件・¥10,000
    "SLW384": "",                           # 2件・¥9,565
    "SLW904": "",                           # 2件・¥8,999
    "SLW506": "",                           # 2件・¥8,950
    "SLW702": "",                           # 2件・¥8,800
    "SLW309": "",                           # 2件・¥8,650
    "SLW665": "",                           # 3件・¥8,500
    "SLW707": "",                           # 2件・¥7,650
    "SLW099": "",                           # 6件・¥7,265
    "SLW638": "",                           # 2件・¥6,490
    "SLW211": "",                           # 2件・¥6,100
    "SLW005": "",                           # 2件・¥4,975
    "SLW640": "",                           # 2件・¥4,585
    "SLW102": "",                           # 2件・¥3,750
    "SLW180": "",                           # 2件・¥3,150
    "SLW121": "",                           # 2件・¥2,540
}


def _extract_loewe_sunglasses(title: str) -> str | None:
    """ロエベのサングラスの型番（LW40001I / 旧型 SLW384）。メガネの LW50xxx・VLW は拾わない"""
    t = unicodedata.normalize("NFKC", title or "")
    for m in _LOEWE_OLD_RE.finditer(t):
        if m.group(1).upper() == "S":
            return "SLW" + m.group(2)
    for m in _LOEWE_SUN_RE.finditer(t):
        if m.group(1)[0] == "5":
            continue
        num, suf = m.group(1), (m.group(2) or "").upper()
        if not suf:
            cands = [k for k in _LOEWE_SUN_MODELS if k[2:7] == num]
            if len(cands) == 1:
                return cands[0]
        return "LW" + num + suf
    return None


# ── バレンシアガ（ケリング。グッチと同じ形式）: BB0096S ──
#   BB + 4桁 + S（SA・SK=アジアンフィット等は別型番）。末尾 O・OA 等はメガネ（光学フレーム）なので拾わない
#   表記ゆれ: BB0096S / BB 0096S / bb-0096s / BB0096S-001（カラー番号）
_BALENCIAGA_SUN_RE = re.compile(r'(?<![A-Za-z0-9])BB[\s\-]?(\d{4})([A-Z]{0,2})(?![A-Za-z])', re.IGNORECASE)
_BALENCIAGA_SUN_MODELS: dict[str, str] = {
    "BB0299S": "",                          # 3件・¥55,000
    "BB0096S": "",                          # 53件・¥35,000
    "BB0311SK": "",                         # 4件・¥34,000
    "BB0253S": "",                          # 2件・¥33,500
    "BB0324SK": "",                         # 5件・¥33,000
    "BB0135SA": "",                         # 2件・¥33,000
    "BB0103SA": "",                         # 4件・¥32,350
    "BB0157S": "",                          # 9件・¥32,000
    "BB0229S": "",                          # 3件・¥32,000
    "BB0102SA": "",                         # 20件・¥30,000
    "BB0134SA": "",                         # 3件・¥30,000
    "BB0202S": "",                          # 2件・¥30,000
    "BB0310SK": "",                         # 2件・¥28,900
    "BB0095S": "",                          # 5件・¥23,999
    "BB0369SK": "",                         # 2件・¥20,000
    "BB0262SA": "",                         # 4件・¥18,750
    "BB0189S": "",                          # 3件・¥16,500
    "BB0215SA": "",                         # 3件・¥15,000
    "BB0106S": "",                          # 4件・¥14,166
}


def _extract_balenciaga_sunglasses(title: str) -> str | None:
    """バレンシアガのサングラスの型番（BB0096S / BB0102SA）。メガネの BB0xxxO は拾わない"""
    t = unicodedata.normalize("NFKC", title or "")
    for m in _BALENCIAGA_SUN_RE.finditer(t):
        num, suf = m.group(1), m.group(2).upper()
        if suf.startswith("O"):
            continue
        if suf.startswith("S"):
            return "BB" + num + (suf[:2] if len(suf) == 2 and suf[1] in "AK" else "S")
        cands = [k for k in _BALENCIAGA_SUN_MODELS if k[2:6] == num]
        if len(cands) == 1:
            return cands[0]
        return "BB" + num + "S" if not suf else None
    return None


# ── ルイ・ヴィトンのサングラス: Z1578E（Z + 4桁 + E/U/W 等）──
#   末尾の英字（E・U・W 等。カラー・ライン違い）は別型番として扱う（Z1578E と Z1578W は相場も違う）。
#   末尾の無い表記（Z1578）は、代表型番に同じ4桁が1つだけあるときだけそれに寄せる
#   表記ゆれ: Z1578E / Z 1578E / z1578e / Z-1578E / Z1578E 94X（サイズ等が続くもの）
#   バッグの型番（M40995 等）は _extract_lv_bag で照合する（ジャンル別）
_LV_SUN_RE = re.compile(r'(?<![A-Za-z0-9])Z[\s\-]?(\d{4})([A-Z])?(?![A-Za-z0-9])', re.IGNORECASE)
_LV_SUN_MODELS: dict[str, str] = {
    "Z1165E": "",                           # 17件・¥66,000
    "Z1326E": "",                           # 2件・¥65,000
    "Z1643E": "",                           # 2件・¥58,800
    "Z1578E": "",                           # 7件・¥56,100
    "Z1971E": "",                           # 2件・¥55,700
    "Z1584U": "",                           # 2件・¥53,500
    "Z1597E": "",                           # 3件・¥50,000
    "Z1414E": "",                           # 2件・¥49,900
    "Z1911U": "",                           # 2件・¥49,500
    "Z2125E": "",                           # 2件・¥49,300
    "Z2063U": "",                           # 5件・¥49,200
    "Z2004E": "",                           # 8件・¥46,500
    "Z1861E": "",                           # 3件・¥45,000
    "Z1487E": "",                           # 2件・¥45,000
    "Z1523E": "",                           # 12件・¥44,000
    "Z2381U": "",                           # 3件・¥44,000
    "Z1502E": "",                           # 6件・¥43,611
    "Z1547E": "",                           # 4件・¥43,550
    "Z2975E": "",                           # 2件・¥43,000
    "Z1667E": "",                           # 7件・¥42,000
    "Z0339U": "",                           # 5件・¥42,000
    "Z2136U": "",                           # 3件・¥41,500
    "Z1842E": "",                           # 2件・¥40,000
    "Z1692E": "",                           # 3件・¥39,800
    "Z1109E": "",                           # 2件・¥38,900
    "Z1955E": "",                           # 2件・¥36,000
    "Z1526E": "",                           # 4件・¥35,750
    "Z1496E": "",                           # 12件・¥34,900
    "Z1500E": "",                           # 2件・¥34,250
    "Z0260U": "",                           # 3件・¥34,000
    "Z1583E": "",                           # 4件・¥33,999
    "Z1093E": "",                           # 2件・¥33,400
    "Z0361U": "",                           # 2件・¥32,250
    "Z1068E": "",                           # 2件・¥29,840
    "Z0340U": "",                           # 2件・¥27,850
    "Z1094E": "",                           # 2件・¥27,690
    "Z0051U": "",                           # 2件・¥26,950
    "Z0165U": "",                           # 6件・¥26,800
    "Z1333E": "",                           # 2件・¥25,150
    "Z0435U": "",                           # 2件・¥25,000
    "Z0089U": "",                           # 2件・¥24,990
    "Z0371U": "",                           # 4件・¥24,000
    "Z0283E": "",                           # 3件・¥24,000
    "Z0460E": "",                           # 2件・¥23,500
    "Z0035U": "",                           # 2件・¥22,390
    "Z0459E": "",                           # 9件・¥21,000
    "Z0111E": "",                           # 2件・¥17,500
    "Z0215U": "",                           # 2件・¥16,500
    "Z0366E": "",                           # 2件・¥16,040
    "Z0032E": "",                           # 3件・¥15,000
    "Z0134E": "",                           # 2件・¥14,390
    "Z0034U": "",                           # 2件・¥12,440
}


def _extract_lv_sunglasses(title: str) -> str | None:
    """ルイ・ヴィトンのサングラスの型番（Z1578E / Z0165U）"""
    t = unicodedata.normalize("NFKC", title or "")
    for m in _LV_SUN_RE.finditer(t):
        num, suf = m.group(1), (m.group(2) or "").upper()
        if not suf:
            cands = [k for k in _LV_SUN_MODELS if k[1:5] == num]
            return cands[0] if len(cands) == 1 else "Z" + num
        return "Z" + num + suf
    return None


# ── シャネルのサングラス: 4桁（4xxx=メタル・5xxx=アセテート・6xxx）+ 末尾の版違い ──
#   テンプルの刻印は「5417-A c.501/S6」（-A=アジアンフィット、-Q=偏光、-B・-D・-H・-S・-T 等=版違い、c.501=カラー）。
#   版違いとアジアンフィットが両方付く「5456-Q-A」「5408-S-A」は 5456-QA・5408-SA にする。
#   末尾の版違いは別型番として扱い（レイバンの F と同じ）、5417-A の形にする。CH5417A・CH 5417 A も同じ型番。
#   3xxx・2xxx はメガネ（光学フレーム）の型番なので拾わない。
#   「CH」も末尾の版違い・カラー（c.501）も無い4桁の数字だけ（「シャネル 4002」）は、代表型番に限って拾う
#   （価格・サイズ等の数字と区別するため）。1990年代の5桁の旧型番（01450・02461 等）も代表型番に限って拾う
_CHANEL_SUN_RE = re.compile(
    r'(?<![A-Za-z0-9.,¥￥\-/])(CH[\s\-]?)?([2-6]\d{3})(?:[\s\-]?(QA|[ABDHQST])(?:-(A))?(?![A-Za-z]))?(?![0-9.,円年%])',
    re.IGNORECASE)
_CHANEL_COLOR_RE = re.compile(r'\s?C[\.\s]?\d{2,4}', re.IGNORECASE)
_CHANEL_OLD_RE = re.compile(r'(?<![A-Za-z0-9.,¥￥\-/])([09]\d{4})(?![0-9.,円年%])')
_CHANEL_SUN_MODELS: dict[str, str] = {
    "4275-Q": "",                           # 2件・¥75,170
    "5487-A": "",                           # 6件・¥69,500
    "4125": "",                             # 2件・¥58,400
    "4245": "",                             # 8件・¥50,000
    "5408-SA": "",                          # 2件・¥49,250
    "5422-BA": "",                          # 2件・¥44,150
    "5414-A": "",                           # 4件・¥43,200
    "5210-QA": "",                          # 8件・¥41,240
    "4242": "",                             # 3件・¥40,000
    "5414": "",                             # 3件・¥36,600
    "5456-QA": "",                          # 2件・¥36,555
    "5210-Q": "",                           # 3件・¥36,000
    "4247-H": "",                           # 3件・¥35,800
    "4104-B": "",                           # 5件・¥34,990
    "5478-A": "",                           # 2件・¥34,695
    "5417-A": "",                           # 7件・¥33,000
    "5479-A": "",                           # 3件・¥32,000
    "5338-HA": "",                          # 3件・¥29,800
    "6014": "",                             # 18件・¥28,900
    "5408-A": "",                           # 7件・¥28,000
    "5565": "",                             # 3件・¥26,500
    "4003": "",                             # 9件・¥25,000
    "5312-A": "",                           # 7件・¥24,900
    "4017-D": "",                           # 28件・¥24,690
    "5422-B": "",                           # 4件・¥23,500
    "4023": "",                             # 2件・¥23,100
    "5278-A": "",                           # 4件・¥22,500
    "4093-B": "",                           # 12件・¥22,450
    "4017": "",                             # 5件・¥22,000
    "5339-HA": "",                          # 3件・¥22,000
    "5113-A": "",                           # 3件・¥22,000
    "4067": "",                             # 5件・¥21,500
    "5372-A": "",                           # 2件・¥21,500
    "5205-A": "",                           # 2件・¥21,250
    "4002": "",                             # 20件・¥21,000
    "4013": "",                             # 7件・¥21,000
    "4092-B": "",                           # 7件・¥21,000
    "4037": "",                             # 3件・¥21,000
    "5394": "",                             # 3件・¥18,000
    "01450": "",                            # 16件・¥17,750
    "5174-A": "",                           # 2件・¥17,449
    "4117-B": "",                           # 2件・¥17,400
    "5340": "",                             # 2件・¥17,200
    "5285-A": "",                           # 2件・¥17,040
    "91235": "",                            # 3件・¥17,000
    "6023": "",                             # 2件・¥16,999
    "5183": "",                             # 2件・¥16,750
    "94305": "",                            # 7件・¥16,500
    "5373-A": "",                           # 2件・¥16,500
    "4008": "",                             # 6件・¥16,390
    "5171": "",                             # 4件・¥16,250
    "5171-A": "",                           # 28件・¥16,000
    "5394-HA": "",                          # 5件・¥16,000
    "4220": "",                             # 5件・¥15,500
    "4246-H": "",                           # 2件・¥15,000
    "5183-A": "",                           # 11件・¥14,999
    "5130-Q": "",                           # 2件・¥14,700
    "5113": "",                             # 15件・¥14,500
    "5391-HA": "",                          # 2件・¥14,200
    "5143-A": "",                           # 2件・¥13,990
    "5102": "",                             # 4件・¥13,900
    "4095-B": "",                           # 2件・¥13,550
    "4085": "",                             # 3件・¥13,500
    "02461": "",                            # 22件・¥12,900
    "4123-B": "",                           # 2件・¥12,340
    "01452": "",                            # 4件・¥12,100
    "5170-A": "",                           # 7件・¥12,000
    "6035": "",                             # 2件・¥12,000
    "4107-B": "",                           # 6件・¥11,500
    "4005": "",                             # 2件・¥10,890
    "01451": "",                            # 4件・¥10,747
    "4101-B": "",                           # 2件・¥9,500
    "05972": "",                            # 2件・¥9,040
}


def _extract_chanel_sunglasses(title: str) -> str | None:
    """シャネルのサングラスの型番（5417-A / 4017-D / 4002 / 旧型 01450）。メガネの 3xxx・2xxx は拾わない"""
    t = unicodedata.normalize("NFKC", title or "")
    for m in _CHANEL_SUN_RE.finditer(t):
        ch, num, suf = m.group(1), m.group(2), (m.group(3) or "").upper() + (m.group(4) or "").upper()
        if suf in ("AA",):
            suf = "A"
        if num[0] in "23":
            continue                     # メガネ（光学フレーム）の型番
        key = num + ("-" + suf if suf else "")
        if ch or suf or _CHANEL_COLOR_RE.match(t, m.end()) or key in _CHANEL_SUN_MODELS:
            return key
    for m in _CHANEL_OLD_RE.finditer(t):
        if m.group(1) in _CHANEL_SUN_MODELS:
            return m.group(1)
    return None


# ── クリスチャンディオールのサングラス: 型番は少なく、モデル名（ソーリアル・グロッシー1 等）で呼ばれることが多い ──
#   型番: ディオールオム期の 0049/S・0175S（0 + 3桁 + S）→ 0049S、ヴィンテージの CD 2289・CD7041J（CD + 4桁）→ CD2289。
#   CD7xxx（CD7763J・CD7014J 等）はメガネ（光学フレーム）の型番なので拾わない（2026-11-09 メルカリ売り切れで「眼鏡フレーム」を確認）。
#   「CD」の無い4桁（「ディオール 2289 ヴィンテージ」）は代表型番に限り、年と区別できない 2000〜2099 以外だけ拾う。
#   型番が無ければモデル名（_MODEL_NAMES。ソーリアル・グロッシー1・30モンテーニュ 等）で照合する
#   現行品の「DiorSoReal S1U」等の末尾（S1U・R2F・M2U 等）はカラー・形の版違いで件数が少ないため、モデル名にまとめる
_DIOR_HOMME_RE = re.compile(r'(?<![A-Za-z0-9.,¥￥\-/])(0\d{3})\s?/?\s?S(?![A-Za-z0-9])', re.IGNORECASE)
_DIOR_CD_RE = re.compile(r'(?<![A-Za-z0-9])CD[\s\-]?(\d{4})(?:[A-Z](?![A-Za-z]))?(?![0-9A-Za-z])', re.IGNORECASE)
_DIOR_SUN_MODELS: dict[str, str] = {
    "0049S": "",                            # 5件・¥18,500
    "0055S": "",                            # 3件・¥18,000
    "0007S": "",                            # 2件・¥14,900
    "CD2854": "",                           # 2件・¥9,900
    "CD2958": "",                           # 2件・¥9,250
    "CD2259": "",                           # 2件・¥8,375
    "CD2181": "",                           # 3件・¥7,780
    "CD2849": "",                           # 2件・¥7,350
    "CD2121": "",                           # 2件・¥6,890
    "CD2320": "",                           # 2件・¥6,880
    "CD2153": "",                           # 2件・¥6,486
    "CD2497": "",                           # 2件・¥6,030
    "CD2917": "",                           # 2件・¥5,980
    "CD2281": "",                           # 2件・¥4,950
    "CD2152": "",                           # 2件・¥4,950
    "CD2289": "",                           # 5件・¥3,964
    "CD2844": "",                           # 2件・¥3,950
    "CD2801": "",                           # 2件・¥3,950
    "CD2228": "",                           # 2件・¥3,887
    "CD2250": "",                           # 3件・¥3,800
    "CD2661": "",                           # 2件・¥3,470
    "CD2601": "",                           # 売り切れ集計で0件（2026-10 登録分）
}
# 「CD」の無いヴィンテージの4桁（2181A のように末尾に1文字付くものも）。代表型番だけ、2000〜2099 は年と区別できないため除く
_DIOR_VINTAGE = sorted({k[2:6] for k in _DIOR_SUN_MODELS if k.startswith("CD") and int(k[2:6]) >= 2100})
_DIOR_SUN_BARE_RE = re.compile(
    r'(?<![A-Za-z0-9.,¥￥\-/])(' + "|".join(_DIOR_VINTAGE) + r')(?:[A-Z](?![A-Za-z]))?(?![A-Za-z0-9.,円年\-/])'
) if _DIOR_VINTAGE else None


def _extract_dior_sunglasses(title: str) -> str | None:
    """ディオールのサングラスの型番（0049S / CD2289）、無ければモデル名（ソーリアル 等）"""
    t = unicodedata.normalize("NFKC", title or "")
    m = _DIOR_HOMME_RE.search(t)
    if m:
        return m.group(1) + "S"
    m = _DIOR_CD_RE.search(t)
    if m:
        if m.group(1).startswith("7"):
            return None          # CD7xxx はメガネ（光学フレーム。CD7763J・CD7014J 等）。モデル名でも照合しない
        return "CD" + m.group(1)
    if _DIOR_SUN_BARE_RE is not None:
        m = _DIOR_SUN_BARE_RE.search(t)
        if m:
            return "CD" + m.group(1)
    return extract_name(t, "クリスチャンディオール")


# ── ジャンポールゴルチエのサングラス（1990〜2000年代のヴィンテージが中心）: 56-0006（2桁 - 4桁）──
#   55-・56-・58- で始まるものをサングラスの型番とする。57- はメガネ（光学フレーム）の型番とされるため拾わない
#   （海外の中古市場での表記。メルカリ売り切れでも 57-0096 は「メガネフレーム」。サングラスとして売れた 57- は3件だけ。2026-11 確認）。
#   表記ゆれ: 56-0006 / 56 0006 / 56ー0006（長音記号）。ハイフンの無い6桁（560006）は他の数字と区別できないため拾わない
_GAULTIER_SUN_RE = re.compile(r'(?<![0-9A-Za-z\-])(5[5-8])\s?[\-ー‐−–]?\s?(\d{4})(?![0-9])')
_GAULTIER_SUN_MODELS: dict[str, str] = {
    "56-8171": "",                          # 3件・¥52,000
    "56-6104": "",                          # 5件・¥49,000
    "56-8101": "",                          # 2件・¥48,900
    "56-0174": "",                          # 17件・¥40,000
    "56-5102": "",                          # 3件・¥39,000
    "56-6106": "",                          # 2件・¥37,000
    "56-0104": "",                          # 2件・¥35,500
    "55-4178": "",                          # 2件・¥35,000
    "56-6001": "",                          # 4件・¥31,850
    "56-5105": "",                          # 5件・¥31,000
    "56-6203": "",                          # 5件・¥31,000
    "58-0003": "",                          # 2件・¥30,900
    "56-5203": "",                          # 2件・¥30,328
    "56-0002": "",                          # 2件・¥29,394
    "56-3175": "",                          # 2件・¥28,800
    "56-9174": "",                          # 2件・¥28,000
    "56-6101": "",                          # 3件・¥27,800
    "56-4178": "",                          # 2件・¥27,750
    "56-7116": "",                          # 4件・¥27,100
    "56-4177": "",                          # 6件・¥26,950
    "58-5102": "",                          # 3件・¥26,600
    "56-5108": "",                          # 3件・¥25,900
    "56-4174": "",                          # 2件・¥25,650
    "58-5201": "",                          # 4件・¥25,000
    "56-6105": "",                          # 3件・¥24,999
    "56-7109": "",                          # 2件・¥24,750
    "56-0015": "",                          # 3件・¥24,444
    "56-7107": "",                          # 2件・¥24,350
    "56-0006": "",                          # 7件・¥24,000
    "58-5174": "",                          # 3件・¥23,100
    "56-6202": "",                          # 3件・¥21,000
    "56-7201": "",                          # 2件・¥20,500
    "56-0027": "",                          # 2件・¥19,300
    "56-6102": "",                          # 2件・¥18,160
    "56-0024": "",                          # 4件・¥17,500
    "56-0017": "",                          # 3件・¥17,500
    "56-0039": "",                          # 2件・¥16,999
    "56-0066": "",                          # 2件・¥12,575
    "56-5106": "",                          # 6件・¥12,490
    "58-0011": "",                          # 2件・¥12,250
    "58-5106": "",                          # 3件・¥11,211
}


def _extract_gaultier_sunglasses(title: str) -> str | None:
    """ジャンポールゴルチエのサングラスの型番（56-0006 / 58-5201）。メガネの 57- は拾わない"""
    t = unicodedata.normalize("NFKC", title or "")
    for m in _GAULTIER_SUN_RE.finditer(t):
        if m.group(1) == "57":
            continue
        if not re.search(r'[\s\-ー‐−–]', m.group(0)):
            continue                    # 560006 のように区切りの無い6桁は拾わない
        return m.group(1) + "-" + m.group(2)
    return None


# ── EYEVAN 7285 のサングラス: 「7285」の後に書かれる3桁（319 / 770 / 140）か 1xxx（1158 / 1196）の番号 ──
#   番号の後の 100・301・801・805・820・900 等はカラー（c.100 / col.301 / 1158_805）、47□22-145 の 145・140 はサイズなので、
#   「7285」の後で最初に出る、c./col./color の付かない・□の前後でない番号を型番とする。
#   正規の形は「7285-319」（検索語は「EYEVAN 7285-319」）。353E・717W のような末尾の英字は同じ型番として扱う。
#   EYEVAN（通常ライン）・EYEVAN 7285 とも同じフレームをメガネとしても売るため、メガネ専用の番号の区別は無い
#   （サングラスかどうかは config の必須語「サングラス」で判断する）
_EYEVAN7285_NUM_RE = re.compile(r'(?<![0-9A-Za-z.□◻\-])(\d{3,4})(?:[A-Za-z](?![A-Za-z]))?(?![0-9□◻])')
_EYEVAN7285_COLOR_BEFORE_RE = re.compile(r'(?:c|co|col|color|colour|カラー)\s*[.:]?\s*$|[□◻\-/_]\s*$', re.IGNORECASE)
_EYEVAN_SUN_MODELS: dict[str, str] = {
    "7285-185": "",                         # 2件・¥112,500
    "7285-197": "",                         # 7件・¥65,580
    "7285-1017": "",                        # 2件・¥62,000
    "7285-140": "",                         # 4件・¥61,250
    "7285-319": "",                         # 6件・¥60,000
    "7285-351": "",                         # 5件・¥59,000
    "7285-799": "",                         # 3件・¥48,000
    "7285-340": "",                         # 3件・¥47,600
    "7285-1158": "",                        # 2件・¥42,500
    "7285-359": "",                         # 5件・¥40,000
    "7285-772": "",                         # 2件・¥31,160
    "7285-770": "",                         # 6件・¥29,000
    "7285-740": "",                         # 3件・¥28,000
    "7285-785": "",                         # 2件・¥21,220
    "7285-766": "",                         # 5件・¥15,000
}


def _extract_eyevan_sunglasses(title: str) -> str | None:
    """EYEVAN 7285 の型番（7285-319）。番号が無ければ通常ラインのモデル名（ウェッブ 等）"""
    t = unicodedata.normalize("NFKC", title or "")
    m = re.fullmatch(r'\s*7285[\s\-]?(\d{3,4})\s*', t)
    if m:                          # 型番だけが渡されたとき（画面の型番候補・自由入力の判定）
        return "7285-" + m.group(1)
    i = t.find("7285")
    if i >= 0:
        rest = t[i + 4:]
        for m in _EYEVAN7285_NUM_RE.finditer(rest):
            num = m.group(1)
            if _EYEVAN7285_COLOR_BEFORE_RE.search(rest[:m.start()]):
                continue
            if rest[m.end():m.end() + 1] in ("円", "年") or re.match(r'\s*(?:円|年|mm|サイズ)', rest[m.end():]):
                continue
            if len(num) == 4 and not ("1000" <= num <= "1299"):
                continue
            return "7285-" + num
    return extract_name(t, "EYEVAN")


# ── ルイ・ヴィトンのバッグ: 型番（M+5桁 / ダミエ等の N+5桁）を優先し、無ければモデル名で照合 ──
# メルカリの表記ゆれ: M40995 / m40995 / M 40995 / M-40995 / Ｍ４０９９５（全角）→ M40995
# 財布・小物にも同じ形式の型番があるため（M60017 ポルトフォイユ・サラ、M62650 ポシェット・クレ 等）、
# 型番はタイトルに「バッグ」「ショルダー」「トート」等の語か、バッグのモデル名があるときだけ採用する。
# 財布・小物そのものは config.json の exclude_words（財布・ウォレット・キーケース 等）で除外する。
# サングラスの型番（Z1502W 等）はジャンルが「サングラス」のときだけ _PATTERNS で照合する
_LV_NUMBER_RE = re.compile(r'(?<![A-Za-z0-9])([MN])[\s\-]?(\d{5})(?!\d)', re.IGNORECASE)
_LV_NUMBER_ONLY_RE = re.compile(r'\s*([MN])[\s\-]?(\d{5})\s*', re.IGNORECASE)
_LV_BAG_WORDS_RE = re.compile(
    r'バッグ|バック(?!パック)|かばん|カバン|鞄|ショルダー|トート|ハンド|ボストン|リュック|バックパック|クラッチ'
    r'|ポシェット|ポーチ|メッセンジャー|ボディ|ウエスト|斜め?[掛が]け|肩掛け|手提げ|2WAY'
    r'|(?<![A-Z])(?:BAG|SAC|TOTE|SHOULDER|POCHETTE|BACKPACK|CLUTCH|BOSTON)(?![A-Z])'
    # サイズが無くてモデル名としては照合しない書き方（「スピーディ M41526」）も、バッグの型番として採用する
    r'|スピーディ|ネ(?:ヴァ|バ|ヴ)ー?フル|キーポー?ル|(?<![ァ-ヶー])アルマ(?!ー?ニ)|カプシーヌ|オン\s*ザ\s*ゴー?'
    r'|パーム\s*スプリング|(?<![A-Z])(?:SPEEDY|NEVER\s*FULL|KEEPALL|ALMA|CAPUCINES?|ON\s*THE\s*GO)(?![A-Z])',
    re.IGNORECASE)


def _extract_lv_bag(title: str) -> str | None:
    """ルイ・ヴィトンのバッグの型番（M40995 / N41358）、無ければモデル名（スピーディ30 等）"""
    t = unicodedata.normalize("NFKC", title or "")
    m = _LV_NUMBER_ONLY_RE.fullmatch(t)
    if m:                          # 型番だけが渡されたとき（画面の型番候補・自由入力の判定）
        return m.group(1).upper() + m.group(2)
    name = extract_name(t, "ルイヴィトン")
    m = _LV_NUMBER_RE.search(t)
    if m and (name or _LV_BAG_WORDS_RE.search(t)):
        return m.group(1).upper() + m.group(2)
    return name


# ジャンル別に型番の照合を切り替えるブランド: (ブランド, ジャンル) → 抽出関数。
# ここに無い組み合わせは従来どおり _PATTERNS[ブランド] で照合する
_CATEGORY_EXTRACTORS = {
    ("グッチ", "時計"): _extract_gucci_watch,
    ("グッチ", "サングラス"): _extract_gucci_sunglasses,
    ("セリーヌ", "サングラス"): _extract_celine_sunglasses,
    ("プラダ", "サングラス"): _extract_prada_sunglasses,
    ("ミュウミュウ", "サングラス"): _extract_miumiu_sunglasses,
    ("バーバリー", "サングラス"): _extract_burberry_sunglasses,
    ("ヴェルサーチ", "サングラス"): _extract_versace_sunglasses,
    ("トムフォード", "サングラス"): _extract_tomford_sunglasses,
    ("オークリー", "サングラス"): _extract_oakley_sunglasses,
    ("ロエベ", "サングラス"): _extract_loewe_sunglasses,
    ("バレンシアガ", "サングラス"): _extract_balenciaga_sunglasses,
    ("ジャンポールゴルチエ", "サングラス"): _extract_gaultier_sunglasses,
    ("EYEVAN", "サングラス"): _extract_eyevan_sunglasses,
    ("クリスチャンディオール", "サングラス"): _extract_dior_sunglasses,
    ("シャネル", "サングラス"): _extract_chanel_sunglasses,
    ("ルイヴィトン", "サングラス"): _extract_lv_sunglasses,
    ("ルイヴィトン", "バッグ"): _extract_lv_bag,
}

# ルイ・ヴィトンのバッグの代表型番とモデル名（画面の「型番の候補」に愛称付きで出す）。
# 相場表プロジェクトの型番逆引きマスター（バッグの行）と定番品から選んだもの（2026-10）。
# メルカリ売却済みでの件数は未確認なので、相場が出ない型番は自然にスキップされる
_LV_BAG_NUMBERS: dict[str, str] = {
    "M40995": "ネヴァーフルMM", "N41358": "ネヴァーフルMM（ダミエ）", "N41359": "ネヴァーフルPM（ダミエ）",
    "M41528": "スピーディ25", "M41526": "スピーディ30", "M41524": "スピーディ35", "M41522": "スピーディ40",
    "N41531": "スピーディ30（ダミエ）", "M41113": "スピーディバンドリエール25", "M41112": "スピーディバンドリエール30",
    "M53151": "アルマ", "M53152": "アルマBB", "N41221": "アルマBB（ダミエ）",
    "M51980": "ポシェットアクセソワール", "M44813": "ミュルティポシェットアクセソワール",
    "M44875": "ポシェットメティス", "M61276": "ポシェットフェリシー", "N55213": "エヴァ（ダミエ）",
    "M44576": "オンザゴーGM", "M44391": "ドーフィーヌMM",
    "M41428": "キーポル45", "M41426": "キーポル50", "M41424": "キーポル55", "M41422": "キーポル60",
    "M42224": "ノエ", "M42226": "プチノエ",
    "M44871": "パームスプリングスPM", "M44873": "パームスプリングスミニ", "M51137": "ミニモンスリ",
    "M51154": "バティニョールオリゾンタル", "M51156": "バティニョール",
    "M51265": "ブローニュ30", "M51274": "トロカデロ27", "M51272": "トロカデロ30",
    "M51145": "ルーピングMM", "M51146": "ルーピングGM",
    "M42256": "ソミュール30", "M51140": "サックプラ", "M40007": "ポパンクールオ",
    # 2026-10 追加（相場表プロジェクトの逆引きマスターにある型番を優先し、定番品で補ったもの）
    # ネヴァーフル（旧型モノグラム・ダミエ・ダミエアズール）
    "M40155": "ネヴァーフルPM", "M40156": "ネヴァーフルMM", "M40157": "ネヴァーフルGM",
    "N51105": "ネヴァーフルMM（ダミエ）", "N51106": "ネヴァーフルGM（ダミエ）",
    "N51107": "ネヴァーフルMM（ダミエアズール）",
    # スピーディ
    "N41532": "スピーディ25（ダミエ）", "N41523": "スピーディ35（ダミエ）",
    "N41533": "スピーディ30（ダミエアズール）",
    "M41111": "スピーディバンドリエール35",
    "N41368": "スピーディバンドリエール25（ダミエ）", "N41367": "スピーディバンドリエール30（ダミエ）",
    "M41534": "ミニスピーディ", "M61252": "ナノスピーディ",
    # アルマ
    "M51130": "アルマ（旧型）", "N53151": "アルマPM（ダミエ）", "M82717": "ナノアルマ",
    # ポシェット類
    "N51985": "ポシェットアクセソワール（ダミエ）", "N51986": "ポシェットアクセソワール（ダミエアズール）",
    "M44840": "ミュルティポシェットアクセソワール（カーキ）",
    "N63032": "ポシェットフェリシー（ダミエ）", "N63106": "ポシェットフェリシー（ダミエアズール）",
    "M95567": "エヴァ", "N55214": "エヴァ（ダミエアズール）",
    "M51854": "ポシェットツインPM", "M51852": "ポシェットツインGM",
    "M51855": "ポシェットフロランティーヌ", "M51870": "ポシェットガンジュ",
    "M51827": "マルリードラゴンヌPM", "M51825": "マルリードラゴンヌGM",
    "M51828": "ポシェットマルリーバンドリエール",
    # キーポル
    "M41418": "キーポルバンドリエール45", "M41416": "キーポルバンドリエール50",
    "M41414": "キーポルバンドリエール55", "M41412": "キーポルバンドリエール60",
    "N41429": "キーポル55（ダミエ）", "N41413": "キーポルバンドリエール55（ダミエグラフィット）",
    # ノエ・バティニョール・ルーピング 等
    "M44020": "ネオノエ", "M41346": "ナノノエ",
    "M51153": "バティニョールヴェルティカル", "M51147": "ルーピングPM",
    "M51253": "カルトシエール22", "M42254": "ソミュール35",
    "M51256": "ミュゼット", "M51257": "ミュゼットタンゴ", "M40009": "ポパンクール",
    # トート・ショルダーの定番（旧型含む）
    "M51151": "カバメゾ", "M51148": "カバピアノ", "M42238": "バケットPM", "M42236": "バケットGM",
    "M51155": "ルコ", "M45266": "ダヌーブ", "M45244": "ナイル",
    "M45254": "リポーターPM", "M45252": "リポーターGM", "M42250": "ソローニュ", "M51221": "ブロワ",
    "M51227": "ジュヌフィーユPM", "M51226": "ジュヌフィーユMM", "M51225": "ジュヌフィーユGM",
    "M51234": "シャンティPM", "M51233": "シャンティMM", "M51232": "シャンティGM",
    "M51290": "ドルーオ", "M51845": "コンピエーニュ28", "M51185": "モンソー28",
    "M51108": "サックショッピング", "M41140": "サックシャス", "M51102": "バビロン",
    "M51172": "ヴァヴァンPM", "M51170": "ヴァヴァンGM", "M47270": "ドーヴィル",
    "M51127": "エリプスPM", "M51126": "エリプスMM",
    "M56688": "トータリーPM", "M56689": "トータリーMM", "M56690": "トータリーGM",
    "M40249": "アーツィーMM", "M56382": "ガリエラPM", "M56381": "ガリエラGM",
    "M40145": "パレルモPM", "M40146": "パレルモGM",
    # メンズ
    "N45255": "ナヴィグリオ", "N51994": "ジェロニモス（ダミエ）", "M40106": "メッセンジャーボスフォールPM",
    "N41259": "タダオPM", "N41719": "アヴェニュースリングバッグ", "M44478": "ソフトトランク",
}

# 画面の「型番」候補に添える愛称（ブランド → {型番: 愛称}）。例: RB2140 → ウェイファーラー
MODEL_NUMBER_LABELS: dict[str, dict[str, str]] = {
    "レイバン": {k: v for k, v in _RAYBAN_MODELS.items() if v},
    "グッチ": {k: v for k, v in {**_GUCCI_WATCH_MODELS, **_GUCCI_SUN_MODELS}.items() if v},
    "セリーヌ": {k: v for k, v in _CELINE_SUN_MODELS.items() if v},
    "ルイヴィトン": {**_LV_BAG_NUMBERS, **{k: v for k, v in _LV_SUN_MODELS.items() if v}},
    "プラダ": {k: v for k, v in _PRADA_SUN_MODELS.items() if v},
    "ミュウミュウ": {k: v for k, v in _MIUMIU_SUN_MODELS.items() if v},
    "バーバリー": {k: v for k, v in _BURBERRY_SUN_MODELS.items() if v},
    "ヴェルサーチ": {k: v for k, v in _VERSACE_SUN_MODELS.items() if v},
    "トムフォード": {k: v for k, v in _TOMFORD_SUN_MODELS.items() if v},
    "オークリー": {k: v for k, v in _OAKLEY_SUN_MODELS.items() if v},
    "ロエベ": {k: v for k, v in _LOEWE_SUN_MODELS.items() if v},
    "バレンシアガ": {k: v for k, v in _BALENCIAGA_SUN_MODELS.items() if v},
    "シャネル": {k: v for k, v in _CHANEL_SUN_MODELS.items() if v},
    "クリスチャンディオール": {k: v for k, v in _DIOR_SUN_MODELS.items() if v},
    "ジャンポールゴルチエ": {k: v for k, v in _GAULTIER_SUN_MODELS.items() if v},
    "EYEVAN": {k: v for k, v in _EYEVAN_SUN_MODELS.items() if v},
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
    # グッチは時計とサングラスの型番（画面では選択中のジャンルの型番だけを出す。MODEL_NUMBER_CATEGORIES）
    "グッチ": list(_GUCCI_WATCH_MODELS) + list(_GUCCI_SUN_MODELS),
    "セリーヌ": list(_CELINE_SUN_MODELS),
    "ルイヴィトン": list(_LV_BAG_NUMBERS) + list(_LV_SUN_MODELS),
    # サングラス（2026-11 追加。メルカリ売り切れで2件以上の型番）
    "プラダ": list(_PRADA_SUN_MODELS),
    "ミュウミュウ": list(_MIUMIU_SUN_MODELS),
    "バーバリー": list(_BURBERRY_SUN_MODELS),
    "ヴェルサーチ": list(_VERSACE_SUN_MODELS),
    "トムフォード": list(_TOMFORD_SUN_MODELS),
    "オークリー": list(_OAKLEY_SUN_MODELS),
    "ロエベ": list(_LOEWE_SUN_MODELS),
    "バレンシアガ": list(_BALENCIAGA_SUN_MODELS),
    "シャネル": list(_CHANEL_SUN_MODELS),
    "クリスチャンディオール": list(_DIOR_SUN_MODELS),
    "ジャンポールゴルチエ": list(_GAULTIER_SUN_MODELS),
    "EYEVAN": list(_EYEVAN_SUN_MODELS),
}

# 型番の候補のうち、特定のジャンル専用のもの（ブランド → {型番: ジャンル}）。
# 画面では選択中のジャンルの型番だけを出し、リサーチでもそのジャンルのキーワードにだけ使う
MODEL_NUMBER_CATEGORIES: dict[str, dict[str, str]] = {
    "グッチ": {**{m: "時計" for m in _GUCCI_WATCH_MODELS}, **{m: "サングラス" for m in _GUCCI_SUN_MODELS}},
    "セリーヌ": {m: "サングラス" for m in _CELINE_SUN_MODELS},
    "ルイヴィトン": {**{m: "バッグ" for m in _LV_BAG_NUMBERS}, **{m: "サングラス" for m in _LV_SUN_MODELS}},
    "プラダ": {m: "サングラス" for m in _PRADA_SUN_MODELS},
    "ミュウミュウ": {m: "サングラス" for m in _MIUMIU_SUN_MODELS},
    "バーバリー": {m: "サングラス" for m in _BURBERRY_SUN_MODELS},
    "ヴェルサーチ": {m: "サングラス" for m in _VERSACE_SUN_MODELS},
    "トムフォード": {m: "サングラス" for m in _TOMFORD_SUN_MODELS},
    "オークリー": {m: "サングラス" for m in _OAKLEY_SUN_MODELS},
    "ロエベ": {m: "サングラス" for m in _LOEWE_SUN_MODELS},
    "バレンシアガ": {m: "サングラス" for m in _BALENCIAGA_SUN_MODELS},
    "シャネル": {m: "サングラス" for m in _CHANEL_SUN_MODELS},
    "クリスチャンディオール": {m: "サングラス" for m in _DIOR_SUN_MODELS},
    "ジャンポールゴルチエ": {m: "サングラス" for m in _GAULTIER_SUN_MODELS},
    "EYEVAN": {m: "サングラス" for m in _EYEVAN_SUN_MODELS},
}

# ── ブランド名の表記ゆれ（他ブランド混入チェック用） ──────────────────────
# キー: config.json の brand_name / キーワード先頭語。値: タイトルに含まれていればOKとする表記
BRAND_ALIASES: dict[str, list[str]] = {
    "デュベティカ": ["デュベティカ", "DUVETICA", "デュべティカ", "デュベチカ", "デュペティカ", "ドゥベティカ"],
    "ノースフェイス": ["ノースフェイス", "ノースフェース", "NORTH FACE", "NORTHFACE", "TNF",
                    "ノース・フェイス", "ノース フェイス"],
    # モデル名（英字）で相場を取るブランド。英字だけのタイトル（MONCLER MAYA 等）も
    # メルカリ相場のブランド名チェックを通るようにする。「モンクレ」は略記（「モンクレ ダウン」等。売り切れに実例あり）
    "モンクレール": ["モンクレール", "モンクレ", "MONCLER"],
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
    # 「ルイビトン」等の誤記も拾う。LV は「SILVER」等に部分一致するため単語境界付きで照合する（_WORD_ALIASES）
    "ルイヴィトン": ["ルイヴィトン", "ルイ・ヴィトン", "ルイ ヴィトン", "ヴィトン", "ビトン",
                  "LOUIS VUITTON", "VUITTON", "LV"],
    "シャネル": ["シャネル", "CHANEL"],
    "オークリー": ["オークリー", "OAKLEY"],
    "トムフォード": ["トムフォード", "トム・フォード", "トム フォード", "TOM FORD", "TOMFORD"],
    "バーバリー": ["バーバリー", "BURBERRY"],
    "クリスチャンディオール": ["クリスチャンディオール", "ディオール", "DIOR"],
    "ヴェルサーチ": ["ヴェルサーチ", "ヴェルサーチェ", "VERSACE"],
    "リックオウエンス": ["リックオウエンス", "リック・オウエンス", "リック オウエンス", "RICK OWENS", "RICKOWENS"],
    "ジャンポールゴルチエ": ["ジャンポールゴルチエ", "ゴルチエ", "ゴルチェ", "GAULTIER"],   # ゴルチェ: 2026-11 追加（売り切れに多い表記）
    "ロエベ": ["ロエベ", "LOEWE"],
    "ミュウミュウ": ["ミュウミュウ", "MIUMIU", "MIU MIU"],
    "バレンシアガ": ["バレンシアガ", "BALENCIAGA"],
    # 2026-11 追加: 英字・カタカナのどちらで書かれたタイトルもメルカリ相場のブランド名チェックを通るようにする
    "EYEVAN": ["EYEVAN", "アイヴァン", "EYE VAN"],
    "ayame": ["ayame", "アヤメ"],
    # HERNO は「Chernobyl」等に部分一致するため、前後に英字が続かないときだけ一致とする（_WORD_ALIASES）
    "ヘルノ": ["ヘルノ", "HERNO"],
    # 英字だけのタイトル（TATRAS POLITEAMA 等）もメルカリ相場のブランド名チェックを通るようにする
    "タトラス": ["タトラス", "TATRAS"],
    # 英字だけのタイトル（CANADA GOOSE JASPER PARKA 等。売り切れの約3割）もメルカリ相場のブランド名チェックを通るようにする
    # （変更前は未登録で「カナダグース」と書かれたタイトルだけが相場に入っていた）。「カナダグ-ス」は実際の誤記
    "カナダグース": ["カナダグース", "カナダ グース", "カナダ・グース", "カナダグ-ス", "CANADA GOOSE", "CANADAGOOSE",
                  "CANADA-GOOSE"],
}

# 前後に英字が続かないときだけ一致とする表記（単語境界付きで照合する）。ここに無い表記は従来どおり部分一致
_WORD_ALIASES = {"HERNO", "LV"}
_WORD_ALIAS_RE = {a: re.compile(r'(?<![A-Za-z])' + re.escape(a) + r'(?![A-Za-z])', re.IGNORECASE)
                  for a in _WORD_ALIASES}


def has_word(title: str, word: str) -> bool:
    """タイトルに語が含まれるか（大文字小文字は区別しない）。required_words の照合用。
    _WORD_ALIASES の語（LV・HERNO）は前後に英字が続かないときだけ一致とする（SILVER の lv 等を除く）"""
    # 全角英数字・半角カナも同じ語として扱う（ＬＯＵＩＳ ＶＵＩＴＴＯＮ / ﾀﾞｳﾝ）
    title = unicodedata.normalize("NFKC", title or "")
    word = unicodedata.normalize("NFKC", word or "")
    rx = _WORD_ALIAS_RE.get(word.upper())
    if rx is not None:
        return bool(rx.search(title))
    return word.lower() in title.lower()


def brand_aliases(brand: str) -> list[str]:
    """ブランド名の表記ゆれ一覧（未登録ならブランド名そのもの）"""
    return BRAND_ALIASES.get(brand, [brand] if brand else [])


def has_brand(title: str, brand: str) -> bool:
    """タイトルにブランド名（いずれかの表記）が含まれるか。大文字小文字は区別しない"""
    title = unicodedata.normalize("NFKC", title or "")   # 全角英字（ＶＵＩＴＴＯＮ）も同じ表記として扱う
    t = title.lower()
    for a in brand_aliases(brand):
        if a in _WORD_ALIAS_RE:
            if _WORD_ALIAS_RE[a].search(title):
                return True
        elif a.lower() in t:
            return True
    return False


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
    """検索に使うモデル名の一覧: [カタカナ名, 英字名, 英字の綴り違い…]。
    英字名が無い・カタカナ名と同じ（正規化後）・モデル名が英字のみ → 1件だけ返す。
    型番（数字を含み名称リストに無いもの）も [model] のまま。
    """
    kana, en = model_names(model, brand)
    names = [kana]
    if en and _n(en) != _n(kana):
        names.append(en)
    # 英字の綴り違いで出品の多いもの（_SEARCH_VARIANTS。タトラスの VERENA 等）も検索する
    for v in _SEARCH_VARIANTS.get(brand, {}).get(kana, []):
        if all(_n(v) != _n(x) for x in names):
            names.append(v)
    return names


def extract_name(title: str, brand: str) -> str | None:
    """モデル名だけを照合して返す（型番は見ない）。どの表記で見つかっても正規名（カタカナ）を返す"""
    if brand in _STRICT_COMPILED:
        if brand in _NFKC_NAME_BRANDS:
            title = unicodedata.normalize("NFKC", title or "")
        rule = _TITLE_RULES.get(brand)
        if rule is not None:
            key = _n(title)
            for k, name in _STRICT_NAME_KEYS[brand].items():
                if key == _n(k):
                    return name
            if not rule(title):
                return None
        for name, pats in _STRICT_COMPILED[brand]:
            if any(p.search(title or "") for p in pats):
                return name
        if brand == "モンクレール":
            # モデル名が無ければ、品番からモデル名を引く（_MONCLER_NUMBER_NAMES。キッズ・コラボ等は除く）
            num = _moncler_number(title) or _n(title)      # 品番だけの文字列（4036605）も引く
            name = _MONCLER_NUMBER_NAMES.get(num)
            if name and re.search(_MC_NOT_LIKE + _MC_NO_COLLAB, title or "", re.IGNORECASE):
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
# グッチのサングラスも同様（「グッチ GG0022S」で検索すると GG0022SA も混ざる）。
# セリーヌのサングラスも同様（「セリーヌ CL40034F」で検索すると CL4003IN も混ざる）。
# ルイ・ヴィトンのバッグも同様（「ルイヴィトン M40995」の検索結果に別の型番の売却済みが混ざるのを除く）。
# モンクレールも同様（「モンクレール 4036605」の検索結果には品番の違う売却済みが多く混ざる。
# 品番がタイトルに載る売却済みは少ないため、多くはモデル名の相場で取り直す。name_fallback）。
_EXACT_NUMBER_BRANDS = {"レイバン", ("グッチ", "時計"), ("グッチ", "サングラス"), ("セリーヌ", "サングラス"),
                        ("ルイヴィトン", "バッグ"), "モンクレール",
                        # サングラス（2026-11 追加）: 末尾の版違い（SK・F・-A 等）や近い番号が検索結果に混ざるため
                        ("ルイヴィトン", "サングラス"),
                        ("プラダ", "サングラス"), ("ミュウミュウ", "サングラス"), ("バーバリー", "サングラス"), ("ヴェルサーチ", "サングラス"),
                        ("トムフォード", "サングラス"), ("オークリー", "サングラス"), ("ロエベ", "サングラス"), ("バレンシアガ", "サングラス"),
                        ("シャネル", "サングラス"), ("クリスチャンディオール", "サングラス"), ("ジャンポールゴルチエ", "サングラス"),
                        ("EYEVAN", "サングラス")}


def exact_number_market(brand: str, category: str = "") -> bool:
    return brand in _EXACT_NUMBER_BRANDS or (brand, category) in _EXACT_NUMBER_BRANDS


def has_category_extractor(brand: str) -> bool:
    """ジャンルによって型番の照合が変わるブランドか（グッチ: 時計とサングラス、セリーヌ: サングラス、
    ルイヴィトン: バッグ）"""
    return any(b == brand for b, _c in _CATEGORY_EXTRACTORS)


def name_fits(model: str, brand: str, category: str) -> bool:
    """モデル名をそのジャンルのキーワードで使えるか。モデル名がジャンル限定のブランド
    （ルイヴィトン: バッグ）だけ判定し、登録済みのモデル名は限定先のジャンルでだけ使う"""
    only = _NAME_CATEGORIES.get(brand)
    if not only or category == only:
        return True
    return extract_name(model, brand) != model


def model_category(model: str, brand: str) -> str | None:
    """ジャンル限定のモデル名なら、そのジャンル（例: スピーディ30 → バッグ）"""
    only = _NAME_CATEGORIES.get(brand)
    return only if only and extract_name(model, brand) == model else None


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
    if brand in _NFKC_NUMBER_BRANDS:
        title = unicodedata.normalize("NFKC", title or "")
    if brand == "モンクレール":
        # 品番を優先し、無ければモデル名。キッズの品番がある出品はモデル名でも照合しない（大人用の相場と混ぜない）
        num = _moncler_number(title)
        if num == _MONCLER_KIDS:
            return None
        return num or extract_name(title, brand)
    for pattern in _PATTERNS.get(brand, []):
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            if brand == "レイバン":
                return _normalize_rayban(match.group(0))
            if brand == "タトラス":
                return _normalize_tatras(match.group(0))
            if brand == "カナダグース":
                return _normalize_canadagoose(match.group(0))
            sep = r'[\s\-]+' if brand in _STRIP_HYPHEN_BRANDS else r'\s+'
            return re.sub(sep, '', match.group(0)).upper()

    # 型番が無ければモデル名で照合（型番が取れた商品は型番の相場を優先する）。
    # モデル名がジャンル限定のブランド（ルイヴィトン=バッグ）は、他のジャンルではモデル名を使わない
    only = _NAME_CATEGORIES.get(brand)
    if only and category and category != only:
        return None
    return extract_name(title, brand)


def normalize(model: str) -> str:
    """型番を正規化（比較用）"""
    return re.sub(r'[\s\-_]', '', model).upper()


# ── 仕入れ候補・相場から外すタイトル（ブランド×ジャンル別。2026-11-09 追加）──────────
# config.json の exclude_words（部分一致）では書けない条件を正規表現で書く。main.py のタイトルフィルターと
# メルカリ相場の売却済みの絞り込み（title_filter）で使う。タイトルだけに使い、説明文・写真の文字には使わない
# （説明文の「メガネ拭き付き」「交換レンズ付き」等で本物の出品を落とさないため）。
#   類似品: ブランド名・モデル名の直後に「風」「似」「タイプ」「系」等（rick owens似 / Rickowens風 / straight jacket風）
#   クリスチャンディオール: メガネ（光学フレーム）・度付き（メルカリ売り切れで「サングラス」と並べて書かれることがある）
#   オークリー: 社外品・互換品の交換レンズ（「交換 偏光レンズ」「専用】UV400」等）、純正でもレンズだけの出品。
#     「交換レンズ付き」「レンズ交換可能」は本体の出品なので残す
_LIKE_SUFFIX = r'\s*(?:風|似|タイプ|系|っぽ|みたい|のよう|好き)'


def _like_re(brand: str, extra: tuple = ()) -> str:
    names = list(brand_aliases(brand)) + list(extra) + list(_MODEL_NAMES.get(brand, []))
    names = sorted({n for n in names if n and len(_n(n)) >= 3}, key=len, reverse=True)
    alt = "|".join(r'\s*'.join(re.escape(ch) for ch in re.sub(r'[\s・]', '', n)) for n in names)
    return r'(?:' + alt + r')' + _LIKE_SUFFIX


_TITLE_NG_SRC: dict[tuple[str, str], list[str]] = {
    ("クリスチャンディオール", "サングラス"): [
        r'メガネ|眼鏡|めがね|伊達|ダテ|度付|度有|度あり|度入|老眼|近視|遠近|ブルーライト|フレームのみ'
        r'|OPTICAL|オプティカル',
        _like_re("クリスチャンディオール"),
    ],
    ("オークリー", "サングラス"): [
        r'社外|互換|リプレイスメント|REPLACEMENT|スペアレンズ|替えレンズ|UV400|専用\s*[】\]]|用レンズ'
        r'|レンズのみ|レンズ単品|レンズ交換用|高品質',
        r'(?<!レンズ)交換(?!\s*(?:用\s*)?レンズ\s*付|\s*可)',
        _like_re("オークリー"),
    ],
    ("リックオウエンス", "サングラス"): [
        _like_re("リックオウエンス", ("リック", "RICK", "オウエンス", "OWENS")),
        r'ノーブランド|NO\s*BRAND|ノンブランド',
    ],
}
_TITLE_NG: dict[tuple[str, str], list] = {}


def has_title_rule(brand: str, category: str = "") -> bool:
    return (brand, category) in _TITLE_NG_SRC


def title_excluded(title: str, brand: str, category: str = "") -> bool:
    """仕入れ候補・相場の売却済みから外すタイトルか（上の _TITLE_NG_SRC）。対象外のブランド・ジャンルは常に False"""
    src = _TITLE_NG_SRC.get((brand, category))
    if not src:
        return False
    pats = _TITLE_NG.get((brand, category))
    if pats is None:
        pats = _TITLE_NG[(brand, category)] = [re.compile(p, re.IGNORECASE) for p in src]
    t = unicodedata.normalize("NFKC", title or "")
    return any(p.search(t) for p in pats)
