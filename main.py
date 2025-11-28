#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "openai-agents==0.4.2",
#   "openai-agents-mcp>=0.0.8,<0.1.0",
#   "mcp-agent>=0.2.4",
#   "openai==2.6.1",
#   "httpx>=0.28.1",
#   "pydantic>=2.12.3",
#   "google-analytics-data==0.19.0",
#   "google-api-python-client==2.185.0",
#   "google-auth>=2.42.0",
#   "google-auth-oauthlib>=1.2.2",
# ]
# [tool.uv]
# exclude-newer = "2025-10-30T00:00:00Z"
# ///

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib
import json
import os
import shlex
import sys
import textwrap
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import dotenv
import httpx
from pydantic import BaseModel, Field

from agents import AgentOutputSchema, Runner, function_tool

import dotenv
dotenv.load_dotenv()

# ====== 環境変数 ======
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
WP_BASE_URL = os.getenv("WP_BASE_URL", "")
WP_APP_USER = os.getenv("WP_APP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "")
GSC_OAUTH_CLIENT_JSON = os.getenv("GSC_OAUTH_CLIENT_JSON", "gsc_oauth_client.json")
GSC_TOKEN_JSON = os.getenv("GSC_TOKEN_JSON", "gsc_token.json")
AHREFS_API_KEY = os.getenv("AHREFS_API_KEY", "")

# agents_mcp 0.0.8 expects legacy module paths from older mcp-agent releases.
def _alias_module(old: str, new: str) -> None:
    try:  # pragma: no cover - best effort shim
        importlib.import_module(old)
    except ModuleNotFoundError:
        module = importlib.import_module(new)
        sys.modules[old] = module


_alias_module("mcp_agent.mcp_server_registry", "mcp_agent.mcp.mcp_server_registry")
_alias_module("mcp_agent.context", "mcp_agent.core.context")

from agents_mcp.agent import Agent
from agents.items import (
    MessageOutputItem,
    ReasoningItem,
    ResponseFunctionToolCall,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.memory.sqlite_session import SQLiteSession
from agents.result import RunResultStreaming
from agents.stream_events import AgentUpdatedStreamEvent, RawResponsesStreamEvent, RunItemStreamEvent, StreamEvent
from mcp_agent.config import MCPServerSettings, MCPSettings


dotenv.load_dotenv()

# ====== 環境変数 ======
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
WP_BASE_URL = os.getenv("WP_BASE_URL", "")
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "")
GSC_OAUTH_CLIENT_JSON = os.getenv("GSC_OAUTH_CLIENT_JSON", "gsc_oauth_client.json")
GSC_TOKEN_JSON = os.getenv("GSC_TOKEN_JSON", "gsc_token.json")
AHREFS_API_KEY = os.getenv("AHREFS_API_KEY", "")

# ====== Google クライアント ======
from google.analytics.data_v1beta import BetaAnalyticsDataClient  # type: ignore
from google.analytics.data_v1beta.types import (  # type: ignore
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


# ====== MCP ヘルパ ======
def _parse_key_value_mapping(raw: str) -> Dict[str, str]:
    """Parse JSON or comma-separated key=value pairs into a dict."""
    if not raw:
        return {}
    raw = raw.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    else:
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    mapping: Dict[str, str] = {}
    for chunk in raw.split(","):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            mapping[key] = value
    return mapping


def _default_wp_mcp_http_url(base_url: str) -> str:
    """Fallback URL for WordPress MCP if only WP_BASE_URL is set.

    Assumes WordPress MCP Adapter is installed and the server ID is
    "marketing-ro-server" (see my-mcp-abilities/my-mcp-abilities.php).
    """
    if not base_url:
        return ""
    # Namespace = marketing, route = mcp, server_id = marketing-ro-server
    # => /wp-json/marketing/mcp/marketing-ro-server
    return base_url.rstrip("/") + "/wp-json/marketing/mcp/marketing-ro-server"


def build_wordpress_mcp_settings(
    *,
    transport: str,
    name: str,
    stdio_command: str,
    stdio_args: str,
    stdio_env: str,
    stdio_cwd: str,
    http_url: str,
    http_headers: str,
    http_username: str,
    http_password: str,
    http_bearer: str,
) -> tuple[MCPSettings, str]:
    transport_normalized = transport.strip().lower() or "streamable_http"
    server_name = name.strip() or "wordpress"

    if transport_normalized in {"http", "streamable_http"}:
        url = http_url.strip()
        if not url:
            raise ValueError(
                "WP MCP HTTP transport requires --wp-mcp-http-url or WP_MCP_HTTP_URL."
            )
        headers = _parse_key_value_mapping(http_headers)
        authorization_present = any(k.lower() == "authorization" for k in headers)
        bearer = http_bearer.strip()
        if bearer and not authorization_present:
            headers["Authorization"] = f"Bearer {bearer}"
            authorization_present = True
        username = http_username.strip()
        password = http_password.strip()
        if username and password and not authorization_present:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
            authorization_present = True
        server_settings = MCPServerSettings(
            name=server_name,
            transport="streamable_http",
            url=url,
            headers=headers or None,
        )
        descriptor = f"{server_name} via streamable_http: {url}"
    elif transport_normalized == "stdio":
        executable = stdio_command.strip() or "wp"
        args_raw = stdio_args.strip()
        if not args_raw:
            args_raw = "mcp-adapter serve"
        args = shlex.split(args_raw)
        env_overrides = _parse_key_value_mapping(stdio_env)
        server_settings = MCPServerSettings(
            name=server_name,
            transport="stdio",
            command=executable,
            args=args,
            cwd=stdio_cwd.strip() or None,
            env=env_overrides or None,
        )
        descriptor = f"{server_name} via stdio: {executable} {' '.join(args)}".strip()
    else:
        raise ValueError(
            "Unsupported WP MCP transport. Use 'streamable_http' (or 'http') or 'stdio'."
        )

    settings = MCPSettings(servers={server_name: server_settings})
    return settings, descriptor


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
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=dims,
        metrics=mets,
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=25000,
    )
    if page_paths:
        # pagePath フィルタの拡張は用途に応じて追加する
        pass
    response = client.run_report(request)
    return {
        "dimension_headers": [header.name for header in response.dimension_headers],
        "metric_headers": [header.name for header in response.metric_headers],
        "rows": [
            [dim.value for dim in row.dimension_values] + [metric.value for metric in row.metric_values]
            for row in response.rows
        ],
    }


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
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": 25000,
    }
    result = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    return result or {}


def serpapi_search(q: str, num: int = 10, gl: str = "jp", hl: str = "ja") -> Dict[str, Any]:
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
    with httpx.Client(timeout=40.0) as client:
        resp = client.get("https://serpapi.com/search", params=params)
        resp.raise_for_status()
        return resp.json()


def ahrefs_mcp_site_overview(domain: str) -> Dict[str, Any]:
    if not AHREFS_API_KEY:
        return {"warning": "AHREFS_API_KEY not set. Skipping Ahrefs MCP call."}
    return {"domain": domain, "note": "Use Ahrefs MCP server via MCP tool in production."}


# ====== Agents SDK ツール ======
@function_tool
def tool_ga4_report(property_id: Optional[str], start_date: str, end_date: str) -> Dict[str, Any]:
    """GA4: PV/セッション推移レポート（読み取り）"""
    return ga4_report_pages(property_id or GA4_PROPERTY_ID, start_date, end_date)


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


# ====== エージェント構築 ======
AGENT_INSTRUCTIONS = """
あなたは社内マーケ部門のアナリストAIです。次を厳密に守ってください。
- あなたは「読み取り専用」のツールだけを使います。CMS更新・公開・削除・API書き込み等は一切行いません。
- WordPress MCPサーバー/GA4/GSC/SerpAPI/Ahrefsから得たデータを横断的に解釈し、「改善案（提案）」までを出力してください。
- WordPressの情報はMCPアダプターが公開するツールのみを利用し、直接REST APIを呼び出さないでください。
- 利用可能なツールが制限されている場合は、その範囲で分析し、不足データは「取得できない」旨を明示してください。
- 出力は指定の構造（JSON）で返します。説明の冗長化は避け、要点と根拠を簡潔に。
- 推奨KPI例：PV、セッション、CTR、平均掲載順位、流入チャネル別比率。
- 日本語で回答してください。
"""


def build_agent(enabled_tools: List[Any], mcp_server_names: List[str]) -> Agent:
    return Agent(
        name="Marketing Analysis Agent",
        instructions=AGENT_INSTRUCTIONS,
        tools=enabled_tools,
        mcp_servers=mcp_server_names,
        output_type=AgentOutputSchema(ImprovementPlan, strict_json_schema=True),
    )


# ====== ストリーミング表示ヘルパ ======
class StreamPrinter:
    def __init__(self) -> None:
        self._progress_active = False
        self._last_tick = time.monotonic()
        self._tick_interval = 0.25
        self._tool_call_names: Dict[str, str] = {}
        self.last_message_text: str = ""

    async def consume(self, result: RunResultStreaming) -> None:
        try:
            async for event in result.stream_events():
                self._handle_event(event)
        finally:
            self._end_progress()

    def _handle_event(self, event: StreamEvent) -> None:
        if isinstance(event, AgentUpdatedStreamEvent):
            self._end_progress()
            print(f"\n[agent] {event.new_agent.name}")
        elif isinstance(event, RawResponsesStreamEvent):
            self._handle_raw_event(event)
        elif isinstance(event, RunItemStreamEvent):
            self._handle_run_item_event(event)

    def _handle_raw_event(self, event: RawResponsesStreamEvent) -> None:
        event_name = event.data.__class__.__name__
        if event_name in {"ResponseCreatedEvent", "ResponseOutputItemAddedEvent"}:
            self._start_progress("model")
        elif event_name == "ResponseTextDeltaEvent":
            self._tick_progress()
        elif event_name in {"ResponseOutputItemDoneEvent", "ResponseCompletedEvent"}:
            self._end_progress()

    def _handle_run_item_event(self, event: RunItemStreamEvent) -> None:
        name = event.name
        item = event.item
        if name == "tool_called" and isinstance(item, ToolCallItem):
            raw = item.raw_item
            if isinstance(raw, ResponseFunctionToolCall):
                self._tool_call_names[raw.call_id] = raw.name
                args_display = _format_json_snippet(raw.arguments)
                print(f"\n[tool] ↘ {raw.name} args={args_display}")
            else:
                print("\n[tool] ↘ function call")
        elif name == "tool_output" and isinstance(item, ToolCallOutputItem):
            call_id = getattr(item.raw_item, "call_id", None)
            if call_id is None and isinstance(item.raw_item, dict):
                call_id = item.raw_item.get("call_id")
            tool_name = self._tool_call_names.get(call_id, "tool")
            summary = summarize_tool_output(item.output)
            print(f"[tool] ↗ {tool_name} -> {summary}")
        elif name == "reasoning_item_created" and isinstance(item, ReasoningItem):
            text = _extract_reasoning_summary(item)
            if text:
                print(f"\n[reasoning] {text}")
        elif name == "message_output_created" and isinstance(item, MessageOutputItem):
            self.last_message_text = _extract_message_text(item)

    def _start_progress(self, label: str) -> None:
        if not self._progress_active:
            print(f"\n[{label}] streaming...", end="", flush=True)
            self._progress_active = True
            self._last_tick = time.monotonic()

    def _tick_progress(self) -> None:
        if not self._progress_active:
            self._start_progress("model")
            return
        now = time.monotonic()
        if now - self._last_tick >= self._tick_interval:
            print(".", end="", flush=True)
            self._last_tick = now

    def _end_progress(self) -> None:
        if self._progress_active:
            print()
            self._progress_active = False


# ====== ユーティリティ ======
def _date_span(days: int) -> tuple[str, str]:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days)
    return (start.isoformat(), end.isoformat())


def _compose_context_block(
    query_hint: str,
    start: str,
    end: str,
    ga4_property_id: str,
    gsc_site_url: str,
    enabled_sources: Iterable[str],
    wordpress_mcp_descriptor: str,
) -> str:
    lines = [
        query_hint,
        f"- 解析期間: {start}〜{end}",
        f"- GA4 property: {ga4_property_id or '(未設定)'}",
        f"- GSC site: {gsc_site_url or '(未設定)'}",
        f"- WordPress: {WP_BASE_URL or '(未設定)'}",
        f"- WordPress MCP: {wordpress_mcp_descriptor}",
        f"- 使用するデータソース: {', '.join(enabled_sources)}",
        "必要に応じてツールを呼び出し、記事動向の要点と改善案を提案してください。",
    ]
    return "\n".join(lines)


def _truncate(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_json_snippet(raw_json: str) -> str:
    try:
        parsed = json.loads(raw_json)
        snippet = json.dumps(parsed, ensure_ascii=False)
    except Exception:
        snippet = raw_json
    return _truncate(snippet, 160)


def summarize_tool_output(output: Any) -> str:
    if isinstance(output, dict):
        if "error" in output:
            return f"error: {output['error']}"
        if "warning" in output:
            return f"warning: {output['warning']}"
        return _truncate(json.dumps(output, ensure_ascii=False))
    if isinstance(output, list):
        sample_titles: List[str] = []
        for entry in output[:2]:
            if isinstance(entry, dict):
                title = entry.get("title")
                if isinstance(title, dict):
                    title = title.get("rendered") or title.get("raw")
                if isinstance(title, str) and title:
                    sample_titles.append(title.strip())
        if sample_titles:
            joined = "; ".join(_truncate(t, 40) for t in sample_titles)
            return f"{len(output)} items (例: {joined})"
        return f"{len(output)} items"
    return _truncate(str(output))


def _extract_message_text(item: MessageOutputItem) -> str:
    parts: List[str] = []
    for content in item.raw_item.content:
        text = getattr(content, "text", "")
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _extract_reasoning_summary(item: ReasoningItem) -> str:
    summaries = getattr(item.raw_item, "summary", None)
    if not summaries:
        return ""
    texts: List[str] = []
    for summary in summaries:
        text = getattr(summary, "text", None)
        if text:
            texts.append(text)
    joined = " ".join(texts).strip()
    return _truncate(joined, 100)


def _print_plan(plan: ImprovementPlan) -> None:
    wrapper = textwrap.TextWrapper(width=100, subsequent_indent="  ")
    print("\n[plan] 概要")
    print(wrapper.fill(plan.summary))

    if plan.metrics_snapshot:
        print("[plan] 指標")
        for metric in plan.metrics_snapshot:
            label = f"{metric.label}: {metric.value}"
            print("  - " + wrapper.fill(label).lstrip())

    if plan.prioritized_actions:
        print("[plan] 優先施策")
        for action in plan.prioritized_actions:
            print(f"  - {action.title} ({action.effort})")
            print("    根拠: " + wrapper.fill(action.rationale).lstrip())
            print("    期待効果: " + wrapper.fill(action.expected_impact).lstrip())
            if action.dependencies:
                deps = ", ".join(action.dependencies)
                print("    依存: " + wrapper.fill(deps).lstrip())
            if action.kpis:
                kpis = ", ".join(action.kpis)
                print("    KPI: " + wrapper.fill(kpis).lstrip())

    if plan.cautions:
        print("[plan] 注意点")
        for caution in plan.cautions:
            print("  - " + wrapper.fill(caution).lstrip())

    if plan.sources:
        print("[plan] 参照元")
        for src in plan.sources:
            print("  - " + wrapper.fill(src).lstrip())


def _extract_plan(result: RunResultStreaming) -> Optional[ImprovementPlan]:
    try:
        return result.final_output_as(ImprovementPlan, raise_if_incorrect_type=True)
    except TypeError:
        final_output = result.final_output
        if isinstance(final_output, ImprovementPlan):
            return final_output
    return None


# ====== 対話ループ ======
async def chat_loop(
    agent: Agent,
    session: SQLiteSession,
    context_block: str,
    initial_query: Optional[str],
    max_turns: int,
    run_context: Optional[SimpleNamespace],
) -> None:
    printer = StreamPrinter()
    pending = initial_query.strip() if initial_query else None

    print("対話モードです。/exit で終了、/help でコマンド一覧を表示します。")
    print("\n--- コンテキスト ---")
    for line in context_block.splitlines():
        print(line)
    print("--------------------")

    while True:
        if pending is not None:
            user_input = pending
            pending = None
        else:
            try:
                user_input = await asyncio.to_thread(input, "you> ")
            except EOFError:
                print()
                break
            user_input = user_input.strip()

        if not user_input:
            continue

        if user_input in {"exit", "quit", "/exit", "/quit"}:
            break

        if user_input == "/help":
            print("利用可能コマンド: /exit, /quit, /help")
            continue

        composed_prompt = f"{user_input}\n{context_block}"
        print(f"\n[you] {user_input}")

        try:
            result = Runner.run_streamed(
                agent,
                input=composed_prompt,
                context=run_context,
                session=session,
                max_turns=max_turns,
            )
        except Exception as exc:
            print(f"[error] Failed to start agent run: {exc}")
            continue

        try:
            await printer.consume(result)
        except Exception as exc:
            print(f"[error] Agent run failed: {exc}")
            continue

        plan = _extract_plan(result)
        if plan:
            _print_plan(plan)
        else:
            fallback = printer.last_message_text or str(result.final_output)
            if fallback:
                wrapper = textwrap.TextWrapper(width=100, subsequent_indent="  ")
                print("\n[assistant]")
                for paragraph in fallback.split("\n"):
                    if paragraph.strip():
                        print(wrapper.fill(paragraph.strip()))
            else:
                print("\n[assistant] 応答は空でした。")


# ====== CLI エントリポイント ======
def main() -> None:
    parser = argparse.ArgumentParser(description="Marketing Analysis Agent CLI (interactive)")
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="起動時に送信する最初の質問（省略時は対話で入力）",
    )
    parser.add_argument("--ga4-property-id", type=str, default=GA4_PROPERTY_ID)
    parser.add_argument("--gsc-site-url", type=str, default=os.getenv("GSC_SITE_URL", ""))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--session-id",
        type=str,
        default=f"cli-{uuid.uuid4()}",
        help="会話を識別するセッションID",
    )
    parser.add_argument(
        "--session-db",
        type=str,
        default=":memory:",
        help="セッション履歴を保存するSQLiteファイル（:memory: は揮発）",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="1プロンプトあたりの最大ターン数（デフォルト: 10）",
    )
    parser.add_argument(
        "--wp-mcp-transport",
        type=str,
        default=os.getenv("WP_MCP_TRANSPORT", "streamable_http"),
        help="WordPress MCPアダプターへの接続方式（streamable_http または stdio）。",
    )
    parser.add_argument(
        "--wp-mcp-stdio-command",
        type=str,
        default=os.getenv("WP_MCP_STDIO_COMMAND", "wp"),
        help="transport=stdio 時に実行するコマンド（既定: wp）。",
    )
    parser.add_argument(
        "--wp-mcp-stdio-args",
        type=str,
        default=os.getenv("WP_MCP_STDIO_ARGS", "mcp-adapter serve"),
        help="transport=stdio 時の引数（space区切り）。既定: 'mcp-adapter serve'。",
    )
    parser.add_argument(
        "--wp-mcp-stdio-env",
        type=str,
        default=os.getenv("WP_MCP_STDIO_ENV", ""),
        help="transport=stdio 時に渡す環境変数。JSON または key=value,key=value 形式。",
    )
    parser.add_argument(
        "--wp-mcp-stdio-cwd",
        type=str,
        default=os.getenv("WP_MCP_STDIO_CWD", ""),
        help="transport=stdio 時のカレントディレクトリ。未設定なら親プロセスと同じ。",
    )
    parser.add_argument(
        "--wp-mcp-http-url",
        type=str,
        default=os.getenv("WP_MCP_HTTP_URL", ""),
        help="transport=streamable_http 時の MCP エンドポイントURL。",
    )
    parser.add_argument(
        "--wp-mcp-http-headers",
        type=str,
        default=os.getenv("WP_MCP_HTTP_HEADERS", ""),
        help="追加HTTPヘッダー。JSON または key=value,key=value 形式。",
    )
    parser.add_argument(
        "--wp-mcp-http-username",
        type=str,
        default=os.getenv("WP_MCP_HTTP_USERNAME", ""),
        help="Basic認証ユーザー名（指定時は Authorization: Basic を自動設定）。",
    )
    parser.add_argument(
        "--wp-mcp-http-password",
        type=str,
        default=os.getenv("WP_MCP_HTTP_PASSWORD", ""),
        help="Basic認証パスワード（指定時は Authorization: Basic を自動設定）。",
    )
    parser.add_argument(
        "--wp-mcp-http-bearer",
        type=str,
        default=os.getenv("WP_MCP_HTTP_BEARER", ""),
        help="Bearer トークン（指定時は Authorization: Bearer を自動設定）。",
    )
    parser.add_argument(
        "--wp-mcp-name",
        type=str,
        default=os.getenv("WP_MCP_NAME", "wordpress"),
        help="WordPress MCP サーバー名（OpenAI Agents SDK 上の識別子）。",
    )
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY is not set.")

    start, end = _date_span(args.days)

    # HTTPモードで URL 未指定の場合、WP_BASE_URL から既定パスを自動補完
    if args.wp_mcp_transport.lower() in {"http", "streamable_http"} and not args.wp_mcp_http_url:
        args.wp_mcp_http_url = _default_wp_mcp_http_url(WP_BASE_URL)

    # 認証情報が未指定なら WP_APP_USER / WP_APP_PASSWORD を流用（Application Password想定）
    if not args.wp_mcp_http_username and WP_APP_USER:
        args.wp_mcp_http_username = WP_APP_USER
    if not args.wp_mcp_http_password and WP_APP_PASSWORD:
        args.wp_mcp_http_password = WP_APP_PASSWORD

    try:
        wordpress_mcp_settings, wordpress_descriptor = build_wordpress_mcp_settings(
            transport=args.wp_mcp_transport,
            name=args.wp_mcp_name,
            stdio_command=args.wp_mcp_stdio_command,
            stdio_args=args.wp_mcp_stdio_args,
            stdio_env=args.wp_mcp_stdio_env,
            stdio_cwd=args.wp_mcp_stdio_cwd,
            http_url=args.wp_mcp_http_url,
            http_headers=args.wp_mcp_http_headers,
            http_username=args.wp_mcp_http_username,
            http_password=args.wp_mcp_http_password,
            http_bearer=args.wp_mcp_http_bearer,
        )
    except ValueError as exc:
        raise SystemExit(f"WordPress MCP configuration error: {exc}")

    mcp_server_names = [args.wp_mcp_name.strip() or "wordpress"]

    # ツール構成
    enabled_tools: List[Any] = []
    enabled_sources: List[str] = ["WordPress MCP"]

    if args.ga4_property_id:
        enabled_tools.append(tool_ga4_report)
        enabled_sources.append("GA4")

    if args.gsc_site_url:
        enabled_tools.append(tool_gsc_query)
        enabled_sources.append("GSC")

    if SERPAPI_API_KEY:
        enabled_tools.append(tool_serpapi)
        enabled_sources.append("SerpAPI")

    if AHREFS_API_KEY:
        enabled_tools.append(tool_ahrefs_site_overview)
        enabled_sources.append("Ahrefs MCP")

    if not enabled_tools:
        print("INFO: Optional connectors are not configured. WordPress MCP のみ利用します。", file=sys.stderr)

    agent = build_agent(enabled_tools, mcp_server_names)
    session = SQLiteSession(session_id=args.session_id, db_path=args.session_db)
    run_context = SimpleNamespace(mcp_config=wordpress_mcp_settings)

    context_block = _compose_context_block(
        query_hint="以下の要望に応えてください。",
        start=start,
        end=end,
        ga4_property_id=args.ga4_property_id.strip(),
        gsc_site_url=args.gsc_site_url.strip(),
        enabled_sources=enabled_sources,
        wordpress_mcp_descriptor=wordpress_descriptor,
    )

    initial_query = args.query.strip() if args.query else None

    try:
        asyncio.run(
            chat_loop(
                agent=agent,
                session=session,
                context_block=context_block,
                initial_query=initial_query,
                max_turns=args.max_turns,
                run_context=run_context,
            )
        )
    except KeyboardInterrupt:
        print("\n終了します。")


if __name__ == "__main__":
    main()
