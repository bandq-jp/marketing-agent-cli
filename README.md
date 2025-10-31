# marketing-agent-cli

WordPress 公式 MCP アダプターと OpenAI Agents SDK (Agents MCP 拡張) を組み合わせ、WordPress・GA4・GSC などのデータからマーケティング改善案を生成する CLI です。WordPress からの情報取得は Model Context Protocol (MCP) サーバー経由で行い、従来の直接 REST 呼び出しは行いません。

## 仕組みの概要

- OpenAI Agents SDK に同梱される MCP クライアントを利用し、MCP サーバー（WordPress 等）が公開するツールを動的に読み込みます。citeturn30click0
- WordPress MCP アダプターのデフォルトサーバーは `/wp-json/mcp/mcp-adapter-default-server` をエンドポイントとした HTTP MCP を提供し、認証済みユーザーのみが利用できます。citeturn28open0
- WordPress が提供する WP-CLI コマンド `wp mcp-adapter serve` を使えば STDIO MCP としてローカル開発にも接続可能です。citeturn27open0

## 前提条件

1. **Python 3.12+**（推奨：`uv` や `venv` で仮想環境を用意）
2. **OpenAI API キー**（環境変数 `OPENAI_API_KEY`）
3. **WordPress サイト**  
   - WordPress 6.8 以降 + [WordPress MCP Adapter](https://github.com/WordPress/mcp-adapter) 有効化  
   - 管理者または `read` 権限を持つユーザーの [アプリケーションパスワード](https://wordpress.org/documentation/article/application-passwords/) など（HTTP モード利用時）
   - `wp` コマンド（WP-CLI）を実行可能な環境（STDIO モード利用時）

## WordPress MCP アダプターの準備

1. WordPress へ MCP アダプターをインストールし有効化（Composer / プラグインいずれか）。  
2. 公開したいアビリティに `mcp.public = true` メタデータを付与（デフォルトサーバーにより Discover / Get Info / Execute が提供されます）。citeturn28open0
3. 認証方式を決定  
   - HTTP モード: アプリケーションパスワードやトークンを Authorization ヘッダー経由で送信  
   - STDIO モード: WP-CLI で `wp mcp-adapter serve --user=<ユーザー>` を起動（WordPress サイト上で実行）citeturn27open0

## 接続モード

### 1. HTTP (streamable_http) モード

- **エンドポイント**: `https://{サイト}/wp-json/mcp/mcp-adapter-default-server`（必要に応じてカスタムサーバーのルートに変更）citeturn28open0
- **認証**: Authorization ヘッダーを付与。  
  - アプリケーションパスワードの場合は Basic 認証 (`username:application-password`) を Base64 化して送信。  
  - トークン制御したい場合は Bearer トークンを指定。
- CLI 側では `WP_MCP_TRANSPORT=streamable_http` を指定し、`WP_MCP_HTTP_*` 系の変数で URL・ヘッダー・資格情報を渡します。

### 2. STDIO モード（WP-CLI）

- WordPress が稼働するサーバー上で以下を起動:

  ```bash
  wp mcp-adapter serve --user=admin --server=mcp-adapter-default-server
  ```

- CLI 側では `WP_MCP_TRANSPORT=stdio` を設定し、`WP_MCP_STDIO_COMMAND`（既定 `wp`）と `WP_MCP_STDIO_ARGS`（既定 `mcp-adapter serve`）を通してプロセスを起動します。必要に応じて `WP_MCP_STDIO_CWD` で WordPress ルートを指示してください。citeturn27open0

## 環境変数と CLI オプション

| 変数 / オプション | 説明 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API キー |
| `WP_MCP_TRANSPORT` / `--wp-mcp-transport` | `streamable_http`（既定）または `stdio` |
| `WP_MCP_NAME` / `--wp-mcp-name` | MCP サーバー識別子（既定 `wordpress`） |
| `WP_MCP_HTTP_URL` | HTTP モード時の MCP エンドポイント URL |
| `WP_MCP_HTTP_USERNAME` / `WP_MCP_HTTP_PASSWORD` | Basic 認証用のユーザー名・パスワード |
| `WP_MCP_HTTP_BEARER` | Bearer トークン（設定時は Authorization: Bearer を自動付与） |
| `WP_MCP_HTTP_HEADERS` | 追加 HTTP ヘッダー（JSON もしくは `key=value` カンマ区切り） |
| `WP_MCP_STDIO_COMMAND` | STDIO モード時の実行コマンド（既定 `wp`） |
| `WP_MCP_STDIO_ARGS` | STDIO モード時の引数（既定 `mcp-adapter serve`） |
| `WP_MCP_STDIO_CWD` | STDIO プロセスを実行するカレントディレクトリ（WordPress ルート推奨） |
| `WP_MCP_STDIO_ENV` | STDIO プロセスに付与する追加環境変数 |
| `GA4_PROPERTY_ID` / `--ga4-property-id` | GA4 コネクタのプロパティ ID |
| `GSC_SITE_URL` / `--gsc-site-url` | GSC コネクタのサイト URL |
| `SERPAPI_API_KEY` | SerpAPI コネクタ向けキー |
| `AHREFS_API_KEY` | Ahrefs MCP（モック）向けキー |

CLI フラグは同名の環境変数より優先されます。

## 実行例

```bash
# 例: HTTP モード + アプリケーションパスワードで接続
export OPENAI_API_KEY=sk-xxxx
export WP_MCP_TRANSPORT=streamable_http
export WP_MCP_HTTP_URL=https://example.com/wp-json/mcp/mcp-adapter-default-server
export WP_MCP_HTTP_USERNAME=api-user
export WP_MCP_HTTP_PASSWORD=app-password

uv run main.py "最近30日の自然検索流入が落ちた理由を分析して"
```

引数なしで起動すると対話モードになり、`/exit` や `/help` で制御できます。

## トラブルシューティング

- **「WordPress MCP のツールが見つからない」**  
  - WordPress 側で MCP アダプターが有効化されているか確認  
  - HTTP モードでは URL パスと認証ヘッダーが正しいか再確認（`mcp-adapter-default-server` のエンドポイントは `/wp-json/mcp/...`）citeturn28open0
- **STDIO モードで接続できない**  
  - `wp mcp-adapter serve` を起動している端末が標準入出力を CLI に公開しているか確認  
  - `--user` で指定した WordPress ユーザーの権限が十分か確認citeturn27open0
- **401/403 が返る**  
  - WordPress 側でログイン済みユーザー（あるいはアプリケーションパスワード）が有効かチェック  
  - Authorization ヘッダーが二重定義になっていないか、Bearer/Basic が正しく設定されているか確認

## 参考リンク

- WordPress MCP Adapter リポジトリ（ドキュメント含む）citeturn28open0
- MCP Adapter CLI Usage（WP-CLI 経由の STDIO 接続）citeturn27open0
- OpenAI Agents SDK MCP ガイドciteturn30click0
