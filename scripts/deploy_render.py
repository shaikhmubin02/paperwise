"""Create or update the Paperwise web service on Render (free plan) via the Render API.

Usage:  python scripts/deploy_render.py
Reads RENDER_API_KEY, NEBIUS_API_KEY and TAVILY_API_KEY from .env. Keys are sent to Render as environment
variables of the service; they are never committed.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
API = "https://api.render.com/v1"
REPO = os.getenv("RENDER_REPO", "https://github.com/shaikhmubin02/paperwise")
NAME = os.getenv("RENDER_SERVICE_NAME", "paperwise")


def main() -> None:
    key = os.getenv("RENDER_API_KEY") or sys.exit("RENDER_API_KEY missing from .env")
    h = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    c = httpx.Client(base_url=API, headers=h, timeout=60)
    owner = c.get("/owners", params={"limit": 20}).raise_for_status().json()[0]["owner"]
    env = [{"key": k, "value": os.environ[k]} for k in ("NEBIUS_API_KEY", "TAVILY_API_KEY") if os.getenv(k)]
    env.append({"key": "DATA_DIR", "value": "/tmp/paperwise"})

    existing = [s["service"] for s in c.get("/services", params={"name": NAME, "limit": 20}).json()]
    if existing:
        svc = existing[0]
        c.put(f"/services/{svc['id']}/env-vars", json=env).raise_for_status()
        c.post(f"/services/{svc['id']}/deploys", json={}).raise_for_status()
        print(f"Updated existing service {svc['id']}; redeploy triggered")
    else:
        body = {
            "type": "web_service", "name": NAME, "ownerId": owner["id"], "repo": REPO, "branch": "main",
            "autoDeploy": "yes", "envVars": env,
            "serviceDetails": {"runtime": "docker", "plan": "free", "region": "oregon",
                               "healthCheckPath": "/api/samples",
                               "envSpecificDetails": {"dockerfilePath": "./Dockerfile", "dockerContext": "."}},
        }
        r = c.post("/services", json=body)
        if r.status_code >= 400:
            sys.exit(f"Render API error {r.status_code}: {r.text}")
        svc = r.json()["service"]
        print(f"Created service {svc['id']}")
    url = svc.get("serviceDetails", {}).get("url")
    print(f"Dashboard: {svc.get('dashboardUrl')}\nURL:       {url}")
    # Follow the first deploy until it is live or fails.
    for _ in range(90):
        deploys = c.get(f"/services/{svc['id']}/deploys", params={"limit": 1}).json()
        status = deploys[0]["deploy"]["status"] if deploys else "pending"
        print(f"  deploy status: {status}", flush=True)
        if status in ("live", "build_failed", "update_failed", "canceled", "deactivated"):
            break
        time.sleep(20)


if __name__ == "__main__":
    main()
