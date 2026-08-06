# meta-horizon-developer-mcp

Meta Horizon の開発者ダッシュボード (developers.meta.com/horizon) の **Analytics** を
読み取り専用で収集する stdio MCP サーバ。

他プロジェクトから使う入口は **MCP**。同梱の `meta-horizon-developer` CLI は
**収集する owner だけ**が使う (人手の認証と cron の定期収集)。

認証済みブラウザで Analytics ページを開き、**Dashboard 自身が発行した** persisted
GraphQL `Query` の成功応答だけを傍受し、秘匿値を落として不変 JSON へ保存する。
リクエストを組み立てて投げ直さない。Mutation も任意 URL の取得も持たない。

対象アプリ (`app_id`) と保存先は呼び出し側が決める。収集した値の下流での扱い
(DuckDB への登録など) はこのパッケージの責務ではない。

## 使い方

```bash
# 1. 人手でログインする (専用の Chromium profile に cookie が残る)
meta-horizon-developer --app-id <APP_ID> --profile-dir .state/meta-horizon-developer/profile auth

# 2. 収集する (cron 向け。不変 JSON を 1 ファイル書く)
meta-horizon-developer --app-id <APP_ID> --snapshot-dir data/raw/meta-store collect

# 3. 状態を見る
meta-horizon-developer --app-id <APP_ID> --snapshot-dir data/raw/meta-store status
```

引数の代わりに環境変数でも渡せる。

| 環境変数 | 対応する引数 |
|---|---|
| `META_HORIZON_APP_ID` | `--app-id` |
| `META_HORIZON_OUTPUT_DIR` | `--snapshot-dir` |
| `META_HORIZON_PROFILE_DIR` | `--profile-dir` |
| `META_HORIZON_CSV_DIR` | `--csv-dir` |
| `META_HORIZON_LOOKBACK_DAYS` | `--lookback-days` (既定 120) |
| `META_HORIZON_STATE_DIR` | 上記 3 つの既定の親 (既定 `./.state/meta-horizon-developer`) |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE` | Chromium の実行ファイル |

## MCP

stdio MCP サーバとして起動する。ホストへの登録方法はホスト側の流儀に従う
(gateway 経由でも、ホストの MCP 設定へ直接でもよい)。必要なのは実行ファイルの
パスと `--app-id` / `--snapshot-dir` だけ。

```json
{
  "mcpServers": {
    "meta-horizon-developer": {
      "command": "/path/to/.venv/bin/meta-horizon-developer-mcp",
      "args": ["--app-id", "<APP_ID>", "--snapshot-dir", "/path/to/data/raw/meta-store"]
    }
  }
}
```

公開するツールは 4 つだけ。

- `meta_horizon_developer_status` — 認証の有無と最新スナップショットの健全性
- `meta_horizon_developer_collect` — 収集を実行する
- `meta_horizon_developer_latest_snapshot` — 最新スナップショットのメタデータ (本体は返さない)
- `meta_horizon_developer_read_operation` — 許可された Query を 1 件読む

**認証・任意 URL・任意 operation・Mutation・Cookie/token は公開しない。**
認証は人手が要るので CLI 側にだけ置く。

## 守っている前提

- **profile は同時に 1 プロセスしか開けない。** `auth` / `collect` / `capture-csv` は
  排他ロックを取り、取れなければ待たずに `ProfileBusyError` で落ちる
- **1 ディレクトリ 1 アプリ。** 別 `app_id` のスナップショットがある場所へは書かない
  (`AppIdMismatchError`)
- **`--password-store=basic` を必ず付ける。** キーリングの無い環境では Chromium が
  別の鍵を選び、保存済み cookie を 1 件も復号できないまま「認証切れ」に見える
- **プロキシは認証情報を分けて渡す。** URL に埋め込んだままだと Chromium は CONNECT に
  使わず、407 → `ERR_TUNNEL_CONNECTION_FAILED` で全ページが開けない
- **集計期間は URL の `start` / `end`。** プリセットは最長 28 日しか選べないため、
  カスタム期間 (既定 120 日) を URL で渡して Dashboard により長い日次系列を出させる
- **収集が 0 件なら空のスナップショットを残さず失敗する。** 部分欠損は
  `missing_operations` / `missing_required_operations` に残す

## スナップショットの形

```json
{
  "schema_version": 1,
  "collected_at": "2026-08-06T01:23:45Z",
  "source": "meta-horizon-dashboard",
  "app_id": "…",
  "operations": [{"name": "…Query", "doc_id": "…", "data": {…}, "errors": null}],
  "missing_operations": ["…"],
  "missing_required_operations": ["…"]
}
```

ファイル名は `meta-store-<UTC>.json`、権限は 0600。**上書きしない**。

`dashboard.json` は収集対象の Dashboard ページと許可 operation の一覧
(= Dashboard の契約) であり、どのアプリを見るかは含まない。画面構成はアプリに
よらず同じなので、この許可リストは全アプリで共有する。

## 秘匿値

- cookie は専用 profile にだけ残る。設定ファイルにもスナップショットにも書かない
- 応答は保存前に redact する (authorization / cookie / token / session / email /
  user_id / dtsg など)
- profile とスナップショットは **git 管理下へ置かない**
