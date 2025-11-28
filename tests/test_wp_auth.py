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

# 2) Abilities ルート確認
get(f"{BASE}/wp-json/wp-abilities/v1")
get(f"{BASE}/wp-json/wp-abilities/v1/abilities")

# 3) 実行（Abilities API の正式ルート: /abilities/{name}/run）
# GET の場合、input はクエリで object 形式にする必要がある（例: input[number]=5）
get(
    f"{BASE}/wp-json/wp-abilities/v1/abilities/marketing/get-posts/run",
    params={"input[number]": 5, "input[status]": "publish"},
)
