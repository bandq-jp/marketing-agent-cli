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
