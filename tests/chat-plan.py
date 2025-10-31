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
#   "python-dotenv>=1.0",
#   "rich>=13.8",
# ]
# [tool.uv]
# exclude-newer = "2025-10-30T00:00:00Z"
# ///

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field
import dotenv
from rich.console import Console
from rich.prompt import Prompt

# Agents SDK
from agents import (
    Agent,
    Runner,
    function_tool,
    AgentOutputSchema,
    ItemHelpers,
    SQLiteSession,
)

# Responses API テキストデルタ（任意で可視化）
from openai.types.responses import ResponseTextDeltaEvent

dotenv.load_dotenv()

# ====== 環境変数 ======
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
WP_BASE_URL = os.getenv("WP_BASE_URL", "")
WP_APP_USER = os.getenv("WP_APP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
GA4_PROPERTY_ID_ENV = os.getenv("GA4_PROPERTY_ID", "")
GSC_OAUTH_CLIENT_JSON = os.getenv("GSC_OAUTH_CLIENT_JSON", "gsc_oauth_client.json")
GSC_TOKEN_JSON = os.getenv("GSC_TOKEN_JSON", "gsc_token.json")
AHREFS_API_KEY = os.getenv("AHREFS_API_KEY", "")

# ====== Google Analytics Data API (v1beta) ======
from google.analytics.data_v1beta import BetaAnalyticsDataClient  # type: ignore
from google.analytics.data_v1beta.types import (  # type: ignore
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

# ====== Google Search Console ======
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
from googleapiclient.discovery import build  # type: ignore
from google.oauth2.credentials import Credentials  # type: ignore

# ====== 構造化出力モデル ======
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
def wp_list_posts(per_page: int = 20, days: int = 30) -> List[Dict[str, Any]]:
    if not WP_BASE_URL:
        return []
    after = (datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    url = f"{WP_BASE_URL.rstrip('/')}/wp-json/wp/v2/posts"
    params = {
        "per_page": per_page,
        "orderby": "modified",
        "order": "desc",
        "after": after,
        "_fields": "id,link,date,modified,slug,title",
    }
    auth = (WP_APP_USER, WP_APP_PASSWORD) if (WP_APP_USER and WP_APP_PASSWORD) else None
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, params=params, auth=auth)
        r.raise_for_status()
        return r.json()

# ====== コネクタ：GA4（runReport, v1） ======
def ga4_report_pages(
    property_id: str,
    start_date: str,
    end_date: str,
    page_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not property_id:
        return {"warning": "GA4 property is not configured. Skipping GA4 report."}
    client = BetaAnalyticsDataClient()
    dims = [Dimension(name="date"), Dimension(name="pagePath"), Dimension(name="sessionDefaultChannelGroup")]
    mets = [Metric(name="screenPageViews"), Metric(name="sessions")]
    req = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=dims,
        metrics=mets,
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=25000,
    )
    resp = client.run_report(req)
    return {
        "dimension_headers": [dh.name for dh in resp.dimension_headers],
        "metric_headers": [mh.name for mh in resp.metric_headers],
        "rows": [[d.value for d in row.dimension_values] + [m.value for m in row.metric_values] for row in resp.rows],
    }

# ====== コネクタ：GSC ======
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
    if not site_url:
        return {"warning": "GSC site URL is not configured. Skipping GSC query."}
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
def serpapi_search(q: str, num: int = 10, gl: str = "jp", hl: str = "ja") -> Dict[str, Any]:
    if not SERPAPI_API_KEY:
        return {"error": "SERPAPI_API_KEY is not set."}
    params = {"engine": "google", "q": q, "gl": gl, "hl": hl, "num": num, "api_key": SERPAPI_API_KEY}
    with httpx.Client(timeout=40.0) as client:
        r = client.get("https://serpapi.com/search", params=params)
        r.raise_for_status()
        return r.json()

# ====== Ahrefs MCP（モック） ======
def ahrefs_mcp_site_overview(domain: str) -> Dict[str, Any]:
    if not AHREFS_API_KEY:
        return {"warning": "AHREFS_API_KEY not set. Skipping Ahrefs MCP call."}
    return {"domain": domain, "note": "Use Ahrefs MCP server via MCP tool in production."}

# ====== 関数ツール登録 ======
from agents import function_tool

@function_tool
def tool_wp_list_posts(per_page: int = 20, days: int = 30) -> List[Dict[str, Any]]:
    """WordPress: 最新/更新記事の一覧（読み取り）"""
    return wp_list_posts(per_page=per_page, days=days)

@function_tool
def tool_ga4_report(property_id: Optional[str], start_date: str, end_date: str) -> Dict[str, Any]:
    """GA4: PV/セッション推移レポート（読み取り）"""
    return ga4_report_pages(property_id or GA4_PROPERTY_ID_ENV, start_date, end_date)

@function_tool
def tool_gsc_query(site_url: str, start_date: str, end_date: str, dimensions: List[str]) -> Dict[str, Any]:
    """GSC: クエリ/ページ別 指標取得（読み取り）"""
    return gsc_query(site_url, start_date, end_date, dimensions)

@function_tool
def tool_serpapi(q: str, num: int = 10, gl: str = "jp", hl: str = "ja") -> Dict[str, Any]:
    """SerpAPI: Google SERP の取得（読み取り）"""
    return serpapi_search(q, num, gl, hl)

@function_tool
def tool_ahrefs_site_overview(domain: str) -> Dict[str, Any]:
    """Ahrefs: サイト概観（読み取り / MCP 経由想定）"""
    return ahrefs_mcp_site_overview(domain)

# ====== エージェント定義（2モード） ======
CHAT_INSTRUCTIONS = """
あなたは社内マーケ部門のアナリストAIです。読み取り専用のツールのみを使い、自然に会話しながら状況把握・仮説出し・確認質問・軽い提案を行ってください。
- CMS更新や外部書き込みは絶対にしない。
- 必要に応じて WordPress / GA4 / GSC / SerpAPI / Ahrefs を呼び出す。
- 会話では簡潔かつ具体的に。数値や期間を明示。
- ユーザーが「JSON/構造化」が欲しいと言ったら、次ターンは Plan モードに切替することを提案。
"""

PLAN_INSTRUCTIONS = """
あなたは社内マーケ部門のアナリストAIです。読み取り専用のツールのみを使います。
- WordPress/GA4/GSC/SerpAPI/Ahrefs のデータに基づき、最終的に「改善提案」を厳密な構造化JSON（ImprovementPlan）で出力してください。
- 不足データは「取得できない」と明記。根拠は sources に列挙。
- JSON以外の余計なテキストは出力しない。
"""

def build_chat_agent(tools: List[Any]) -> Agent:
    # 出力タイプを設定しない → プレーンテキスト（柔軟会話）。:contentReference[oaicite:4]{index=4}
    return Agent(name="Marketing Chat Agent", instructions=CHAT_INSTRUCTIONS, tools=tools)

def build_plan_agent(tools: List[Any]) -> Agent:
    # 厳密な構造化出力（ImprovementPlan）。:contentReference[oaicite:5]{index=5}
    return Agent(
        name="Marketing Plan Agent",
        instructions=PLAN_INSTRUCTIONS,
        tools=tools,
        output_type=AgentOutputSchema(ImprovementPlan, strict_json_schema=True),
    )

# ====== CLI/REPL ======
console = Console(stderr=True)

def _date_span(days: int) -> tuple[str, str]:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days)
    return (start.isoformat(), end.isoformat())

def _preview(obj: Any, max_len: int = 800) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s if len(s) <= max_len else (s[:max_len] + "…")

def _extract_tool_name_and_args(raw_item: Any) -> tuple[str, str]:
    name = getattr(raw_item, "name", None)
    args = getattr(raw_item, "arguments", None)
    if name is None and isinstance(raw_item, dict):
        name = raw_item.get("name") or raw_item.get("tool_name") or str(raw_item.get("type") or "")
        args = raw_item.get("arguments")
    if name is None:
        name = raw_item.__class__.__name__
    if isinstance(args, (dict, list)):
        args_preview = _preview(args, 500)
    elif isinstance(args, str):
        try:
            args_preview = _preview(json.loads(args), 500)
        except Exception:
            args_preview = args[:500] + ("…" if len(args) > 500 else "")
    else:
        args_preview = "-"
    return name, args_preview

def _enabled_sources(ga4: bool, gsc: bool, serp: bool, ahrefs: bool) -> List[str]:
    s = ["WordPress"]
    if ga4: s.append("GA4")
    if gsc: s.append("GSC")
    if serp: s.append("SerpAPI")
    if ahrefs: s.append("Ahrefs MCP")
    return s

def _route_is_plan(query: str) -> bool:
    # 明示コマンド
    if query.strip().lower() in {"/plan"}:  # このターンのみPlan
        return True
    # 日本語キーワードで意図検出
    return bool(re.search(r"(JSON|構造化|スキーマ|ImprovementPlan|計画を出力|プラン出力)", query, re.IGNORECASE))

async def run_one_turn(
    chat_agent: Agent,
    plan_agent: Agent,
    session: Optional[SQLiteSession],
    user_query: str,
    *,
    days: int,
    ga4_property_id: str,
    gsc_site_url: str,
    model: Optional[str],
    max_turns: int,
    show_text_deltas: bool,
    current_mode: str,   # "chat" | "plan"
) -> Dict[str, Any] | str:
    start, end = _date_span(days)
    enabled_sources = _enabled_sources(
        bool(ga4_property_id), bool(gsc_site_url), bool(SERPAPI_API_KEY), bool(AHREFS_API_KEY)
    )

    # 毎ターンの補助文脈（ツール構成・期間など）
    user_context = (
        f"{user_query}\n"
        f"- 解析期間: {start}〜{end}\n"
        f"- GA4 property: {ga4_property_id or '(未設定)'}\n"
        f"- GSC site: {gsc_site_url or '(未設定)'}\n"
        f"- WordPress: {WP_BASE_URL or '(未設定)'}\n"
        f"- 使用するデータソース: {', '.join(enabled_sources)}\n"
        "必要に応じてツールを呼び出して回答してください。"
    )

    # ルーティング
    use_plan = (current_mode == "plan") or _route_is_plan(user_query)
    agent = plan_agent if use_plan else chat_agent

    # ストリーミング実行（進捗を逐次表示）:contentReference[oaicite:6]{index=6}
    run_config = {"model": model} if model else None
    result = Runner.run_streamed(
        agent,
        input=user_context,
        session=session,
        max_turns=max_turns,
        run_config=run_config,
    )

    console.rule(f"[bold cyan]Run started ({'PLAN' if use_plan else 'CHAT'})")
    printed_text_delta = False

    async for event in result.stream_events():
        if event.type == "raw_response_event":
            # Responses API のテキストデルタ（任意で可視化）:contentReference[oaicite:7]{index=7}
            if show_text_deltas and isinstance(event.data, ResponseTextDeltaEvent):
                # Chatモードのときは STDOUT にストリーム表示（タイピング風）
                if not use_plan:
                    sys.stdout.write(event.data.delta)
                    sys.stdout.flush()
                    printed_text_delta = True
            continue

        if event.type == "agent_updated_stream_event":
            console.print(f"[dim]🧠 agent updated → {event.new_agent.name}[/]")
            continue

        if event.type == "run_item_stream_event":
            item = event.item
            if item.type == "tool_call_item":
                name, args_preview = _extract_tool_name_and_args(item.raw_item)
                console.print(f"[yellow]🔧 tool.call[/] [bold]{name}[/] args={args_preview}")
            elif item.type == "tool_call_output_item":
                out_preview = _preview(item.output, 800)
                console.print(f"[green]✅ tool.result[/] {out_preview}")
            elif item.type == "message_output_item":
                # 途中の思考メッセージをログとして表示（本番は控えめ推奨）
                msg = ItemHelpers.text_message_output(item)
                if msg:
                    console.print(f"[blue]💬 message[/] {msg}")

    console.rule("[bold cyan]Run complete")

    # 最終出力の整形：
    if use_plan:
        # 構造化（ImprovementPlan）で返す
        try:
            plan = result.final_output_as(ImprovementPlan, raise_if_incorrect_type=True)
            return plan.model_dump()
        except TypeError as exc:
            # フォールバック
            fallback = result.final_output
            payload = fallback.model_dump() if isinstance(fallback, BaseModel) else (fallback if isinstance(fallback, dict) else {"text": str(fallback)})
            return {"summary_text": payload, "error": f"Structured output unavailable: {exc}"}
    else:
        # Chat：テキストで返す（デルタ表示済みなら改行だけ）
        if printed_text_delta:
            print()  # 改行
            return ""
        else:
            text = str(result.final_output or "")
            print(text)
            return text

def build_enabled_tools(ga4_property_id: str, gsc_site_url: str) -> List[Any]:
    tools: List[Any] = [tool_wp_list_posts]
    if ga4_property_id:
        tools.append(tool_ga4_report)
    if gsc_site_url:
        tools.append(tool_gsc_query)
    if SERPAPI_API_KEY:
        tools.append(tool_serpapi)
    if AHREFS_API_KEY:
        tools.append(tool_ahrefs_site_overview)
    return tools

async def repl_loop(
    chat_agent: Agent,
    plan_agent: Agent,
    session: Optional[SQLiteSession],
    *,
    days: int,
    ga4_property_id: str,
    gsc_site_url: str,
    model: Optional[str],
    max_turns: int,
    show_text_deltas: bool,
):
    console.print("[bold]対話を開始します。終了は /exit、モード切替は /mode chat|plan、単発構造化は /plan[/]")
    mode = "chat"  # 既定は柔軟会話
    while True:
        try:
            user_query = Prompt.ask(f"[bold magenta]You ({mode})[/]")
        except (EOFError, KeyboardInterrupt):
            break
        if not user_query:
            continue

        # モードコマンド
        low = user_query.strip().lower()
        if low in {"/exit", "exit", "quit"}:
            break
        if low.startswith("/mode"):
            target = low.split(" ", 1)[1] if " " in low else ""
            if target in {"chat", "plan"}:
                mode = target
                console.print(f"[bold green]Mode changed → {mode.upper()}[/]")
            else:
                console.print("[yellow]Usage: /mode chat | /mode plan[/]")
            continue

        payload = await run_one_turn(
            chat_agent, plan_agent, session, user_query,
            days=days, ga4_property_id=ga4_property_id, gsc_site_url=gsc_site_url,
            model=model, max_turns=max_turns, show_text_deltas=show_text_deltas,
            current_mode=mode,
        )

        # 返却：PLAN時は JSON を STDOUT、CHAT時はすでにテキストをSTDOUTへ出力済み
        if mode == "plan" or _route_is_plan(user_query):
            print(json.dumps(payload, ensure_ascii=False, indent=2))

def main():
    parser = argparse.ArgumentParser(description="Marketing Analysis Agent (interactive, READ-ONLY, dual-mode)")
    parser.add_argument("--session-id", type=str, default="marketing_cli", help="会話セッションID（SQLite保存）")
    parser.add_argument("--session-db", type=str, default="", help="SQLite DB ファイルパス（未指定ならインメモリ）")
    parser.add_argument("--ga4-property-id", type=str, default=GA4_PROPERTY_ID_ENV)
    parser.add_argument("--gsc-site-url", type=str, default=os.getenv("GSC_SITE_URL", ""))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--model", type=str, default=None, help="Responsesモデル（例：o4-mini 等）")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--show-text-deltas", action="store_true", help="テキストデルタを逐次表示（Chatモード）")
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY is not set.")

    # ツール有効化
    tools = build_enabled_tools(args.ga4_property_id.strip(), args.gsc_site_url.strip())
    if tools == [tool_wp_list_posts]:
        console.print("[dim]INFO: Optional connectors (GA4/GSC/SerpAPI/Ahrefs) are not configured. Running with WordPress only.[/]")

    # 2モードのエージェントを用意（必要に応じて使い分け）:contentReference[oaicite:8]{index=8}
    chat_agent = build_chat_agent(tools)
    plan_agent = build_plan_agent(tools)

    # セッション（会話記憶）:contentReference[oaicite:9]{index=9}
    session: Optional[SQLiteSession] = None
    if args.session_id:
        session = SQLiteSession(args.session_id, args.session_db or None)

    asyncio.run(
        repl_loop(
            chat_agent, plan_agent, session,
            days=args.days,
            ga4_property_id=args.ga4_property_id.strip(),
            gsc_site_url=args.gsc_site_url.strip(),
            model=args.model,
            max_turns=args.max_turns,
            show_text_deltas=args.show_text_deltas,
        )
    )

if __name__ == "__main__":
    main()
