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
- `.env` - 現在このプロジェクトで必要な環境変数はありません（gitignore済み）
