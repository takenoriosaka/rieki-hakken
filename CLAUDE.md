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
