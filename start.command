#!/bin/bash
# ダブルクリックでローカルのリサーチ画面を起動する（ブラウザが自動で開きます）
# 終了するときは、このターミナル画面で Ctrl+C を押すか、ウィンドウを閉じてください。
#
# 起動前に自動で最新版を取得します（git pull --ff-only）。
#   - ネット不通・ローカル変更などで取得できないときは、警告だけ出して今の版で起動します。
#   - requirements.txt が変わっていたらライブラリを自動で入れ直し、
#     Playwright のバージョンが変わっていたら Chromium も入れ直します。
#   - 「知り合いモード」（セットアップ時に作る .rieki_friend_mode がある場合）だけ、
#     config.json などの追跡ファイルを誤って編集していても、その変更を git stash に退避してから更新します。
#     ユーザー本人の Mac（マーカーなし）では退避せず、未コミット変更には一切触れません。
#   - 8765 番がすでに使われている（起動中）なら、二重起動せずブラウザで開くだけにします。
#
# 注意: 更新でこのファイル自体が書き換わっても途中から壊れないよう、
#       処理はすべて main 関数の中に書き、最後の 1 行で呼び出してすぐ exit しています。

main() {
    cd "$(dirname "$0")" || exit 1
    export PATH="$HOME/.local/bin:$HOME/rieki-hakken-tools/bin:$PATH"

    local PORT="${RIEKI_PORT:-8765}"
    local URL="http://127.0.0.1:$PORT"

    # ---- 1) すでに起動中なら、ブラウザで開くだけ ----
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "リサーチ画面はすでに起動しています。ブラウザで開きます: $URL"
        [ "${RIEKI_NO_BROWSER:-}" = "1" ] || open "$URL"
        return 0
    fi

    if [ ! -x ./venv/bin/python ]; then
        echo "[エラー] ./venv/bin/python が見つかりません。"
        echo "         セットアップ（setup_for_friend.command）をもう一度実行してください。"
        return 1
    fi

    # ---- 2) 最新版を取得 ----
    if [ "${RIEKI_SKIP_UPDATE:-}" != "1" ]; then
        update_repo
    fi

    # ---- 3) 依存ライブラリ・ブラウザの更新 ----
    sync_requirements
    sync_playwright

    # ---- 4) 起動 ----
    ./venv/bin/python app.py
}

# 最新版の取得。失敗しても絶対に止めない（警告だけ）。
update_repo() {
    # Xcode Command Line Tools が無い Mac で git を呼ぶとインストール画面が出てしまうので確認する
    if ! xcode-select -p >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
        echo "[注意] git が使えないため、更新の確認をスキップしました（今の版で起動します）"
        return 0
    fi
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        return 0
    fi

    echo "最新版を確認しています…"
    local friend=0 out
    [ -f .rieki_friend_mode ] && friend=1
    local GITNET=(-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=20)

    # まず取得だけ行う（公開リポジトリなので認証不要。ネット不通ならここで分かる。この段階では作業ツリーに触れない）
    if ! out="$(GIT_TERMINAL_PROMPT=0 git "${GITNET[@]}" fetch --quiet 2>&1)"; then
        echo "[注意] 最新版を確認できませんでした。今の版のまま起動します。"
        echo "       （インターネット接続を確認してください）"
        echo "$out" | sed -n '1,3p' | sed 's/^/       > /'
        return 0
    fi
    if [ -z "$(git rev-list -n 1 'HEAD..@{u}' 2>/dev/null)" ]; then
        echo "最新版です。"
        return 0
    fi

    # 知り合いモードだけ: 追跡ファイルのローカル変更を退避してから更新する
    if [ "$friend" = "1" ] && [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
        local stamp
        stamp="$(date '+%Y-%m-%d %H:%M:%S')"
        if git -c user.name=rieki-hakken -c user.email=rieki-hakken@localhost \
               stash push -q -m "自動退避 ${stamp}（起動時の更新のため）" >/dev/null 2>&1; then
            echo "[お知らせ] ツールのファイル（config.json など）が編集されていたため、"
            echo "           その変更を退避してから最新版に更新します。"
            echo "           （退避した内容は『git stash list』で確認できます。通常は気にしなくて大丈夫です）"
        else
            echo "[注意] 編集されたファイルの退避に失敗しました。更新できない場合があります。"
        fi
    fi

    if out="$(GIT_TERMINAL_PROMPT=0 git "${GITNET[@]}" pull --ff-only --quiet 2>&1)"; then
        echo "最新版に更新しました。"
    else
        echo "[注意] 最新版に更新できませんでした。今の版のまま起動します。"
        echo "       （このパソコンでの変更や、まだ送信していない変更と重なっている可能性があります）"
        echo "$out" | sed -n '1,4p' | sed 's/^/       > /'
    fi
    return 0
}

# requirements.txt の内容が前回インストール時と違えば入れ直す
sync_requirements() {
    [ -f requirements.txt ] || return 0
    local stamp_file=./venv/.rieki_requirements.sha256 cur old=""
    cur="$(shasum -a 256 requirements.txt | awk '{print $1}')"
    [ -f "$stamp_file" ] && old="$(cat "$stamp_file" 2>/dev/null)"
    [ "$cur" = "$old" ] && return 0

    echo "必要なライブラリを確認・更新しています…"
    local ok=1
    if command -v uv >/dev/null 2>&1; then
        uv pip install --quiet --python ./venv/bin/python -r requirements.txt || ok=0
    elif [ -x ./venv/bin/pip ]; then
        ./venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt || ok=0
    else
        ./venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt || ok=0
    fi
    if [ "$ok" = "1" ]; then
        echo "$cur" > "$stamp_file"
    else
        echo "[注意] ライブラリの更新に失敗しました。今の状態のまま起動します（次回の起動時にもう一度試します）。"
    fi
    return 0
}

# Playwright のバージョンが前回と違えば Chromium を入れ直す
sync_playwright() {
    local stamp_file=./venv/.rieki_playwright_version cur old=""
    cur="$(./venv/bin/python -c 'import importlib.metadata as m; print(m.version("playwright"))' 2>/dev/null)"
    [ -n "$cur" ] || return 0
    [ -f "$stamp_file" ] && old="$(cat "$stamp_file" 2>/dev/null)"
    [ "$cur" = "$old" ] && return 0

    echo "ブラウザ部品（Chromium）を確認・更新しています…（初回は数分かかることがあります）"
    if ./venv/bin/python -m playwright install chromium; then
        echo "$cur" > "$stamp_file"
    else
        echo "[注意] Chromium の更新に失敗しました。今の状態のまま起動します（次回の起動時にもう一度試します）。"
    fi
    return 0
}

main "$@"; exit $?
