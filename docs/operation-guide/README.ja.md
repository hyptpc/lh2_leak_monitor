# LH2モニター：動作を上から追うガイド

[English](README.md) · [プロジェクトREADME](../../README.md)

フローチャートを上から下へ読んでください。
現行の [config.toml](../../config/config.toml)、[起動処理](../../app/monitor.py)、
[制御ロジック](../../app/lh2_monitor/engine.py) に沿った説明です。
設定を変えると、以下の順序・接続先・待ち時間も変わります。

**モニターの通常起動は実機動作です。別プログラムの事前チェックは、
監視の起動・停止命令・Discord通知を行いません。**
事前チェックは手動で行う独立した手順であり、monitor.py が自動実行するものではありません。

実行場所はmonitor2の /home/sks/monitor_tools/lh2_leak_monitor に固定しています。
実機起動にはtools/daemon/lh2/start.sh を使います。Pythonは /home/sks/.venv/bin/python、
作業ディレクトリも上記パスに固定され、activateは不要です。
systemdは準備済みのローカル用Type=exec定義を使い、旧oneshot/tmux定義では起動しないでください。

## 操作する入口

確認・計画表示・シミュレーションはプロジェクト直下、サービス操作は
`tools/daemon/lh2/` のシェルスクリプトから行います。venvのactivateは不要です。

| コマンド | 動作 |
| --- | --- |
| `./preflight.sh` | 全機器との読み取り専用確認。Discord送信はスキップ |
| `./plan.sh` | 設定された停止順序の表示のみ |
| `./simulate.sh` | 機器に接続しない仮想動作確認 |
| `./tools/daemon/lh2/install-service.sh` | サービス定義の登録・再読み込みのみ。起動や自動起動の有効化はしない |
| `./tools/daemon/lh2/start.sh` | systemdで実機監視を開始 |
| `./tools/daemon/lh2/stop.sh` | 監視を停止。既に停止した機器をONには戻さない |
| `./tools/daemon/lh2/status.sh` | サービス状態・直近のログを確認 |

今回の配置変更後は、最初に `./tools/daemon/lh2/install-service.sh` を実行してください。
Python本体は `app/`、設定は `config/config.toml`、`.env` はプロジェクト直下です。

## 1. 起動前：まず事前チェック

monitor2 の共有フォルダ側で実行します。

~~~bash
cd /home/sks/monitor_tools/lh2_leak_monitor
mkdir -p logs

# 標準：ローカル確認に加え、設定中の全機器との読み取り専用の通信確認も行います。
./preflight.sh

# 必要な場合だけ：import・設定・認証情報・入力の確認のみ。機器には接続しません。
./preflight.sh --local

# 必要ならJSONで保存。logs/ はGit管理対象外です。
./preflight.sh --json > logs/preflight.json
~~~

~~~mermaid
flowchart TD
    P["モニターと同じPythonで preflight.py を実行"] --> C["設定・必要な認証情報を確認<br/>モジュールとCAENネイティブライブラリをimport"]
    C --> I["Alert_H2leak を読む<br/>入力ファイルの更新からの経過時間も表示"]
    I --> D{"機器も確認する？<br/>標準は有効・--local なら省略"}
    D -->|いいえ| L["ローカル確認のみ<br/>機器通信は SKIP"]
    D -->|はい| Q["設定中の全機器へ読み取り専用チェック<br/>下の表を参照"]
    L --> R["PASS / WARN / FAIL / SKIP を確認"]
    Q --> R
    R --> F{"FAIL がある？"}
    F -->|はい| X["原因を解消してから実機監視の起動を判断"]
    F -->|いいえ| W["WARN・SKIPも確認<br/>PASSでも将来の停止成功は保証されない"]
    X --> N["チェック終了<br/>モニターの起動状態は変えない"]
    W --> N
    classDef check fill:#e8f1ff,stroke:#3568a8,color:#182c44;
    classDef stop fill:#ffe7e7,stroke:#b33a3a,color:#501919;
    class C,I,Q,R check;
    class X stop;
~~~

| 確認対象 | 読み取り専用で行うこと | このチェックでは確認しないこと |
| --- | --- | --- |
| Python・ネイティブライブラリ | モニターのモジュールと caen_libs.caenhvwrapper を時間制限付きの別プロセスでimport | 後続の全コード経路・実機の動作 |
| 認証情報 | CAENユーザー名・パスワードの設定有無だけ確認。値は表示しない | 実際のログインは標準の機器確認モードで確認 |
| アラート入力 | 指定ファイル、フラグの形式、更新からの経過時間を確認 | 生成元の健全性。モニター本体には古い入力を拒否する処理はない |
| HV1（.12）・HV2（.13）・monitor2 HV3（.90） | GETでOpenAPI・キュー・ポートラベルを取得。設定ポートと停止APIの定義を照合 | POST・コマンド投入・シリアル機器の応答試験・実際のHV出力 |
| 出口・入口マスフロー | それぞれ独立してCPLのバルブ状態を読み、応答とチェックサムを検証 | バルブを閉める命令・実際の流量 |
| Kikusui .45・.42 | OUTP? で出力状態を問い合わせ | OUTP OFF・出力の実測 |
| CAEN BLC2 .51 | 全使用スロットのチャンネル名を読み、BLC2を含む対象を表示 | チャンネル設定の書き込み |
| Pi .12・.13 のUSB | SSH認証・既知のホスト鍵・実行ファイルを確認。sudo -n -l で各命令の許可を照会 | uhubctlの実行・ハブの照会・USB電源の変更 |
| Discord | 必ず SKIP。URLの設定有無だけ表示し、値は表示しない | Webhookへのリクエスト・通知の送信試験 |

各チェックに時間制限があり、CAENのネイティブ処理やSSHが止まっても無期限には待ちません。
通信のタイムアウトは config.toml の値（現在10秒）を使用します。
--timeout 5 でチェック側だけ変更できます。失敗しても他の対象の確認は続けます。

**終了コード：** 選択した確認範囲に FAIL がなければ0、あれば1です。
WARN は、アラートが既に1、または入力ファイルが古い場合などです。
古さの目安は「10秒」と「監視間隔の3倍」の大きい方です。
--local だけでは機器通信の確認になりません。--devices は標準動作を明示する指定として引き続き使えます。
認証や権限の問題は報告するだけで、自動修正しません。

/home/sks/.venv/bin/python を直接指定しているため、activateしていなくても
その仮想環境で動きます。importの失敗と機器通信の失敗は別項目で表示します。
手元の仮想環境をactivateしても、接続先のAPIサーバーが起動するわけではありません。

## 2. 実機モニターを起動すると

~~~mermaid
flowchart TD
    A["monitor.py を起動<br/>標準動作は実機モード"] --> B["設定を検証<br/>二重起動防止ロックを取得"]
    B --> C["古い有効な操作ファイルを消費<br/>.env を読む・既存の環境変数が優先"]
    C --> K{"必要なCAEN認証情報がある？"}
    K -->|ない| E["起動エラーを記録して終了<br/>機器への命令は送らない"]
    K -->|ある| S["1秒ごとに Alert_H2leak を読む"]
    S --> V{"有効な0または1が<br/>ちょうど1件ある？"}
    V -->|いいえ| BAD["警告を出し、最後の有効値を保持<br/>読めるようになるまで待つ"]
    BAD --> S
    V -->|はい| FIRST{"最初の有効値？"}
    FIRST -->|はい| ONE{"既に1？"}
    ONE -->|いいえ・0| S
    ONE -->|はい| CHOICE{"起動時の選択"}
    CHOICE -->|q・exit| EXIT["機器を操作せず終了"]
    CHOICE -->|Enter・wait・端末なし| RESET["一度0に戻り<br/>新しく0から1になるまで待つ"]
    RESET --> S
    FIRST -->|いいえ| EDGE{"前の有効値が0で<br/>今回の値が1？"}
    EDGE -->|いいえ| S
    EDGE -->|はい| RUN["停止シーケンスを確定<br/>次の図へ"]
    classDef alert fill:#ffe7e7,stroke:#b33a3a,color:#501919;
    classDef wait fill:#e8f1ff,stroke:#3568a8,color:#182c44;
    class RUN alert;
    class S,BAD,RESET wait;
~~~

標準の監視ファイルは
/home/sks/share/monitor-tmp/H2tgtPresentStatus.txt、
対象キーは Alert_H2leak です。
ファイルがない、読めない、不正な値、同じキーが複数ある場合は、
その入力だけでは停止を開始せず、直前の有効値を保持します。
有効な0が続いている間も停止は繰り返しません。

.env はプロジェクト直下（app/ の外側）に置きます。
python-dotenv があれば実機モニターの起動時に読み込みます。
編集内容の反映にはプロセスの起動し直しが必要です。
同名の環境変数が既にあれば、そちらが優先されます。
CAEN認証情報がなければ起動を中止します。
Discordの設定がなくても、通知を無効にして監視は続けます。
.env・実際の認証情報・Webhook URLはコミットしません。

## 3. 新しく0 → 1になった後の順序

**停止シーケンスは1回実行してPythonプロセスを終了します。
開始後は入力を読まなくなるため、フラグが0に戻っても中止しません。**

~~~mermaid
flowchart TD
    A["Alert_H2leak が0から1へ"] --> N1["Discord通知を送信キューへ<br/>停止シーケンス開始"]
    N1 --> H12["1. HV1 .12 のポート1・2・3<br/>BAC・KVC-1・KVC-2 をOFF"]
    H12 --> H13["2. HV2 .13 のポート0・1・2・3<br/>HTOF Up・Down・High と T1 をOFF"]
    H13 --> H90["3. monitor2 .90:8000 のポート0・1・2<br/>T2・SCH・BH2 をOFF"]
    H90 --> MF["4. マスフロー2台<br/>両方の接続確認後、出口から入口の順に閉止<br/>詳細は第5節"]
    MF --> K45["5. BLC2Vth・Kikusui .45 に OUTP OFF<br/>出力状態を問い合わせ"]
    K45 --> CA["6. CAEN .51：名前にBLC2を含む対象<br/>スロットごとに一括OFF・OFFを読み戻す<br/>現在はスロット4のch 4〜7、BLC1/T0は対象外"]
    CA --> WAIT["7. 60分待つ<br/>ここまでの試行が終了してから計時開始"]
    WAIT --> CONT{"待機が満了・スキップされた？<br/>詳細は第4節"}
    CONT -->|キャンセル| CANCEL["残りの機器操作を全て省略<br/>既に実行した操作は戻さない"]
    CONT -->|はい| U12["8. Pi .12 のUSBポート1・2・3・4をOFF"]
    U12 --> U13["9. Pi .13 のUSBポート1・2・3・4をOFF"]
    U13 --> K42["10. 最後にshutdownタップ用5V・.42をOFF<br/>出力状態を問い合わせ"]
    K42 --> SUM["Discordに最終結果を送信キューへ<br/>完了・エラー付き完了"]
    CANCEL --> CS["Discordに最終結果を送信キューへ<br/>キャンセル・それまでのエラーも記載"]
    SUM --> EXIT["結果をログに残してPython終了"]
    CS --> EXIT
    classDef action fill:#fff0d9,stroke:#a66a17,color:#51350c;
    classDef wait fill:#e8f1ff,stroke:#3568a8,color:#182c44;
    classDef notify fill:#eee7ff,stroke:#7656a5,color:#35234f;
    class H12,H13,H90,MF,K45,CA,U12,U13,K42 action;
    class WAIT wait;
    class N1,SUM,CS notify;
~~~

| 対象 | 現在の接続先 |
| --- | --- |
| HV1 | 192.168.20.12:8000、ポート1 BAC・2 KVC-1・3 KVC-2 |
| HV2 | 192.168.20.13:8000、ポート0 HTOF Up・1 HTOF Down・2 HTOF High・3 T1 |
| monitor2 HV3 | 192.168.20.90:8000、ポート0 T2・1 SCH・2 BH2（55V） |
| マスフロー | 出口 192.168.20.17:4196 → 入口 192.168.20.14:4001、機器アドレス1 |
| BLC2Vth（先に止めるKikusui） | 192.168.20.45:5025 |
| CAEN BLC2 | 192.168.20.51、全スロットから名前で選択。現在はスロット4のch 4〜7 |
| USB電源 | Pi .12 → .13 へSSH、ハブ1-1・ポート1〜4 |
| shutdownタップ用5V（最後のKikusui） | 192.168.20.42:5025 |

旧 .91 のHV対象は削除しました。monitor2のポート0 T2・1 SCH・2 BH2を対象にしています。
別のHV4 API（:8001）は、このHVグループの対象には加えていません。
Kikusui .45 はBLC2Vth、.42 はshutdownタップにつながる5V電源です。
shutdownタップ用5VはUSB停止後の最後にOFFにします。

CAENは全使用スロットのチャンネル名を読み、大文字小文字を区別せずBLC2を含むものを選びます。
現在の対象はスロット4のch 4〜7（BLC2a-cat・BLC2a-pot・BLC2b-cat・BLC2b-pot）です。
同じスロットのch 0〜3のBLC1、およびスロット8のT0は対象外です。
OFF直前にも名前と配置を照合し、一致する対象がない・名前や配置が変わった場合は、
推測で操作せずエラーにします。

対象チャンネルをスロットごとにまとめ、1回の Pw=0 で出力OFFを指定します。
これはmonitor3の chamber_off.py と同じ電源スイッチの操作です。V0Setは変更しません。
複数スロットに分かれている場合は順番に命令するため、全スロットの厳密な同時実行ではありません。
命令後は対象全チャンネルのPwを読み戻し、最大3秒（または試行タイムアウトの半分）まで
OFFを待ちます。OFF未確認はエラーとして再試行します。設定電圧は保持し、
実際の電圧の放電完了を確認するものではありません。

HV・USBはポートごと、CAENは対象スロットの一括OFFごとに再試行します。
現在は **最大3回（初回を含む）・1回10秒・再試行間隔2秒** です。
名前一覧を取得できなければCAENのOFFは行いません。対象スロットで失敗しても、
他の対象スロットの試行は続けます。
各エラーを記録し、残りの機器へ進みます。
タイムアウトは「機器側の結果が不明」であり、「命令が絶対に届いていない」ではありません。

タイムアウト後はワーカーのプロセス群へSIGKILLを送り、後始末の待機は追加で最大2秒です。
回収できなくても結果をUNKNOWN（不明）として記録し、既存の再試行・残りの対象の処理へ進みます。
後で回収するデーモンスレッドは制御ループの外で待ち、監視やプログラム終了を待たせません。
事前チェックでも同じ後始末の上限を使います。カーネル内で停止したプロセスの即時終了や、
遠隔機器への命令の成否まで保証するものではありません。

Discordはバックグラウンドで送信を試みます。
未設定・送信失敗でも停止処理は止めません。
現行コードの通知は開始時と最終結果であり、別途の復旧通知はありません。

命令の成功・応答の受信は、**全てのHV・バルブ・電源の物理的な停止確認とは異なります。**
処理完了・キャンセルでそのPythonプロセスは終了します。
その後の自動再起動の有無は、実際に導入したサービス定義に依存します。
リポジトリ内の雛形だけで判断せず、[サービス管理](../../README.md#service-control)を参照してください。

## 4. 60分待機中の操作

操作が有効なのは待機中です。スキップ後の猶予時間にもキャンセルできます。
既に行った操作を取り消したり、機器を再びONにしたりはしません。

~~~mermaid
flowchart TD
    W["待機ループ"] --> C{"cancel の要求がある？"}
    C -->|はい| STOP["残りの処理を中止<br/>最終通知して終了"]
    C -->|いいえ| S{"skip の要求がある？"}
    S -->|はい| G["5秒間の猶予"]
    G --> GC{"猶予中または期限到達時に<br/>cancel がある？"}
    GC -->|はい| STOP
    GC -->|いいえ| FINAL["USB停止へ進み<br/>最後にshutdownタップ用5V・.42を停止"]
    S -->|いいえ| E{"extend の要求がある？"}
    E -->|はい| RESET["今から丸60分にリセット"]
    RESET --> T{"待機期限になった？"}
    E -->|いいえ| T
    T -->|はい| FINAL
    T -->|いいえ| PAUSE["最大1秒待つ"]
    PAUSE --> W
    classDef wait fill:#e8f1ff,stroke:#3568a8,color:#182c44;
    classDef stop fill:#ffe7e7,stroke:#b33a3a,color:#501919;
    class W,G,RESET,PAUSE wait;
    class STOP stop;
~~~

| モニターを動かすホストの別端末で行う操作 | 結果 |
| --- | --- |
| touch /tmp/cancel.now | 残りの処理を中止。最優先 |
| touch /tmp/skip.now | 5秒後に最終停止へ進む。猶予中もキャンセル可能 |
| touch /tmp/extend.now | 通常待機を要求時点から丸60分にリセット |
| 何もしない | 待機期限が来たら自動的に最終停止へ進む |

同時に要求があれば **cancel → skip → extend** の順に優先します。
スキップの5秒猶予に入った後は extend を処理しません。
通常の待機・猶予の期限ちょうどでもキャンセルを確認します。

通常はモニターのユーザー（sks）で通常ファイルを作ってください。root所有の通常ファイルも受け付けます。
シンボリックリンク・特殊ファイル・他ユーザー所有のファイルは無視します。
信頼済みの要求を削除できない場合も、inodeと更新・変更時刻を記憶して1回だけ受け付けます。
同じファイルで待機を繰り返し延長することはありません。所有者が再度touchすると、新しい要求になります。
ファイルの情報を確認できない場合は警告して無視し、残りの停止処理は中断しません。
実機モニター起動時には、削除できず残ったものを含め古い有効な要求を消費します。
起動前に作っておいても、起動後に発動する要求にはなりません。

## 5. マスフロー2台の分岐

~~~mermaid
flowchart TD
    O["出口側の接続・状態を読み取り確認"] --> I["入口側の接続・状態を読み取り確認"]
    I --> BOTH{"両方の確認に成功？"}
    BOTH -->|いいえ| NONE["どちらにも閉止命令を送らない<br/>エラーを記録して BLC2Vth（.45）へ"]
    BOTH -->|はい| CLOSEO["出口側の閉止を試行<br/>応答を確認"]
    CLOSEO --> CLOSEI["入口側の閉止を試行<br/>出口側が失敗しても試す"]
    CLOSEI --> GOOD{"両方の閉止命令の応答を確認？"}
    GOOD -->|はい| DONE["命令レベルの成功を記録<br/>BLC2Vth（.45）へ"]
    GOOD -->|いいえ| PART["重大：閉止未完了として記録<br/>片側だけ閉まった可能性がある<br/>BLC2Vth（.45）へ"]
    classDef error fill:#ffe7e7,stroke:#b33a3a,color:#501919;
    class NONE,PART error;
~~~

各接続確認・各閉止操作に、それぞれ再試行回数と時間制限があります。
ペア全体のやり直しはしないため、片側の失敗だけを理由に成功済みの側を繰り返しません。
2台を原子的・同時確実に閉める保証はなく、自動で開き直す処理もありません。

## 実装との対応

| 挙動 | 実装 |
| --- | --- |
| 接続先・時間・操作順 | [config.toml](../../config/config.toml) |
| 実機起動・環境変数・ロック・終了 | [monitor.py](../../app/monitor.py) |
| 0→1の検知・再試行・待機・最終結果 | [engine.py](../../app/lh2_monitor/engine.py) |
| 入力・操作ファイル・通知送信 | [adapters.py](../../app/lh2_monitor/adapters.py) |
| 実際の機器プロトコル | [hardware.py](../../app/lh2_monitor/hardware.py) |
| 読み取り専用の事前チェック | [preflight.py](../../app/lh2_monitor/preflight.py) |
| ワーカーの後始末の時間制限 | [processes.py](../../app/lh2_monitor/processes.py) |
| 完全仮想の動作確認 | [simulation.py](../../app/lh2_monitor/simulation.py) |

機器なしで挙動を試す場合は、[プロジェクトREADME](../../README.md#offline-testing)
にある明示的な --simulate を使用してください。
標準の機器事前チェックと違い、シミュレーションでは機器に一切接続しません。
