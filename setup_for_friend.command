#!/bin/bash
# =====================================================================
#  利益発見ツール  初回セットアップ（知り合いの Mac 用）
#
#  このファイル 1 つだけで、次のことを全部自動で行います。
#    1. git（Apple 純正の開発ツール）があるか確認
#    2. GitHub にログイン（GitHub CLI を使用。管理者パスワード不要）
#    3. ツール本体をダウンロード（~/rieki-hakken）
#    4. Python とライブラリを準備（uv を使用。管理者パスワード不要）
#    5. ブラウザ部品（Chromium）を準備
#    6. デスクトップに「利益発見ツール」を作成
#    7. ツールを起動してブラウザで画面を開く
#
#  何度実行しても大丈夫です（済んでいる作業は飛ばします）。
#
#  開き方（どちらか）:
#    - ダブルクリック（開けないときは「右クリック → 開く」）
#    - ターミナルに次を貼り付けて Enter:  bash ~/Downloads/setup_for_friend.command
# =====================================================================

# --- 設定（テスト時は環境変数で差し替え可能） ---
REPO="${RIEKI_REPO:-takenoriosaka/rieki-hakken}"
APP_DIR="${RIEKI_DIR:-$HOME/rieki-hakken}"
TOOLS_DIR="${RIEKI_TOOLS_DIR:-$HOME/rieki-hakken-tools}"
UV_DIR="${RIEKI_UV_DIR:-$HOME/.local/bin}"
DESKTOP_DIR="${RIEKI_DESKTOP:-$HOME/Desktop}"
SHORTCUT_NAME="利益発見ツール"
PY_VERSION="3.12"

export PATH="$UV_DIR:$TOOLS_DIR/bin:$PATH"
export GH_NO_UPDATE_NOTIFIER=1

TOTAL=7

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
    echo "   （10〜20分ほどかかります。途中で GitHub へのログインがあります）"
    echo "============================================================"

    step_git
    step_gh
    step_clone
    step_python
    step_playwright
    step_shortcut
    step_launch
}

# ---------------------------------------------------------------------
# 1/7 git
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
# 2/7 GitHub CLI（gh）とログイン
# ---------------------------------------------------------------------
install_gh() {
    local arch tag ver url tmp
    case "$(uname -m)" in
        arm64)  arch=arm64 ;;
        x86_64) arch=amd64 ;;
        *) fail "この Mac の種類（$(uname -m)）に対応していません。" "この画面のスクリーンショットを送ってください。" ;;
    esac

    # 最新版の番号を調べる（github.com の「latest」の転送先から取得。API の回数制限を受けない）
    tag="$(curl -fsSI --max-time 30 https://github.com/cli/cli/releases/latest 2>/dev/null \
            | tr -d '\r' | awk 'tolower($1)=="location:"{print $2}' | sed 's#.*/tag/##' | tail -n 1)"
    [ -n "$tag" ] || fail "GitHub CLI の最新版を確認できませんでした（インターネットにつながっていない可能性があります）。" \
                         "Wi-Fi などインターネット接続を確認してください。" \
                         "そのあと、このセットアップファイルをもう一度開いてください。"
    ver="${tag#v}"
    url="https://github.com/cli/cli/releases/download/${tag}/gh_${ver}_macOS_${arch}.zip"

    echo "  GitHub CLI ${ver}（${arch}）をダウンロードしています…"
    tmp="$(mktemp -d)"
    if ! curl -fL --max-time 300 --retry 2 -o "$tmp/gh.zip" "$url"; then
        rm -rf "$tmp"
        fail "GitHub CLI をダウンロードできませんでした。" \
             "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。"
    fi
    if ! unzip -q -o "$tmp/gh.zip" -d "$tmp"; then
        rm -rf "$tmp"
        fail "GitHub CLI の展開に失敗しました。" "このセットアップファイルをもう一度開いてください。"
    fi
    mkdir -p "$TOOLS_DIR/bin"
    rm -rf "$TOOLS_DIR/gh"
    mv "$tmp/gh_${ver}_macOS_${arch}" "$TOOLS_DIR/gh" \
        || { rm -rf "$tmp"; fail "GitHub CLI の配置に失敗しました。" "このセットアップファイルをもう一度開いてください。"; }
    rm -rf "$tmp"
    ln -sf "$TOOLS_DIR/gh/bin/gh" "$TOOLS_DIR/bin/gh"
    xattr -dr com.apple.quarantine "$TOOLS_DIR/gh" 2>/dev/null || true
    "$TOOLS_DIR/bin/gh" --version >/dev/null 2>&1 \
        || fail "GitHub CLI を起動できませんでした。" "このセットアップファイルをもう一度開いてください。"
}

step_gh() {
    step 2 "GitHub へのログインを準備しています…"
    if command -v gh >/dev/null 2>&1; then
        ok "GitHub CLI は準備済みです（$(gh --version | head -n 1)）"
    else
        install_gh
        ok "GitHub CLI を準備しました"
    fi
    [ "${RIEKI_SKIP_AUTH:-}" = "1" ] && { echo "  （テスト: ログインは省略）"; return; }

    if gh auth status --hostname github.com >/dev/null 2>&1; then
        ok "GitHub にはログイン済みです"
    else
        echo
        echo "  これから GitHub にログインします。次の順番で進めてください。"
        echo
        echo "   ① 下に「! First copy your one-time code: XXXX-XXXX」と出ます。"
        echo "      この XXXX-XXXX（英数字 8 けた）がワンタイムコードです。メモするか覚えておいてください。"
        echo "   ②「Press Enter to open github.com in your browser...」と出たら、Enter キーを押します。"
        echo "      → ブラウザで GitHub の画面が開きます（開かないときは https://github.com/login/device を開く）。"
        echo "   ③ GitHub にログインしていなければ、登録したメールアドレスとパスワードでログインします。"
        echo "   ④「Device Activation」の画面で「Continue」を押し、"
        echo "      ①のコードを入力して「Continue」→「Authorize github」を押します。"
        echo "   ⑤「Congratulations, you're all set!」と出たら、この黒い画面に戻ってください。"
        echo "      （「Authenticate Git with your GitHub credentials?」と聞かれたら、そのまま Enter）"
        echo
        if ! gh auth login --hostname github.com --web --git-protocol https; then
            fail "GitHub へのログインが完了しませんでした。" \
                 "このセットアップファイルをもう一度開いて、ログインをやり直してください。" \
                 "コードの入力は 15 分以内に行ってください。"
        fi
    fi
    gh auth setup-git --hostname github.com >/dev/null 2>&1 \
        || fail "git に GitHub のログイン情報を設定できませんでした。" "このセットアップファイルをもう一度開いてください。"
    ok "git に GitHub のログイン情報を設定しました"

    if ! gh repo view "$REPO" >/dev/null 2>&1; then
        local who
        who="$(gh api user --jq .login 2>/dev/null)"
        fail "ツールの保管場所（${REPO}）を開く権限がありません（ログイン中のアカウント: ${who:-不明}）。" \
             "招待メール（GitHub から届く「invited you to collaborate」）の「View invitation」→「Accept invitation」を押してください。" \
             "招待がまだ届いていなければ、ツールを紹介してくれた人に GitHub のユーザー名（${who:-あなたのユーザー名}）を伝えてください。" \
             "承認できたら、このセットアップファイルをもう一度開いてください。"
    fi
    ok "ツールの保管場所にアクセスできました"
}

# ---------------------------------------------------------------------
# 3/7 ツール本体
# ---------------------------------------------------------------------
step_clone() {
    step 3 "ツール本体をダウンロードしています…（保存先: ${APP_DIR}）"
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
        if ! GIT_TERMINAL_PROMPT=0 gh repo clone "$REPO" "$APP_DIR" -- --quiet; then
            rm -rf "$APP_DIR"
            fail "ツール本体をダウンロードできませんでした。" \
                 "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。"
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
# 4/7 Python（uv）
# ---------------------------------------------------------------------
venv_ok() {
    [ -x "$APP_DIR/venv/bin/python" ] && \
        "$APP_DIR/venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

step_python() {
    step 4 "Python とライブラリを準備しています…（数分かかります）"
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
# 5/7 Chromium
# ---------------------------------------------------------------------
step_playwright() {
    step 5 "ブラウザ部品（Chromium）を準備しています…（数分かかります）"
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
# 6/7 デスクトップの起動用ファイル
# ---------------------------------------------------------------------
step_shortcut() {
    step 6 "デスクトップに「${SHORTCUT_NAME}」を作っています…"
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
# 7/7 起動
# ---------------------------------------------------------------------
step_launch() {
    step 7 "ツールを起動しています…"
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
