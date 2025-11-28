import os, sys, requests
from requests.auth import HTTPBasicAuth
import dotenv

dotenv.load_dotenv()

BASE = os.getenv("WP_BASE_URL", "https://hitocareer.com").rstrip("/")
USER = os.getenv("WP_APP_USER")
PWD  = os.getenv("WP_APP_PASSWORD")

if not USER or not PWD:
    print("WP_APP_USER / WP_APP_PASSWORD を設定してください")
    sys.exit(1)

auth = HTTPBasicAuth(USER, PWD)

def get(url, params=None):
    r = requests.get(url, params=params, auth=auth, timeout=20)
    print(f"GET {r.url} [{r.status_code}]")
    if r.status_code != 200:
        print(r.text[:500])
    return r

def jsonrpc(url, payload, session_id=None):
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    r = requests.post(url, json=payload, auth=auth, timeout=20, headers=headers)
    print(f"POST {url} [{r.status_code}] sid={session_id}")
    if r.status_code != 200:
        print(r.text[:500])
    return r

print("--- 1. ユーザー認証確認 ---")
get(f"{BASE}/wp-json/wp/v2/users/me")

print("\n--- 2. カスタム MCP サーバーへの接続確認 ---")
# 重要: プラグインで定義されたサーバーID 'marketing-ro-server' を指定
# Namespace 'marketing' + route 'mcp' + server_id
mcp_url = "https://hitocareer.com/wp-json/mcp/mcp-adapter-default-server"

# Initialize
init_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "1.0"}
    }
}
init = jsonrpc(mcp_url, init_payload)

if init.status_code == 200:
    sid = init.headers.get("Mcp-Session-Id")
    print(f"Session ID 取得成功: {sid}")
    
    print("\n--- 3. ツール一覧の取得 (tools/list) ---")
    list_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }
    res = jsonrpc(mcp_url, list_payload, session_id=sid)
    print(res.text[:3000]) # 結果を表示
else:
    print("Initialize 失敗。プラグインが有効化されているか、URLが正しいか確認してください。")