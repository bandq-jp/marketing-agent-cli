以下は、**マーケ部門専用 AI エージェント（MVP）**を、**OpenAI Agents SDK**と**Python**でバックエンドのみ先に完成させるための**実装指示書 兼 要件定義書 兼 参考情報まとめ**です。
要件どおり **「提案までを出力」**にフォーカスし、**WordPress／GA4／GSC／SerpAPI／Ahrefs MCP**等を**読み取り系ツール**として横断利用し、**編集・実行（書き込み）処理は行わない**構成にしています。実装は**uv（Astral）**でワンファイル実行できる形（PEP 723）で提示します。

---

## 0. エグゼクティブサマリー（MVP像）

* **目的**：最新の記事動向をワンクエリで横断分析し、**改善提案（戦術レベル）**までを**構造化出力**で返す社内アシスタントを提供する。
* **対象**：コンテンツ企画・SEO担当。ノーコードで「状況把握→改善案素案」を即時に得たい利用者。
* **スコープ（MVP）**：

  1. **記事一覧の取得**（WordPress REST／読み取りのみ）
  2. **PV/流入の把握**（GA4 Data API `runReport`）
  3. **検索クエリ・順位・CTR**（Search Console API `searchanalytics.query`）
  4. **競合SERP観測**（SerpAPI）
  5. **外部SEO資産（被リンク・KW難易度等）の取得**（Ahrefs MCP：読み取り）
  6. **提案の自動生成**（OpenAI Responses＋Agents SDKの**構造化出力**）
  7. **全ツールは読み取りスコープ**。**CMS更新／GA4設定変更／GSC操作**等は**絶対に実施しない**。
* **SDK**：**OpenAI Agents SDK（Python）** 最新（PyPI: `openai-agents 0.4.2` 2025-10-24）を採用。内部で**Responses API**や**ツール呼び出し**、**Structured Outputs**を活用。([PyPI][1])
* **実行形態**：**uv run** で依存解決〜実行（PEP723メタデータ内に依存列挙/排他日）。**shebang実行**も可能。([Astral Docs][2])

---

## 1. ユースケース（代表例）

* **ユーザー**：「最近の記事の動向は？」
* **エージェント**：

  * WordPressから**直近公開/更新の投稿リスト**を収集（`/wp/v2/posts`）。([WordPress Developer Resources][3])
  * GA4から**過去28〜30日のPV/セッション推移**＋**流入チャネル**（例：`sessionDefaultChannelGroup`）を集計（`runReport`）。([Google for Developers][4])
  * GSCから**主要クエリ・CTR・平均掲載順位**を抽出（`searchanalytics.query`）。([Google for Developers][5])
  * SerpAPIで**狙いKWの直近SERP**を取得し**競合見出し／サマリ**を把握。([SerpApi][6])
  * Ahrefs MCPから**被リンクやKW難易度の補助指標**（読み取り）を取得（**MCP 経由**）。([GitHub][7])
  * **構造化出力**スキーマ（提案プラン）に沿って**課題要約・優先施策・期待効果**を生成。([OpenAI GitHub][8])

---

## 2. 非機能要件

* **言語**：日本語応答（内部でlocale指定／プロンプト明示）。
* **安全性**：**読み取り専用**権限（GA4: analytics.readonly、GSC: webmasters.readonly、WP: Application Passwords読み取り、Ahrefs: APIキーのみ）。([Google for Developers][4])
* **再現性**：`uv run --script`＋**lock**を推奨。**exclude-newer**で2025-10-30以降を除外可能。([Astral Docs][2])
* **可観測性**：Agents SDKの**トレーシング**利用可（将来：外部SaaS送出）。([PyPI][1])
* **拡張性**：MCP/関数ツールを追加して**知識検索**や**BI接続**を段階的に拡張。([OpenAI GitHub][9])

---

## 3. 使用SDK・主要ライブラリ（2025/10/30時点）

* **OpenAI Agents SDK（Python）**：`openai-agents 0.4.2`（2025-10-24）([PyPI][1])
* **OpenAI Python**：`openai 2.6.1`（Responses API／Structured Outputs対応）([PyPI][10])
* **GA4 Data API クライアント**：`google-analytics-data 0.19.0`（2025-10-16）([PyPI][11])
* **Search Console API クライアント**：`google-api-python-client 2.185.0`（2025-10-17）([PyPI][12])
* **HTTP クライアント**：`httpx`（SerpAPI／WP REST 呼び出し用）
* **Pydantic v2**：**構造化出力**スキーマ検証用。Agents SDKはJSON Schema準拠の構造化出力を推奨。([OpenAI Platform][13])
* **uv（Astral）**：スクリプト実行＆依存解決／ロック／shebang。([Astral Docs][2])

> 備考：**SerpAPI**はRESTで直接叩きます（`/search`、`api_key`必須）([SerpApi][6])。
> **WordPress**は**Application Passwords**によるBasic認証で**REST GET**を使用（**読み取りのみ**）([WordPress Developer Resources][3])。
> **Ahrefs MCP**は**MCPサーバ（ローカル or リモート）**に接続（API v3キー要件／リモートMCPの案内あり）。([GitHub][7])

---

## 4. アーキテクチャ（バックエンド／エージェント）

```
User CLI
  └─ marketing_agent.py（uv run / PEP723）
       ├─ OpenAI Agents SDK: Agent / Runner / Structured Output
       ├─ Tools（Function-Tools）
       │    ├─ wordpress_list_posts()
       │    ├─ ga4_report_pages()
       │    ├─ gsc_query()
       │    ├─ serpapi_search()
       │    └─ ahrefs_mcp_*()    ※MCP経由（読み取り）
       ├─ （任意）Hosted MCP / Web Search Tool の追加余地
       └─ 出力：改善提案（構造化 JSON）＋短い要約
```

* **Agent**：`instructions` に**読み取り専用**と**提案まで**を厳格に記述。
* **Output Schema**：`ImprovementPlan`（Pydantic）を**AgentOutputSchema**に指定（**Strict JSON Schema**推奨）。([OpenAI GitHub][8])
* **ツール**は**関数ツール**で登録（GA4/GSC/WP/SerpAPI/Ahrefs MCP）。
* **セッション**：対話履歴を内部管理（必要に応じてfile/SQLite/Redisに拡張可）。([OpenAI GitHub][14])

---

## 5. セキュリティと資格情報

* **OpenAI**：`OPENAI_API_KEY`（環境変数）
* **SerpAPI**：`SERPAPI_API_KEY`（環境変数）。パラメータ：`q, gl, hl, num, start …`。([SerpApi][6])
* **WordPress**：`WP_BASE_URL`、`WP_APP_USER`、`WP_APP_PASSWORD`（Application Passwords）。([WordPress Developer Resources][15])
* **GA4**：`GOOGLE_APPLICATION_CREDENTIALS`（サービスアカウントjson）＋該当プロパティの**閲覧権限**。`runReport`使用。([Google for Developers][4])
* **GSC**：OAuth 2.0（Installed Appフロー／トークン保存）。**Search Console の `searchanalytics.query`**を読み取り。([Google for Developers][16])
* **Ahrefs MCP**：`AHREFS_API_KEY`。ローカル`npx @ahrefs/mcp`または**リモートMCP**設定（ベンダー記載）。([GitHub][7])

> すべて**読み取り権限のみ**で構成し、**書き込みAPIには接続しません**。

---

## 6. API 仕様（参考）

* **WordPress**：`GET /wp/v2/posts?per_page=…&after=…&orderby=modified` 等で新着・更新取得。([WordPress Developer Resources][3])
* **GA4 Data API**：`runReport`（dimensions例：`date`, `pagePath`, `sessionDefaultChannelGroup`／metrics例：`screenPageViews`, `sessions`）。([Google for Developers][4])
* **GSC**：`searchanalytics.query`（dimensions：`date`, `query`, `page`／metrics：`clicks`, `impressions`, `ctr`, `position`）。([Google for Developers][5])
* **SerpAPI**：`GET https://serpapi.com/search`（必須：`q`,`api_key`／オプション：`gl`,`hl`,`num`,`start` 等）。**organic_results / related_questions / people_also_ask**などを受領。([SerpApi][6])
* **Ahrefs MCP**：公式MCPサーバからAhrefs API v3に依存するツールを公開（ローカル/リモート）。**configにAPI_KEY**を設定。([GitHub][7])

---

## 7. 実装（完成スクリプト：`marketing_agent.py`）

> **使い方（例）**
>
> ```bash
> chmod +x marketing_agent.py
> ./marketing_agent.py \
>   --ga4-property-id 123456789 \
>   --gsc-site-url https://example.com/ \
>   --days 30 \
>   "最近の記事の動向は？"
> ```

> 初回のみGSCのOAuth認可がブラウザで開きます（トークンはローカル保存）。GA4はサービスアカウントを推奨。([Google for Developers][17])

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "openai-agents==0.4.2",
#   "openai==2.6.1",
#   "httpx>=0.27",
#   "pydantic>=2.8",
#   "google-analytics-data==0.19.0",
#   "google-api-python-client==2.185.0",
#   "google-auth>=2.35",
#   "google-auth-oauthlib>=1.2",
# ]
# [tool.uv]
# # 将来再実行時の再現性確保のため（任意）
# exclude-newer = "2025-10-30T00:00:00Z"
# ///

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field, HttpUrl, ValidationError

# OpenAI Agents SDK（Python）
from agents import Agent, Runner, function_tool, AgentOutputSchema  # noqa: E402

# ====== 環境変数 ======
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
WP_BASE_URL = os.getenv("WP_BASE_URL", "")  # 例: https://example.com
WP_APP_USER = os.getenv("WP_APP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "")  # "123456789"
GSC_OAUTH_CLIENT_JSON = os.getenv("GSC_OAUTH_CLIENT_JSON", "gsc_oauth_client.json")
GSC_TOKEN_JSON = os.getenv("GSC_TOKEN_JSON", "gsc_token.json")
AHREFS_API_KEY = os.getenv("AHREFS_API_KEY", "")  # MCP用（任意）

# ====== 参考: Google クライアント ======
from google.analytics.data_v1 import AnalyticsDataClient  # type: ignore
from google.analytics.data_v1.types import (  # type: ignore
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
from googleapiclient.discovery import build  # type: ignore
from google.oauth2.credentials import Credentials  # type: ignore

# ====== 構造化出力（提案プラン） ======
class PlanMetric(BaseModel):
    key: str
    label: str
    value: float | int | str

class PlanAction(BaseModel):
    title: str = Field(..., description="施策名（短く）")
    rationale: str = Field(..., description="根拠・洞察（要点）")
    expected_impact: str = Field(..., description="期待効果（例：PV+10%/CTR+0.5pt 等）")
    effort: str = Field(..., description="工数目安（S/M/L など）")
    dependencies: List[str] = Field(default_factory=list, description="前提／依存（担当・専門等）")
    kpis: List[str] = Field(default_factory=list, description="追跡指標（例：PV, CTR, 平均順位）")

class ImprovementPlan(BaseModel):
    summary: str = Field(..., description="総括（現状と課題）")
    metrics_snapshot: List[PlanMetric] = Field(default_factory=list, description="要点指標の抜粋")
    prioritized_actions: List[PlanAction] = Field(default_factory=list, description="優先施策（優先順）")
    cautions: List[str] = Field(default_factory=list, description="注意点・リスク")
    sources: List[str] = Field(default_factory=list, description="参照データの出典（URLや説明）")

# ====== コネクタ：WordPress（読み取りのみ） ======
async def wp_list_posts(per_page: int = 20, days: int = 30) -> List[Dict[str, Any]]:
    """
    最新/更新の投稿を取得（読み取り専用）。
    認証：Application PasswordsのBasic認証。
    """
    if not WP_BASE_URL:
        return []
    after = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
    url = f"{WP_BASE_URL.rstrip('/')}/wp-json/wp/v2/posts"
    params = {
        "per_page": per_page,
        "orderby": "modified",
        "order": "desc",
        "after": after,
        "_fields": "id,link,date,modified,slug,title",
    }
    auth = (WP_APP_USER, WP_APP_PASSWORD) if (WP_APP_USER and WP_APP_PASSWORD) else None
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, params=params, auth=auth)
        r.raise_for_status()
        return r.json()

# ====== コネクタ：GA4（runReport） ======
def ga4_report_pages(
    property_id: str,
    start_date: str,
    end_date: str,
    page_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    GA4 Data API v1: runReport。
    dimensions: date, pagePath, sessionDefaultChannelGroup
    metrics: screenPageViews, sessions
    """
    if not property_id:
        property_id = GA4_PROPERTY_ID
    client = AnalyticsDataClient()  # GOOGLE_APPLICATION_CREDENTIALS を前提
    dims = [Dimension(name="date"), Dimension(name="pagePath"), Dimension(name="sessionDefaultChannelGroup")]
    mets = [Metric(name="screenPageViews"), Metric(name="sessions")]
    req = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=dims,
        metrics=mets,
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=25000,
    )
    if page_paths:
        # ここで pagePath フィルタをかける実装は省略（用途により追加）
        pass
    resp = client.run_report(req)
    return {
        "dimension_headers": [dh.name for dh in resp.dimension_headers],
        "metric_headers": [mh.name for mh in resp.metric_headers],
        "rows": [[d.value for d in row.dimension_values] + [m.value for m in row.metric_values] for row in resp.rows],
    }

# ====== コネクタ：GSC（searchanalytics.query） ======
GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

def _gsc_credentials() -> Credentials:
    creds: Optional[Credentials] = None
    if os.path.exists(GSC_TOKEN_JSON):
        creds = Credentials.from_authorized_user_file(GSC_TOKEN_JSON, GSC_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request  # type: ignore
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GSC_OAUTH_CLIENT_JSON, GSC_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(GSC_TOKEN_JSON, "w") as token:
            token.write(creds.to_json())
    return creds

def gsc_query(site_url: str, start_date: str, end_date: str, dimensions: List[str]) -> Dict[str, Any]:
    creds = _gsc_credentials()
    svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": 25000,
    }
    res = svc.searchanalytics().query(siteUrl=site_url, body=body).execute()
    return res or {}

# ====== コネクタ：SerpAPI ======
async def serpapi_search(q: str, num: int = 10, gl: str = "jp", hl: str = "ja") -> Dict[str, Any]:
    if not SERPAPI_API_KEY:
        return {"error": "SERPAPI_API_KEY is not set."}
    params = {
        "engine": "google",
        "q": q,
        "gl": gl,
        "hl": hl,
        "num": num,
        "api_key": SERPAPI_API_KEY,
    }
    async with httpx.AsyncClient(timeout=40.0) as client:
        r = await client.get("https://serpapi.com/search", params=params)
        r.raise_for_status()
        return r.json()

# ====== （任意）コネクタ：Ahrefs MCP ======
# ここでは実運用では Hosted/Remote MCP または stdio MCP を用いる。
# Agents SDK の MCP 連携は公式ドキュメントに従いセットアップすること。
# （MVPでは必須ではないため、関数ツールでモック的にエンドポイントを叩く例を残す）
async def ahrefs_mcp_site_overview(domain: str) -> Dict[str, Any]:
    if not AHREFS_API_KEY:
        return {"warning": "AHREFS_API_KEY not set. Skipping Ahrefs MCP call."}
    # 実際は MCP に handoff / tool-call する。ここでは説明用のダミー応答。
    return {"domain": domain, "note": "Use Ahrefs MCP server via MCP tool in production."}

# ====== Agents SDK：関数ツールとして登録 ======
@function_tool
def tool_wp_list_posts(per_page: int = 20, days: int = 30) -> List[Dict[str, Any]]:
    """WordPress: 最新/更新記事の一覧（読み取り）"""
    return asyncio.get_event_loop().run_until_complete(wp_list_posts(per_page=per_page, days=days))

@function_tool
def tool_ga4_report(property_id: Optional[str], start_date: str, end_date: str) -> Dict[str, Any]:
    """GA4: PV/セッション推移レポート（読み取り）"""
    return ga4_report_pages(property_id or "", start_date, end_date)

@function_tool
def tool_gsc_query(site_url: str, start_date: str, end_date: str, dimensions: List[str]) -> Dict[str, Any]:
    """GSC: クエリ/ページ別 指標取得（読み取り）"""
    return gsc_query(site_url, start_date, end_date, dimensions)

@function_tool
def tool_serpapi(q: str, num: int = 10, gl: str = "jp", hl: str = "ja") -> Dict[str, Any]:
    """SerpAPI: Google SERP の取得（読み取り）"""
    return asyncio.get_event_loop().run_until_complete(serpapi_search(q, num, gl, hl))

@function_tool
def tool_ahrefs_site_overview(domain: str) -> Dict[str, Any]:
    """Ahrefs: サイト概観（読み取り / MCP 経由想定）"""
    return asyncio.get_event_loop().run_until_complete(ahrefs_mcp_site_overview(domain))

# ====== エージェント（提案までを構造化出力） ======
AGENT_INSTRUCTIONS = """
あなたは社内マーケ部門のアナリストAIです。次を厳密に守ってください。
- あなたは「読み取り専用」のツールだけを使います。CMS更新・公開・削除・API書き込み等は一切行いません。
- WordPress/GA4/GSC/SerpAPI/Ahrefsから得たデータを横断的に解釈し、「改善案（提案）」までを出力してください。
- 出力は指定の構造（JSON）で返します。説明の冗長化は避け、要点と根拠を簡潔に。
- 推奨KPI例：PV、セッション、CTR、平均掲載順位、流入チャネル別比率。
- 日本語で回答してください。
"""

def build_agent() -> Agent:
    return Agent(
        name="Marketing Analysis Agent",
        instructions=AGENT_INSTRUCTIONS,
        tools=[  # 読み取り系ツールのみ
            tool_wp_list_posts,
            tool_ga4_report,
            tool_gsc_query,
            tool_serpapi,
            tool_ahrefs_site_overview,
        ],
        # 構造化出力（Strict JSON Schema）
        output_type=AgentOutputSchema(ImprovementPlan, strict_json_schema=True),
        # モデルはデフォルト（Responses APIの既定モデル）を使用。必要なら model/settings を指定可
    )

# ====== CLI ======
def _date_span(days: int) -> tuple[str, str]:
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    return (start.isoformat(), end.isoformat())

def main():
    parser = argparse.ArgumentParser(description="Marketing Analysis Agent (read-only)")
    parser.add_argument("query", type=str, help="ユーザーからの要望（例：最近の記事の動向は？）")
    parser.add_argument("--ga4-property-id", type=str, default=GA4_PROPERTY_ID)
    parser.add_argument("--gsc-site-url", type=str, default=os.getenv("GSC_SITE_URL", ""))
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY is not set.")

    start, end = _date_span(args.days)

    # 解析前に、最低限の追加文脈を付与
    user_prompt = (
        f"{args.query}\n"
        f"- 解析期間: {start}〜{end}\n"
        f"- GA4 property: {args.ga4_property_id or '(未設定)'}\n"
        f"- GSC site: {args.gsc_site_url or '(未設定)'}\n"
        f"- WordPress: {WP_BASE_URL or '(未設定)'}\n"
        "必要に応じてツールを呼び出し、記事動向の要点と改善案を提案してください。"
    )

    agent = build_agent()
    # 1ターンで提案（必要に応じてツールを複数回実行）
    result = Runner.run_sync(agent, input=user_prompt, max_turns=10)

    # 構造化出力（Pydantic検証済）を取得
    try:
        plan: ImprovementPlan = result.structured_output  # type: ignore
        print(json.dumps(plan.model_dump(), ensure_ascii=False, indent=2))
    except Exception as e:
        # フォールバック：テキスト出力
        print(json.dumps({"summary_text": result.final_output, "error": str(e)}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
```

### 重要ポイント（実装上の根拠）

* **Agents SDK**は`Agent`に**ツール**と**構造化出力（AgentOutputSchema）**を設定し、`Runner`で実行するのが基本。**Strict JSON Schema**が推奨です。([OpenAI GitHub][14])
* **Responses API**は**内蔵ツール**（Web検索等）にも対応可能。必要に応じて**Hosted Tool**を追加できます（本MVPは自前関数ツールで完結）。([OpenAI Platform][18])
* **uv run**は**PEP 723**の**inline script metadata**を解釈し、**shebang**で実行可能。`uv lock --script`推奨。([Astral Docs][2])
* **WordPress**は**RESTのPostsエンドポイント**で**一覧・更新日時**等が取得可能。認証は**Application Passwords**が簡便。([WordPress Developer Resources][3])
* **GA4**は**Data API v1**の`runReport`で**任意のディメンション/メトリクス**（例：`date`, `pagePath`, `screenPageViews`）を取得。([Google for Developers][4])
* **GSC**は**`searchanalytics.query`**で**`clicks`/`impressions`/`ctr`/`position`**などを取得。([Google for Developers][5])
* **SerpAPI**は**`/search`**に`q`と`api_key`必須。`gl/hl/num/start`等で地域・言語・件数を制御。**organic_results**などを返します。([SerpApi][6])
* **Ahrefs MCP**は**MCPサーバ**（ローカル`npx @ahrefs/mcp` or リモート）で提供。**API_KEY**を設定してMCPクライアントからツールを叩きます（本MVPでは読み取り想定）。([GitHub][7])

---

## 8. セットアップ手順（ローカル最短）

1. **OpenAI**

   * `export OPENAI_API_KEY=…`
   * Agents SDK / Python ライブラリはスクリプト内のPEP723で自動導入（`uv`が必要）。([PyPI][1])

2. **uv（Astral）**

   * macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   * 実行：`./marketing_agent.py "最近の記事の動向は？"`（shebang式）
   * 依存ロック：`uv lock --script marketing_agent.py`（任意）。([Astral Docs][2])

3. **WordPress**（読み取りのみ）

   * 管理画面 → ユーザー → プロフィール → **Application Passwords**を発行。
   * 環境変数：`WP_BASE_URL`、`WP_APP_USER`、`WP_APP_PASSWORD` を設定。([WordPress Developer Resources][15])

4. **GA4 Data API**

   * GCPでプロジェクト作成→**Google Analytics Data API v1**有効化→**サービスアカウント**発行→GA4プロパティに**閲覧権限付与**→`GOOGLE_APPLICATION_CREDENTIALS`に鍵パス。([Google for Developers][17])
   * `GA4_PROPERTY_ID` をエクスポート。

5. **GSC API**

   * **OAuth クライアント（Installed app）**を作成し`GSC_OAUTH_CLIENT_JSON`に保存。初回実行時にブラウザ認可→`GSC_TOKEN_JSON`へトークン保存。([Google for Developers][16])

6. **SerpAPI**

   * ダッシュボードでAPIキー取得→`SERPAPI_API_KEY`設定。パラメタ仕様は公式に準拠。([SerpApi][6])

7. **Ahrefs MCP**（任意／読み取り）

   * ローカル: `npm install -g @ahrefs/mcp` → MCPクライアント設定に`API_KEY`。
   * リモートMCPも提供あり（ドキュメント参照）。([GitHub][7])

---

## 9. テスト観点（MVP）

* **正常系**：

  * WordPressから直近30日の更新あり投稿が返る。([WordPress Developer Resources][3])
  * GA4 `runReport` が `date/pagePath` × `screenPageViews/sessions` を返す。([Google for Developers][4])
  * GSC `searchanalytics.query` が `query/page/date`で`clicks/impressions/ctr/position` を返す。([Google for Developers][5])
  * SerpAPI が `organic_results` を返す。([SerpApi][6])
  * 構造化出力 `ImprovementPlan` が **Strict JSON Schema**に一致。([OpenAI GitHub][8])

* **異常系**：

  * 資格情報未設定時は**エラー/警告**フィールドで明示。
  * APIレート制限時は**短時間スリープ→メッセージ**。
  * GSC OAuth未認可時は**ローカルサーバで認可**を促す（自動起動）。

---

## 10. 将来拡張

* **Hosted Web Search Tool**の追加（OpenAI内蔵）。**ニュースや外部記事**の根拠強化に有効。([OpenAI Platform][18])
* **MCP**の本実装（Ahrefs以外：GA4 MCP、GSC MCP等も）。
* **Guardrails**で入力/出力検証ルールを強化（社内用語・禁止事項チェック等）。([OpenAI GitHub][14])
* **セッション永続化**（SQLite/Redis/SQLAlchemyセッション）で会話状態を保持。([OpenAI GitHub][14])
* **ダッシュボード連携**や**Slack通知**（提案の定期配信）※提案のみ送付。

---

## 11. リスクと対策

* **計測差異**：GA4 UIとAPIの粒度差 → **同一ディメンション/メトリクス定義**を共有。([Google for Developers][4])
* **GSCのフィルタ仕様**：日付・ディメンション選択により**欠損日**が返らない場合 → **日次でクエリ**し補完。([Google for Developers][5])
* **地域/言語に依存するSERP**：`gl`/`hl`/`uule`等を明示。([SerpApi][6])
* **認証鍵の管理**：環境変数＋Secret管理、**ログに値を出力しない**。
* **法令/規約遵守**：各APIの**利用規約・レート制限**順守。

---

## 12. 参考（公式ドキュメント）

* **OpenAI Agents SDK**：概要・ツール・MCP・出力スキーマ・Runner 等（**最新版**）([OpenAI GitHub][14])
* **OpenAI Responses API（構造化出力／ツール）**([OpenAI Platform][19])
* **PyPI（バージョン根拠）**：`openai-agents 0.4.2`、`openai 2.6.1`([PyPI][1])
* **uv（Astral）**：`uv run --script`、PEP723、lock、shebang。([Astral Docs][2])
* **WordPress REST**：`/wp/v2/posts`、**Application Passwords**。([WordPress Developer Resources][3])
* **GA4 Data API v1**：Overview／Quickstart／Schema／`runReport`。([Google for Developers][4])
* **Search Console API**：Quickstart（Python）／`searchanalytics.query`／用語。([Google for Developers][16])
* **SerpAPI**：Google Search API（パラメータ仕様）。([SerpApi][6])
* **Ahrefs MCP**：GitHub（ローカルMCP／リモートMCP案内）。([GitHub][7])

---

### 最後に（導入の目安）

* **0.5日**：APIキー・OAuth準備／環境変数設定
* **0.5日**：`marketing_agent.py` の動作確認（小規模プロパティでのE2E）
* **1〜2日**：自社サイトのスキーマ・KPIラベル調整／提案の口調・優先度ロジックのカスタム
* **以降**：Ahrefs MCPやWeb検索ツールの本実装、セッション永続化、レポ自動配信

このドキュメントとスクリプトをベースに、**まずはバックエンドの読取分析→改善提案**の一連を**uv run**で完成させ、後段でUIへ流用する構成に最適化してあります。必要に応じて、MCP（Ahrefs等）やHostedツールを有効化してリサーチの自由度を高めてください。

[1]: https://pypi.org/project/openai-agents/ "openai-agents · PyPI"
[2]: https://docs.astral.sh/uv/guides/scripts/ "Running scripts | uv"
[3]: https://developer.wordpress.org/rest-api/reference/posts/ "Posts – REST API Handbook | Developer.WordPress.org"
[4]: https://developers.google.com/analytics/devguides/reporting/data/v1 "Google Analytics Data API Overview  |  Google for Developers"
[5]: https://developers.google.com/webmaster-tools/v1/searchanalytics/query?utm_source=chatgpt.com "Search Analytics: query | Search Console API"
[6]: https://serpapi.com/search-api "Google Search Engine Results API - SerpApi"
[7]: https://github.com/ahrefs/ahrefs-mcp-server "GitHub - ahrefs/ahrefs-mcp-server: Official Ahrefs MCP Server"
[8]: https://openai.github.io/openai-agents-python/ref/agent_output/ "Agent output - OpenAI Agents SDK"
[9]: https://openai.github.io/openai-agents-python/mcp/ "Model context protocol (MCP) - OpenAI Agents SDK"
[10]: https://pypi.org/project/openai/ "openai · PyPI"
[11]: https://pypi.org/project/google-analytics-data/?utm_source=chatgpt.com "google-analytics-data"
[12]: https://pypi.org/project/google-api-python-client/?utm_source=chatgpt.com "google-api-python-client"
[13]: https://platform.openai.com/docs/guides/structured-outputs?utm_source=chatgpt.com "Structured model outputs - OpenAI API"
[14]: https://openai.github.io/openai-agents-python/ "OpenAI Agents SDK"
[15]: https://developer.wordpress.org/rest-api/reference/application-passwords/?utm_source=chatgpt.com "Application Passwords – REST API Handbook"
[16]: https://developers.google.com/webmaster-tools/v1/quickstart/quickstart-python?utm_source=chatgpt.com "Quickstart: Run a Search Console App in Python"
[17]: https://developers.google.com/analytics/devguides/reporting/data/v1/quickstart?utm_source=chatgpt.com "Google Analytics API quickstart"
[18]: https://platform.openai.com/docs/guides/tools-web-search?api-mode=responses&utm_source=chatgpt.com "Enable web search with Responses API"
[19]: https://platform.openai.com/docs/api-reference/responses?utm_source=chatgpt.com "Responses API reference"
