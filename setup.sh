#!/bin/bash
set -e

echo "======================================"
echo "メルカリ アービトラージツール セットアップ"
echo "======================================"

# Python バージョン確認
python3 --version || { echo "Python3 が必要です"; exit 1; }

# 仮想環境の作成
if [ ! -d "venv" ]; then
    echo "→ 仮想環境を作成中..."
    python3 -m venv venv
fi

echo "→ 仮想環境を有効化中..."
source venv/bin/activate

# パッケージインストール
echo "→ パッケージをインストール中..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Playwright ブラウザのインストール
echo "→ Playwright Chromium をインストール中..."
playwright install chromium

# .env ファイルの作成
if [ ! -f ".env" ]; then
    echo "→ .env ファイルを作成中..."
    cp .env.example .env
    echo ""
    echo "【重要】.env ファイルを編集して LINE_NOTIFY_TOKEN を設定してください"
    echo "  LINE Notify トークン取得: https://notify-bot.line.me/my/"
    echo ""
fi

# logs ディレクトリ作成
mkdir -p logs

echo ""
echo "======================================"
echo "セットアップ完了！"
echo "======================================"
echo ""
echo "次のステップ:"
echo "  1. .env ファイルに LINE_NOTIFY_TOKEN を設定"
echo "  2. config.json でキーワードを設定"
echo "  3. テスト実行:"
echo "     source venv/bin/activate"
echo "     python main.py --dry-run"
echo ""
echo "  4. 定時実行（毎日自動）:"
echo "     python scheduler.py"
echo ""
