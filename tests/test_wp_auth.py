# test_wp_auth.py
import os, sys, requests
from requests.auth import HTTPBasicAuth

import dotenv
dotenv.load_dotenv()

BASE = os.getenv("WP_BASE_URL", "https://hitocareer.com").rstrip("/")
USER = os.getenv("WP_APP_USER")
PWD  = os.getenv("WP_APP_PASSWORD")

if not USER or not PWD:
    print("WP_APP_USER / WP_APP_PASSWORD を設定してください"); sys.exit(1)

auth = HTTPBasicAuth(USER, PWD)

def get(url, params=None):
    r = requests.get(url, params=params, auth=auth, timeout=20)
    full_url = r.url  # resolved URL with query for logging
    print("GET", full_url, r.status_code); print(r.text[:2000])
    return r

def post(url, json):
    r = requests.post(url, json=json, auth=auth, timeout=20)
    print("POST", url, r.status_code); print(r.text[:2000])
    return r

# 1) 認証確認（通ればユーザー情報JSONが返る）
get(f"{BASE}/wp-json/wp/v2/users/me")

# 2) Abilities ルート確認（Abilities API は wp/v2 配下）
get(f"{BASE}/wp-json/wp/v2/abilities")
get(f"{BASE}/wp-json/wp/v2/abilities/marketing/get-posts")

# 3) abilities 実行 (GET; input はクエリで object 形式)
get(
    f"{BASE}/wp-json/wp/v2/abilities/marketing/get-posts/run",
    params={"input[number]": 5, "input[status]": "publish"},
)

# 4) MCP HttpTransport ハンドシェイク → tools/list
def jsonrpc(url, payload, session_id=None):
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    r = requests.post(url, json=payload, auth=auth, timeout=20, headers=headers)
    print("POST", url, r.status_code, "sid", session_id); print(r.text[:2000]); return r

mcp_url = f"{BASE}/wp-json/mcp/mcp-adapter-default-server"
init = jsonrpc(mcp_url, {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}})
sid = init.headers.get("Mcp-Session-Id")
if not sid:
    print("initialize で Session ID が取得できませんでした"); sys.exit(1)

jsonrpc(
    mcp_url,
    {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}},
    session_id=sid,
)
