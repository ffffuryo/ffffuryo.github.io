# 配備ガイド: 自宅 PC / Raspberry Pi + Tailscale でスマホから使う

自宅の PC や Raspberry Pi で FE Study を動かし、Tailscale 経由で外出先（通学中など）のスマホからアクセスする手順です。アプリはインターネットに公開されず、同じ Tailscale アカウントの端末からだけ見えます。

## 1. 構成図

```
[スマホ]  ──(Tailscale の暗号化トンネル)──  [自宅 PC / Raspberry Pi]
Safari / Chrome                             Tailscale Serve (HTTPS 443)
ホーム画面のアプリ                                │
                                              ▼
                                         127.0.0.1:8000  ← FE Study (Flask/waitress)
                                              │
                                         data/fe.db (SQLite)
```

- アプリは `127.0.0.1:8000` にだけ listen します（外部のネットワークインターフェースには開きません）。
- Tailscale Serve が tailnet 内の HTTPS 入口になり、`127.0.0.1:8000` へ中継します。
- 公開インターネットには出ません（後述の `tailscale funnel` は使いません）。

## 2. 前提

- 常時起動させたい機器（Windows / macOS / Linux PC、または Raspberry Pi）
- 同じ Tailscale アカウントでログインできるスマホ
- Python 3.10 以上（`python3 --version` で確認）
- インターネット接続（初回の問題データ取り込みで IPA の公開 PDF を取得します）

## 3. 手順

### 3-1. Tailscale をインストールしてログインする

| 対象 | 方法 |
|---|---|
| Linux / Raspberry Pi | 公式の手順 `curl -fsSL https://tailscale.com/install.sh \| sh` のあと `sudo tailscale up` |
| macOS | App Store または tailscale.com から「Tailscale」をインストールし、メニューバーのアイコンからログイン |
| Windows | tailscale.com からインストーラを実行し、トレイアイコンからログイン |
| スマホ（iPhone / Android） | ストアで「Tailscale」アプリをインストールし、**同じアカウント**でログイン |

- 配備するマシンとスマホが同じアカウント（同じ tailnet）に入っていることを確認してください。
- 確認: `tailscale status` で自分のマシンとスマホが一覧に出れば OK。

### 3-2. Admin コンソールで MagicDNS と HTTPS 証明書を有効にする

Tailscale Serve で `https://<マシン名>.<tailnet名>.ts.net/` の形の HTTPS アドレスを使うには、次の 2 つが必要です。

1. **MagicDNS** を有効化
2. **HTTPS 証明書**（HTTPS Certificates）を有効化

どちらも https://login.tailscale.com/admin/dns のページで設定します。未有効のまま `serve` を実行すると、証明書関連のエラーになります。

### 3-3. アプリをセットアップする

```bash
cd ~/ffffuryo.github.io/fe-study
bash scripts/setup.sh
```

- `.venv` の作成、`requirements.txt` のインストール、`python -m importer`（IPA からの問題取り込み）を順に行います。
- 取り込みは初回のみ・数分〜十数分かかります。失敗しても setup.sh は止まり、再実行コマンドが表示されます。

### 3-4. 動作確認（ローカル）

```bash
bash scripts/run.sh
```

同じ PC のブラウザで `http://127.0.0.1:8000/` を開いて、ホーム画面が出れば成功です。確認できたら `Ctrl+C` で止めます。

### 3-5. Tailscale Serve で tailnet 内に HTTPS 公開する

アプリを起動した状態（`run.sh` または後述の systemd / launchd）で、別のターミナルから実行します。

```bash
tailscale serve --bg 8000
```

- `--bg` は設定をバックグラウンドで保持するオプションです。`run.sh` とは別に、Tailscale 側がこの転送を保持します。
- 転送先は既定で `127.0.0.1:8000` です。
- 設定の確認:

  ```bash
  tailscale serve status
  ```

  `https://<マシン名>.<tailnet名>.ts.net` が 127.0.0.1:8000 へ転送されていることが表示されます。

- アドレスは `https://<マシン名>.<tailnet名>.ts.net/` です。`tailscale serve status` の先頭にも表示されます。

設定を取り消すとき:

```bash
tailscale serve reset
```

### 3-6. スマホから開く

1. スマホで Tailscale アプリが接続（オン）になっていることを確認する。
2. ブラウザ（Safari / Chrome）で `https://<マシン名>.<tailnet名>.ts.net/` を開く。
3. ホーム画面に追加する:
   - **iPhone (Safari)**: 共有ボタン → 「ホーム画面に追加」
   - **Android (Chrome)**: メニュー（︙）→ 「ホーム画面に追加」 または「アプリをインストール」

ホーム画面のアイコンからは、ブラウザのアドレスバーなしで開けるようになります。

## 4. 常時起動

PC を起動したら自動でアプリも立ち上がるように設定します。`tailscale serve --bg 8000` の設定は Tailscale 側に保持されますが、アプリ本体が起動していないと応答しないため、アプリの自動起動も合わせて設定してください。

### 4-1. Linux / Raspberry Pi（systemd ユーザーユニット）

雛形は `scripts/fe-study.service` にあります。

```bash
mkdir -p ~/.config/systemd/user
cp ~/ffffuryo.github.io/fe-study/scripts/fe-study.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fe-study.service

systemctl --user status fe-study.service     # 状態確認
journalctl --user -u fe-study.service -f    # ログ確認
```

**`loginctl enable-linger` が必要な理由**: ユーザーサービスは「そのユーザーがログインしている間」だけ動きます。ヘッドレスの Raspberry Pi で再起動後も自動で動かすには、一度だけ次を実行します。

```bash
sudo loginctl enable-linger "$USER"
```

- ユニットのパスが `~/ffffuryo.github.io/fe-study` と違う場合は、`fe-study.service` の `WorkingDirectory` と `ExecStart` を書き換えてください。

### 4-2. macOS（launchd）

`~/Library/LaunchAgents/com.example.fe-study.plist` を次の内容で作成します（`/Users/you` は実際のホームに置き換え）。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.example.fe-study</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/ffffuryo.github.io/fe-study/.venv/bin/python</string>
    <string>-m</string>
    <string>app</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/you/ffffuryo.github.io/fe-study</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/fe-study.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/fe-study.err</string>
</dict>
</plist>
```

読み込みと停止:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.fe-study.plist   # 開始
launchctl bootout  gui/$(id -u)/com.example.fe-study                                  # 停止・解除
```

- デスクトップ Mac はスリープするとアクセスできなくなります（後述のトラブルシュート参照）。

### 4-3. Windows（タスクスケジューラ）

- 「タスクスケジューラ」を開き、「タスクの作成」を選びます（「基本タスクの作成」より細かく設定できます）。
- 設定の要点:
  - **トリガー**: 「ログオン時」
  - **操作**: プログラムの開始
    - プログラム: `C:\Users\<you>\ffffuryo.github.io\fe-study\.venv\Scripts\python.exe`
    - 引数: `-m app`
    - 開始（作業ディレクトリ）: `C:\Users\<you>\ffffuryo.github.io\fe-study`
  - **条件**: 「AC 電源に接続しているときだけ」のチェックを外す
  - **設定**: 「タスクが失敗した場合、再起動する」にチェック
- Tailscale のトレイアイコンからログインし、`tailscale serve --bg 8000` を一度実行しておけば、以降は自動で転送されます。

## 5. セキュリティ

- **`tailscale funnel` は使わないでください。** funnel はインターネット全体に公開されるため、学習データや将来の機能が第三者から見える状態になります。
- アプリは **`127.0.0.1` にバインドしたまま** にしてください。外部からは必ず `tailscale serve` 経由でアクセスします。
- **`FE_HOST=0.0.0.0` にしないでください。** 同じ LAN 上の他の機器や、設定を誤ったルーター越しからも直接見えてしまいます。
- 公開先は tailnet 内の端末に限定されます。Tailscale の ACL で、スマホ以外の端末からのアクセスを制限することもできます。
- 問題データ（`data/`）と DB は git 管理しません（`.gitignore` 済み）。

## 6. バックアップ

学習履歴は `data/fe.db` にあります。アプリ稼働中でも、次のコマンドで整合性を保ったままコピーできます（`sqlite3` コマンドが必要）。

```bash
cd ~/ffffuryo.github.io/fe-study
sqlite3 data/fe.db ".backup backup.db"
```

- `.backup` は SQLite の正式な方法で、単純な `cp` より安全です（WAL モードでも一貫したコピーになります）。
- `backup.db` は好きな場所（外付けドライブ、クラウドストレージの同期フォルダなど）に移してください。
- 復元する場合は、アプリを停止してから `backup.db` を `data/fe.db` として置き換えます。
- 取り込みデータ（`data/images/`、`data/cache/`）は IPA から再取得できるため、DB だけ保存すれば十分です。

## 7. トラブルシュート

| 症状 | 確認すること |
|---|---|
| スマホでつながらない | スマホとマシンの両方で Tailscale がオンか（`tailscale status`）。同じアカウントか。 |
| `https://...ts.net` にアクセスできない | `tailscale serve status` で転送が出ているか。出ていなければ `tailscale serve --bg 8000` を再実行。 |
| 証明書のエラー、「安全ではありません」 | Admin コンソールで **HTTPS 証明書** と **MagicDNS** が有効か（3-2 参照）。有効化直後は数分待ってから再試行。 |
| 502 / 接続拒否 | アプリが起動していない。`bash scripts/run.sh` か `systemctl --user status fe-study.service` で確認。 |
| ポート 8000 が使用中（`Address already in use`） | 既に別のアプリか、起動済みの FE Study が動いている。`lsof -i :8000`（macOS/Linux）や `netstat -ano \| findstr :8000`（Windows）で確認し、重複を止める。別ポートを使う場合は `FE_PORT=8001 bash scripts/run.sh` のように指定し、`tailscale serve --bg 8001` で転送先も変える。 |
| 問題が 0 件・画面が空 | 問題データの取り込みが未完了の可能性。`cd fe-study && .venv/bin/python -m importer` で再実行。 |
| 転送設定を消したい／作り直したい | `tailscale serve reset` のあと `tailscale serve --bg 8000`。 |
| PC がスリープするとつながらない | マシンがスリープ中はサーバーが止まります。下記の対策を。 |

**スリープ対策**
- Windows: 「電源とスリープ」でスリープを「なし」にする（ディスプレイだけ消灯でよい）。
- macOS: システム設定の「ディスプレイ」「バッテリー」「電源」で、スリープしない設定にする。一時的には `caffeinate -i` を実行したターミナルを開いたままにする方法もあります。
- Linux / Raspberry Pi: `systemctl status sleep.target` で状態を確認し、サスペンドが不要ならマスクする（例: `sudo systemctl mask sleep.target suspend.target`）。Raspberry Pi は通常スリープしません。

## 8. 参考

- 起動方法・環境変数: `scripts/run.sh`、`docs/SPEC.md` 1章
- systemd 雛形: `scripts/fe-study.service`
- 初期セットアップ: `scripts/setup.sh`
