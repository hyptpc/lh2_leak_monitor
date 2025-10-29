#!/bin/bash

# 監視対象のファイル名 (Pythonスクリプトと合わせてください)
FILE="H2tgtPresentStatus.txt"

# --- ここからスクリプト ---

# 1. ファイルが存在するか確認
if [ ! -f "$FILE" ]; then
    echo "エラー: ファイル $FILE が見つかりません。"
    exit 1
fi

# 2. 現在の 'Alert_H2leak:' の値を取得
#    grepで対象行を探し、awkで ':' の後ろの値を取得し、
#    (+0) で数値に変換 (これにより前後の空白が除去されます)
current_status=$(grep "^Alert_H2leak:" "$FILE" | awk -F: '{print $2+0}')

# 3. 新しい値を決定
if [ "$current_status" = "0" ]; then
    new_status="1"
    echo "現在の状態 (Alert_H2leak): 0"
    echo "変更後 -> 1"
else
    new_status="0"
    echo "現在の状態 (Alert_H2leak): 1 (または0以外)"
    echo "変更後 -> 0"
fi

# 4. sed を使ってファイル内の値を "in-place" (直接) 置換
#    -i = ファイルを直接編集
#    s/パターン/置換/
#    パターン:
#      ^\(Alert_H2leak:[[:space:]]*\)  = 行頭が "Alert_H2leak:" で、
#                                       続く空白( : )までをグループ1(\1)として記憶
#      $current_status                 = 古い値 (0 または 1)
#    置換:
#      \1$new_status                   = 記憶したグループ1 + 新しい値
sed -i "s/^\(Alert_H2leak:[[:space:]]*\)$current_status/\1$new_status/" "$FILE"

echo "---"
echo "更新後の行:"
grep "^Alert_H2leak:" "$FILE"
