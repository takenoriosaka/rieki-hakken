# Mercari arbitrage sourcing tool

## プロジェクト概要
ヤフオク・メルカリ・セカストから転売案件を自動スキャンし、Webダッシュボードで確認するPythonツール。
（旧: メールで生徒に配信する仕組みだったが、ダッシュボード確認に一本化。メール配信機能は撤去済み）

## 実行方法
```bash
cd /Users/apple/program/rieki-hakken
source venv/bin/activate
python main.py --dry-run   # DB保存・ダッシュボード更新なしでスキャン確認
python main.py             # 本番実行（スキャン→DB保存→ダッシュボード再生成→git push まで自動）
python generate_dashboard.py --open  # ダッシュボードHTMLだけ再生成してブラウザで開く
```

## 現在の状態（2026-07-02時点）
- スキャン: 稼働中（メール送信は撤去済み、ダッシュボード確認に一本化）
- ウェブダッシュボード: 完成・公開済み
  - URL: https://takenoriosaka.github.io/rieki-hakken/
  - `python main.py` の本番実行時に自動で `generate_dashboard.py` を再生成し、
    `git add docs/index.html && git commit && git push` まで実行する
  - 手動更新したい場合は `python generate_dashboard.py && git add docs/index.html && git commit -m "Update dashboard" && git push`

## 未解決の課題
- **腕時計が0件問題**: オメガ・タグホイヤー・カルティエ時計が前回も今回も0件
  - 原因候補①: 価格帯バケット化（PRICE_BUCKET=10,000）で相場が取れていない
  - 原因候補②: `required_words`（時計・腕時計など）が厳しすぎてフィルタで落ちている
  - 原因候補③: `require_model_number=true` で型番照合できず全件スキップ
  - 調査方法: `python main.py --dry-run` を実行してログを確認する

## ファイル構成
- `main.py` - メインスキャン処理（スキャン→DB保存→ダッシュボード再生成→自動publish）
- `config.json` - キーワード・価格・カテゴリ設定
- `analyzer.py` - 相場取得・案件計算
- `model_extractor.py` - 型番抽出
- `database.py` - SQLite読み書き（スキーマ・マイグレーション含む）
- `generate_dashboard.py` - ダッシュボードHTML生成
- `docs/index.html` - 生成済みダッシュボード（GitHub Pages: `takenoriosaka/rieki-hakken` リポジトリ）
- `arbitrage.db` - SQLiteキャッシュ・履歴DB（gitignore済み）
- `ocr_reader.py` - 写真のタグから型番を無料で読む（Mac 内蔵の OCR = Apple Vision。説明文にも型番が無い出品が対象。結果は photo_ocr_cache）
- `photo_reader.py` - 写真AI確認（有料・Claude API。既定オフ）。OCR でも型番が無い出品のタグ読み取りと、案件の傷・汚れ判定。結果は photo_read_cache / photo_inspect_cache
- `set_api_key.command` - Claude の API キーを `.env` に登録する（ダブルクリック。キー自体は含まない）
- `.env` - `ANTHROPIC_API_KEY`（写真から型番を読む機能を使うときだけ。gitignore済み。キーを絶対にコミットしないこと）

## 写真から型番を読む機能（2026-10 追加）
- `.env` に `ANTHROPIC_API_KEY` があり、かつ `.rieki_friend_mode` が無いときだけ動く（それ以外は静かに無効。anthropic も import しない）。
- 読んだ文字列は必ず `model_extractor.extract()` に通し、型番形式に当てはまるものだけ採用する。
- 設定は config.json の settings（`photo_read_model` 既定 claude-sonnet-5-5、`photo_read_max_yen_per_scan` 既定 1000円 など）。
- ローカルの `.git/hooks/pre-commit`（git 管理外）で API キーの文字列（sk-ant… で始まるもの）を含むファイルと `.env` のコミットを止めている。
- 2026-11 追加: 同じ API 呼び出しで写真の傷・汚れも判定する（5段階: 新品同様/目立った傷なし/やや傷あり/傷あり/状態悪い。
  `photo_reader.inspect_deals` → 結果は photo_inspect_cache（URL＋写真URL一覧＋判定基準の版 `_INSPECT_VERSION` で照合））。
  タイトルの状態と悪いほうを取り（`condition_checker.combine`）、利益を計算し直す（`analyzer.apply_photo_condition`、`main._check_photo_conditions`）。
  ローカル画面のステップ4「写真AI確認（有料・Claude API）」のチェックで1回ごとにオン/オフ（`settings.photo_check_enabled`、2026-11-06 から既定オフ）。料金上限は品番・状態で共有。

## 写真のタグ読み取り（無料・Mac内OCR、2026-11-06 追加）
- 型番照合でタイトル・説明文に型番が無い出品は、まず `ocr_reader.read_listings` で写真を OCR（Apple Vision の
  VNRecognizeTextRequest、accurate、ja-JP+en-US、言語補正なし）し、読めた文字を説明文と同じく `model_extractor` で照合する。
  型番は1行ずつ照合し、見つからなければ OCR の読み間違い（数字に隣接する O→0・I/l→1、タトラスの先頭 I→L）を直した行でも探す。
  モデル名は写真にブランド名も写っているときだけ採用。
- 使えるのは macOS で `pyobjc-framework-Vision`（requirements.txt。darwin のみ）が入っているときだけ。料金なし・ログインなし。
- キャッシュ photo_ocr_cache: キー＝出品URL＋写真URL一覧＋`_OCR_VERSION`。OCR の全文を保存し、照合はキャッシュからも毎回やり直す
  （model_extractor の形式を増やしたら OCR し直さずに反映）。
- ローカル画面のステップ4「写真のタグ読み取り（無料・Mac内OCR）」（`settings.photo_ocr_enabled`、既定オン）。
- **有料の写真AI確認（photo_reader）は既定オフ**（`settings.photo_check_enabled` 既定 False。画面の「写真AI確認（有料・Claude API）」は
  毎回オフから始まり、前回の選択も復元しない）。オンにしたときだけ OCR でも型番が無い出品を Claude で読み、案件の傷・汚れも判定する。
  既定の設定では Anthropic API は一切呼ばれない。
- 傷・汚れのローカル判定は `local_inspector.py`（下の「写真で傷・汚れ確認（無料・Mac内AI）」）。`ocr_reader.inspect_deals_local` からも呼べる。

## 写真で傷・汚れ確認（無料・Mac内AI、2026-11-06 追加）
- `local_inspector.py`: 案件の写真を Mac 内の Ollama（http://127.0.0.1:11434、既定モデル `qwen3-vl:8b-instruct`）に1枚ずつ見せ、
  傷・汚れの種類ごとに yes/maybe/no で答えさせて（英語の指示・JSON スキーマ指定）、重さ×yes の数から5段階のランクにする（`_aggregate`）。
  結果は有料版と同じ形（＋`source: "local"`, `model`）で、`condition_checker.combine`・`analyzer.apply_photo_condition` をそのまま使う。
- 有料の写真AI確認もオンのときは有料の判定を優先し、判定できなかった案件だけ無料で見る（`main._check_photo_conditions`）。
- キャッシュ photo_local_inspect_cache（キー＝`_LOCAL_VERSION`＋モデル名＋写真URL一覧）。有料版のキャッシュとは別。
- ローカル画面のステップ4「写真で傷・汚れ確認（無料・Mac内AI）」（`settings.photo_local_check_enabled`、既定オフ）。
  Ollama が入っていない Mac（知り合いの Mac 等）では項目ごと出さない。入っていて止まっていれば `ollama serve` を起動してみる
  （ログは logs/ollama_serve.log）。インストールはしない。
- 本人の Mac: Ollama は Homebrew（`brew install ollama`、/opt/homebrew/bin/ollama）。ログイン項目・brew services には登録していない。
- タグの文字の読み取り（`local_inspector.read_listings`、`settings.photo_local_read_enabled`、既定オフ）: OCR でも型番が無い出品の
  タグ写真を AI に書き写させ、`ocr_reader.extract_model` で照合する。

## 知り合いとの共有（2026-10 追加）
- リポジトリ `takenoriosaka/rieki-hakken` は **public** で運用（招待・GitHub アカウントは不要）。個人情報や秘密情報をコミットしないこと。
- 知り合いは `setup_for_friend.command` で ~/rieki-hakken にセットアップ（`git clone https://github.com/takenoriosaka/rieki-hakken.git`、ログイン不要。uv で Python 3.12 の venv）。
  手順は `知り合い向け_使い方.md`。
- `start.command` は起動前に `git fetch` → 新版があれば `git pull --ff-only`（public なので認証不要）。失敗しても警告だけで起動。
  requirements.txt / Playwright のバージョンが変わったら自動で入れ直す（印: venv/.rieki_*）。
- `.rieki_friend_mode`（git 管理外）がある環境＝知り合いの Mac だけ、追跡ファイルのローカル変更を git stash してから更新する。
  本人の Mac にはこのファイルを置かないこと（置くと未コミット変更が stash される）。
- config.json は git 管理。型番を追加して commit & push すれば、知り合いの次回起動時に反映される。

## 仕入れ先 Yahoo!ショッピング（2026-11-06 追加）
- `scrapers/yahoo_shopping.py`（仕入れ先キー `yahoo_shopping`）。ログインなし・Cookie なしの GET で、検索結果 `/search/<検索語>/0/<ページ>/` と
  商品ページ `store.shopping.yahoo.co.jp/<店>/<商品>.html` の `__NEXT_DATA__` を読む。公式 API（Client ID が必要）は使っていない。
- robots.txt で禁止されている検索パラメータ（pf/pt=価格、n=件数、view、ship 等）と `/searchbff/*/search/postageInfo` は使わない。
  使うのは `used`（1=中古 / 2=新品）・`X=2`（安い順）とページ番号だけ。価格の範囲は安い順に読んで上限を超えたら打ち切る（最大10ページ）。
- 既定は中古のみ（`settings.yahoo_shopping_condition`、既定 "used"。画面の「Yahoo!ショッピングは新品も含める」で "all"）。
- 仕入れ値は **表示価格＋送料**（PayPay ポイント・クーポンは数えない）。送料無料でない出品は、タイトルの照合のあと
  `main.fill_shopping_postage` → `yahoo_shopping.fill_postage` で商品ページの `postage.fee`（東京都あて）を足す。取れなければ
  `settings.yahoo_shopping_default_postage`（既定 1,000円）を仮に足す。在庫なし・販売終了はここで除く。`Item.shipping` / `base_price`。
- 中古ランク（検索結果 labels.hasUsed20/40/60/80 ＝ 目立った傷や汚れなし/やや傷や汚れあり/傷や汚れあり/全体的に状態が悪い）が
  タイトルの状態より悪ければ、その言葉をタイトルの末尾に【】で足して condition_checker に効かせる。
- 写真は `https://item-shopping.c.yimg.jp/i/l/<画像ID>`（長辺600px）。説明文・写真は商品ページを1回読んだら控えて再取得しない。

## フリーリサーチモード（2026-11-06 追加）
- `free_research.py`。ローカル画面のヘッダー「フリーリサーチ」。登録済みのモデル・型番に縛られず、ブランド（自由入力）×
  ジャンル（`GENRES`: セットアップ・スーツ/ジャケット/コート/ダウン/トップス/ボトムス/ワンピース/バッグ/靴/小物＋自由入力）×
  仕入れ値の範囲（送料込み）× メンズ/レディース/指定なし で全仕入れ先を検索する。`/api/research` に `mode: "free"`。
- 相場: メルカリ売り切れ（ログインなし）を「ブランド + ジャンルの検索語（+ メンズ/レディース）」で取り、タイトルにブランド・
  ジャンルの語があり、別アイテム・キッズ・付属品・まとめ売り・「〜風」・逆の性別でないものだけ。上下10%を除いた中央値・四分位。
  信頼度: 高=20件以上かつ (Q3−Q1)/中央値 ≤0.6、中=8件以上かつ ≤1.0、低=それ以外（3件未満は相場なし）。売り切れは
  `free_market_cache` テーブル（DB）に price_cache_hours 保存し、絞り込みは毎回やり直す。
- 性別: `detect_gender`（タイトル→説明文→タトラス品番 MTA/LTA→スカート等のアイテム→紳士服サイズ A5 等）。不明は除かずに表示。
- 注目ポイント: `FEATURES`（利益商品共有 seed.json の特徴タグのうち文字で読めるもの）。売り切れの中でその特徴あり/なしの中央値を比べる。
- 型番: config.json に同じ ブランド×ジャンル のキーワードがある組（タトラス×ダウン 等）だけ、タイトル・説明文・写真のOCRで型番が
  取れたら型番の相場（analyzer.get_market_price、3件以上）に切り替える（信頼度 高）。
- ブランドの英字表記（`EXTRA_ALIASES`）は model_extractor.BRAND_ALIASES に無いものだけ実行中に足す。仕入れ先は `EN_QUERY` の英字名でも検索する。
- 入力候補: 登録済みのブランド＋ ~/program/rieki-shohin-share/data/seed.json の件数・平均利益（投稿者名は使わない）。
