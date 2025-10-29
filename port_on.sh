#!/bin/bash

# --- 設定 (環境に合わせて確認) ---

# SSHでログインするユーザー名
TARGET_USER="sks"

# 対象のラズパイのIPアドレス (スペース区切りで複数指定)
TARGET_HOSTS=("192.168.20.12" "192.168.20.13")

# リモート先で使用するuhubctlのフルパス
UHUBCTL_PATH="/usr/sbin/uhubctl"

# 制御するハブの場所
HUB_LOCATION="1-1"

# 制御するポート番号 (スペース区切り)
PORTS_TO_ENABLE=(1 2 3 4)

# SSH接続時に "yes/no" を聞かないためのオプション
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# --- スクリプト本体 ---

echo "USBポートの電源をONにします..."

# 各ホストに対してループ
for host in "${TARGET_HOSTS[@]}"; do
    echo "--- [ 対象ホスト: $host ] ---"
    
    # 各ポートに対してループ
    for port in "${PORTS_TO_ENABLE[@]}"; do
    
        # リモートで実行するコマンドを定義 (アクションを -a 1 に)
        REMOTE_CMD="sudo $UHUBCTL_PATH -l $HUB_LOCATION -p $port -a 1"
        
        echo "  ポート $port をONにします... (ssh $TARGET_USER@$host ...)"
        
        # SSH経由でコマンドを実行
        ssh $SSH_OPTS "$TARGET_USER@$host" "$REMOTE_CMD"
        
        # 終了コードをチェック (簡易エラー表示)
        if [ $? -ne 0 ]; then
            echo "  [エラー] $host のポート $port でコマンドが失敗しました。"
        fi
        
    done
done

echo "--- 完了 ---"
