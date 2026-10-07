#!/bin/bash
# =====================================================================
#  利益発見ツール  初回セットアップ（知り合いの Mac 用）
#
#  このファイル 1 つだけで、次のことを全部自動で行います。
#    1. git（Apple 純正の開発ツール）があるか確認
#    2. ツール本体をダウンロード（~/rieki-hakken。公開リポジトリなのでログイン不要）
#    3. Python とライブラリを準備（uv を使用。管理者パスワード不要）
#    4. ブラウザ部品（Chromium）を準備
#    5. デスクトップに「利益発見ツール」を作成
#    6. ツールを起動してブラウザで画面を開く
#
#  GitHub のアカウントやログインは不要です。
#  何度実行しても大丈夫です（済んでいる作業は飛ばします）。
#
#  開き方（どちらか）:
#    - ダブルクリック（開けないときは「右クリック → 開く」）
#    - ターミナルに次を貼り付けて Enter:  bash ~/Downloads/setup_for_friend.command
# =====================================================================

# --- 設定（テスト時は環境変数で差し替え可能） ---
REPO_URL="${RIEKI_REPO_URL:-https://github.com/takenoriosaka/rieki-hakken.git}"
APP_DIR="${RIEKI_DIR:-$HOME/rieki-hakken}"
UV_DIR="${RIEKI_UV_DIR:-$HOME/.local/bin}"
DESKTOP_DIR="${RIEKI_DESKTOP:-$HOME/Desktop}"
SHORTCUT_NAME="利益発見ツール"
PY_VERSION="3.12"

export PATH="$UV_DIR:$PATH"

TOTAL=6

line() { echo "------------------------------------------------------------"; }
step() { echo; line; echo " $1/$TOTAL  $2"; line; }
ok()   { echo "  → OK: $1"; }

# 失敗したら原因と次にやることを表示して止める
fail() {
    echo
    echo "############################################################"
    echo "  セットアップを中断しました"
    echo "############################################################"
    echo "  原因: $1"
    echo
    echo "  次にやること:"
    shift
    for msg in "$@"; do echo "    ・$msg"; done
    echo
    echo "  分からないときは、この画面のスクリーンショットを送ってください。"
    echo
    pause_exit 1
}

pause_exit() {
    if [ -t 0 ] && [ "${RIEKI_NO_PAUSE:-}" != "1" ]; then
        read -r -p "Enter キーを押すとこの画面を閉じられます…" _
    fi
    exit "$1"
}

main() {
    echo
    echo "============================================================"
    echo "   利益発見ツール  初回セットアップを始めます"
    echo "   （10〜20分ほどかかります。途中で操作することは基本的にありません）"
    echo "============================================================"

    step_git
    step_clone
    step_python
    step_playwright
    step_shortcut
    step_launch
}

# ---------------------------------------------------------------------
# 1/6 git
# ---------------------------------------------------------------------
step_git() {
    step 1 "git（Apple 純正の開発ツール）を確認しています…"
    if xcode-select -p >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
        ok "$(git --version)"
        return
    fi
    echo "  git がまだ入っていません。Apple のインストール画面を表示します。"
    xcode-select --install >/dev/null 2>&1
    fail "git（コマンドライン・デベロッパ・ツール）が入っていません。" \
         "画面に出た「コマンドライン・デベロッパ・ツールをインストールしますか？」で「インストール」を押してください。" \
         "利用許諾が出たら「同意する」を押します（10〜30分ほどかかります）。" \
         "「ソフトウェアがインストールされました」と出たら「完了」を押してください。" \
         "そのあと、このセットアップファイルをもう一度開いてください。" \
         "インストール画面が出ないときは、Mac を再起動してからもう一度開いてください。"
}

# ---------------------------------------------------------------------
# 2/6 ツール本体
# ---------------------------------------------------------------------
step_clone() {
    step 2 "ツール本体をダウンロードしています…（保存先: ${APP_DIR}）"
    if [ -d "$APP_DIR/.git" ]; then
        echo "  すでにダウンロード済みです。最新版に更新します…"
        if ! (cd "$APP_DIR" && GIT_TERMINAL_PROMPT=0 git pull --ff-only --quiet); then
            echo "  [注意] 最新版への更新はできませんでした。今の版のまま続けます（起動時にもう一度試します）。"
        fi
    elif [ -e "$APP_DIR" ]; then
        fail "$APP_DIR という名前のフォルダ（またはファイル）がすでにあり、ツールのフォルダではありません。" \
             "Finder でホームフォルダを開き、「rieki-hakken」の名前を「rieki-hakken-old」などに変えてください。" \
             "そのあと、このセットアップファイルをもう一度開いてください。"
    else
        # 公開リポジトリなので認証不要。GIT_TERMINAL_PROMPT=0 で、万一ログインを求められても入力待ちで止まらないようにする
        if ! GIT_TERMINAL_PROMPT=0 git clone --quiet "$REPO_URL" "$APP_DIR"; then
            rm -rf "$APP_DIR"
            fail "ツール本体をダウンロードできませんでした。" \
                 "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。" \
                 "それでもだめなときは、この画面のスクリーンショットを紹介者に送ってください。"
        fi
    fi
    [ -f "$APP_DIR/start.command" ] && [ -f "$APP_DIR/requirements.txt" ] \
        || fail "ダウンロードしたツールのファイルが足りません。" "このセットアップファイルをもう一度開いてください。"
    chmod +x "$APP_DIR/start.command" 2>/dev/null
    # 知り合いモードの印（git 管理外）。起動時の自動更新で、誤って編集したファイルを退避してから更新する
    touch "$APP_DIR/.rieki_friend_mode"
    ok "ツール本体を準備しました"
}

# ---------------------------------------------------------------------
# 3/6 Python（uv）
# ---------------------------------------------------------------------
venv_ok() {
    [ -x "$APP_DIR/venv/bin/python" ] && \
        "$APP_DIR/venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

step_python() {
    step 3 "Python とライブラリを準備しています…（数分かかります）"
    if ! command -v uv >/dev/null 2>&1; then
        echo "  uv（Python の準備ツール）をダウンロードしています…"
        mkdir -p "$UV_DIR"
        if ! curl -LsSf --max-time 300 https://astral.sh/uv/install.sh \
                | env UV_INSTALL_DIR="$UV_DIR" UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1; then
            fail "uv をダウンロードできませんでした。" \
                 "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。"
        fi
        command -v uv >/dev/null 2>&1 || fail "uv を準備できませんでした。" "このセットアップファイルをもう一度開いてください。"
    fi
    ok "uv: $(uv --version)"

    cd "$APP_DIR" || fail "$APP_DIR を開けませんでした。" "このセットアップファイルをもう一度開いてください。"
    if venv_ok; then
        ok "Python は準備済みです（$(./venv/bin/python --version)）"
    else
        rm -rf ./venv
        echo "  Python ${PY_VERSION} を準備しています…"
        # --seed: venv の中に pip も入れておく（start.command が uv の無い環境でも pip で更新できるように）
        if ! uv venv --quiet --seed --python "$PY_VERSION" venv; then
            rm -rf ./venv
            fail "Python を準備できませんでした。" \
                 "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。"
        fi
        ok "Python を準備しました（$(./venv/bin/python --version)）"
    fi

    echo "  ライブラリをインストールしています…"
    if ! uv pip install --quiet --python ./venv/bin/python -r requirements.txt; then
        fail "ライブラリをインストールできませんでした。" \
             "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。"
    fi
    # start.command が「requirements.txt が変わったか」を判定するための印
    shasum -a 256 requirements.txt | awk '{print $1}' > ./venv/.rieki_requirements.sha256
    ok "ライブラリをインストールしました"
}

# ---------------------------------------------------------------------
# 4/6 Chromium
# ---------------------------------------------------------------------
step_playwright() {
    step 4 "ブラウザ部品（Chromium）を準備しています…（数分かかります）"
    cd "$APP_DIR" || exit 1
    if [ "${RIEKI_SKIP_PLAYWRIGHT:-}" = "1" ]; then
        echo "  （テスト: Chromium のインストールは省略）"
        return
    fi
    if ! ./venv/bin/playwright install chromium; then
        fail "ブラウザ部品（Chromium）をインストールできませんでした。" \
             "インターネット接続と、Mac の空き容量（1GB 以上）を確認してください。" \
             "そのあと、このセットアップファイルをもう一度開いてください。"
    fi
    ./venv/bin/python -c 'import importlib.metadata as m; print(m.version("playwright"))' \
        > ./venv/.rieki_playwright_version 2>/dev/null
    ok "Chromium を準備しました"
}

# ---------------------------------------------------------------------
# 5/6 デスクトップの起動用ファイル
# ---------------------------------------------------------------------
step_shortcut() {
    step 5 "デスクトップに「${SHORTCUT_NAME}」を作っています…"
    mkdir -p "$DESKTOP_DIR"
    local f="$DESKTOP_DIR/$SHORTCUT_NAME.command"
    # 自分の Mac で作ったファイルなので、ダブルクリックでそのまま開ける（Gatekeeper の警告は出ない）
    cat > "$f" <<EOF
#!/bin/bash
# 利益発見ツールを起動します（ダブルクリックで開いてください）
exec "$APP_DIR/start.command"
EOF
    chmod +x "$f" || fail "デスクトップにファイルを作れませんでした。" "このセットアップファイルをもう一度開いてください。"
    ok "デスクトップに「$SHORTCUT_NAME.command」を作りました。次回からはこれをダブルクリックします"
}

# ---------------------------------------------------------------------
# 6/6 起動
# ---------------------------------------------------------------------
step_launch() {
    step 6 "ツールを起動しています…"
    echo
    echo "  ★ セットアップが完了しました！"
    echo "    まもなくブラウザでリサーチ画面（http://127.0.0.1:8765）が開きます。"
    echo "    使っている間は、この黒い画面を閉じないでください。"
    echo "    終わるときは、この画面を閉じれば OK です。"
    echo "    次回からは、デスクトップの「${SHORTCUT_NAME}」をダブルクリックしてください。"
    echo
    if [ "${RIEKI_NO_LAUNCH:-}" = "1" ]; then
        echo "  （テスト: 起動は省略）"
        exit 0
    fi
    exec "$APP_DIR/start.command"
}

main "$@"; exit $?
