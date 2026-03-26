"""Test Aviv POS live status endpoint — READ ONLY diagnostic."""

import urllib3
urllib3.disable_warnings()

import json
import requests

STATUS_URL = "https://bi1.aviv-pos.co.il:65010/raw/status/plain"
BASE = "https://bi1.aviv-pos.co.il:8443/avivbi"

login_creds = {"userId": "S33834", "password": "S33834"}

login_attempts = [
    ("a. /user/login",
     lambda: requests.post(f"{BASE}/user/login", json=login_creds, verify=False, timeout=15)),

    ("b. /auth/login",
     lambda: requests.post(f"{BASE}/auth/login", json=login_creds, verify=False, timeout=15)),

    ("c. /login",
     lambda: requests.post(f"{BASE}/login", json=login_creds, verify=False, timeout=15)),

    ("d. /dashboard/query (type=login)",
     lambda: requests.post(f"{BASE}/dashboard/query",
                           json={"type": "login", "userId": "S33834", "password": "S33834"},
                           verify=False, timeout=15)),
]

print("=" * 60)
print("  STEP 1 — Finding login endpoint")
print("=" * 60)

token = None
for name, fn in login_attempts:
    print(f"\n--- {name} ---")
    try:
        resp = fn()
        print(f"  Status: {resp.status_code}")
        print(f"  Headers: {dict(resp.headers)}")
        body = resp.text
        if resp.status_code == 200:
            print(f"  Body (FULL, {len(body)} chars):\n{body}")
            # Try to extract token from response
            try:
                data = resp.json()
                for key in ("token", "authToken", "Authtoken", "authtoken", "auth_token", "access_token"):
                    if key in data:
                        token = data[key]
                        print(f"\n  >>> FOUND TOKEN in key '{key}': {token[:50]}...")
                        break
                if not token and isinstance(data, dict):
                    # Check nested
                    for k, v in data.items():
                        if isinstance(v, str) and len(v) > 20:
                            print(f"  >>> Potential token? key='{k}', value={v[:80]}...")
            except Exception:
                pass
        else:
            print(f"  Body ({len(body)} chars): {body[:300]}")
    except Exception as e:
        print(f"  ERROR: {e}")

print(f"\n\n{'=' * 60}")
print("  STEP 2 — Call status endpoint with token")
print("=" * 60)

if token:
    print(f"\nUsing token: {token[:50]}...")
    try:
        resp = requests.post(STATUS_URL, headers={"Authtoken": token}, verify=False, timeout=15)
        print(f"  Status: {resp.status_code}")
        print(f"  Headers: {dict(resp.headers)}")
        print(f"  Body (FULL, {len(resp.text)} chars):\n{resp.text}")
    except Exception as e:
        print(f"  ERROR: {e}")
else:
    print("\n  No token found from any login attempt.")
    print("  Try checking browser DevTools → Network → look for the request")
    print("  that returns the Authtoken value.")

print("\n--- done ---")
