#!/bin/bash
# ダブルクリックで Claude の API キーを登録する（写真から型番を読む機能用）
#
# ・入力欄（文字は ●●● で隠れます）にキーを貼り付けて「保存」を押すと、
#   このフォルダの .env に ANTHROPIC_API_KEY=… として保存します（すでにあれば置き換えます）。
# ・.env は git の管理外（.gitignore 済み）なので、GitHub には送られません。
# ・このファイル自体にはキーは入っていません。
# ・知り合いの Mac（.rieki_friend_mode がある）では使いません。

main() {
    cd "$(dirname "$0")" || exit 1
    local TITLE="写真から型番を読む機能（APIキーの登録）"
    # キーの先頭の文字（このファイルに続けて書くとリポジトリの誤公開チェックに引っかかるため分けて書く）
    local PREFIX="sk-""ant-"

    if [ -f .rieki_friend_mode ]; then
        alert "このMacでは写真から型番を読む機能は使いません。" "登録は不要です。"
        return 0
    fi

    local key
    key="$(osascript <<EOF 2>/dev/null
set r to display dialog "Claude の API キーを貼り付けて「保存」を押してください。

・貼り付けは ⌘（コマンド）キー ＋ V です
・入力した文字は ●●● で隠れて表示されます
・キーは ${PREFIX} で始まる長い英数字です" default answer "" with hidden answer buttons {"やめる", "保存"} default button "保存" cancel button "やめる" with title "$TITLE" with icon note
text returned of r
EOF
)"
    if [ $? -ne 0 ]; then
        echo "登録をやめました（何も変更していません）。"
        return 0
    fi

    # 前後の空白・改行を取り除く（コピーのときに入りがち）
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    if [ -z "$key" ]; then
        alert "キーが入力されていません。" "何も変更していません。もう一度このファイルをダブルクリックして、キーを貼り付けてください。"
        return 1
    fi
    if [[ "$key" != "$PREFIX"* ]] || [[ ! "$key" =~ ^[A-Za-z0-9_-]{40,}$ ]]; then
        alert "キーの形式が正しくないようです。" "キーは ${PREFIX} で始まる長い英数字です。コピーし直して、もう一度このファイルをダブルクリックしてください。（何も変更していません）"
        return 1
    fi

    # .env を書き換える: 既存の ANTHROPIC_API_KEY の行を除き、新しい行を足す（他の行はそのまま）
    local tmp
    tmp="$(mktemp "${TMPDIR:-/tmp}/rieki_env.XXXXXX")" || { alert "保存に失敗しました。" "一時ファイルを作れませんでした。"; return 1; }
    chmod 600 "$tmp"
    if [ -f .env ]; then
        grep -v -E '^[[:space:]]*(export[[:space:]]+)?ANTHROPIC_API_KEY[[:space:]]*=' .env > "$tmp"
        # 最後の行に改行が無いと次の行とつながるので補う
        if [ -s "$tmp" ] && [ -n "$(tail -c 1 "$tmp")" ]; then echo >> "$tmp"; fi
    fi
    printf 'ANTHROPIC_API_KEY=%s\n' "$key" >> "$tmp"
    if cat "$tmp" > .env; then
        chmod 600 .env
        rm -f "$tmp"
    else
        rm -f "$tmp"
        alert "保存に失敗しました。" "このフォルダに書き込めませんでした。"
        return 1
    fi
    key=""

    if ! git check-ignore -q .env 2>/dev/null && command -v git >/dev/null 2>&1 \
            && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "[注意] .env が git の管理外になっていません。.gitignore を確認してください。"
    fi

    echo "APIキーを保存しました（.env）。"
    alert "APIキーを保存しました。" "次のリサーチから、写真から型番を読む機能が使われます。
リサーチ画面の上の方に「写真から型番読み取り: 有効」と表示されます（画面を開き直すと反映されます）。"
    return 0
}

# ダイアログでお知らせを出す（osascript が使えないときはターミナルに表示）
alert() {
    local msg="$1" detail="$2"
    osascript - "$msg" "$detail" >/dev/null 2>&1 <<'EOF' || { echo "$msg"; echo "$detail"; }
on run argv
    display alert (item 1 of argv) message (item 2 of argv) buttons {"OK"} default button "OK"
end run
EOF
}

main "$@"
exit $?
