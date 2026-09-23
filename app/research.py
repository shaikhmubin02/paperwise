"""Live web research via Tavily — grounds rights and scam checks in real, citable sources."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("paperwise")

TAVILY_URL = "https://api.tavily.com/search"

# Bias rights research toward authoritative sources (government, legal aid, consumer protection).
TRUSTED_DOMAINS = [
    "consumerfinance.gov", "ftc.gov", "consumer.ftc.gov", "irs.gov", "usa.gov", "hud.gov", "ssa.gov",
    "medicare.gov", "cms.gov", "uscis.gov", "lawhelp.org", "nolo.com", "justia.com", "law.cornell.edu",
    "gov.uk", "citizensadvice.org.uk", "canada.ca", "europa.eu", "legalaid.org",
]


class Researcher:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=30)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, *, max_results: int = 5, trusted: bool = False, depth: str = "advanced") -> dict:
        if not self.enabled:
            return {"query": query, "results": [], "answer": None, "disabled": True}
        payload: dict = {
            "query": query[:400],
            "search_depth": depth,
            "max_results": max_results,
            "include_answer": "basic",
        }
        if trusted:
            payload["include_domains"] = TRUSTED_DOMAINS
        data = None
        for attempt in range(2):  # one retry for transient network/DNS errors
            try:
                r = self.client.post(TAVILY_URL, json=payload, headers={"Authorization": f"Bearer {self.api_key}"})
                r.raise_for_status()
                data = r.json()
                break
            except httpx.HTTPError as e:
                log.warning("Tavily search failed (attempt %d): %s", attempt + 1, e)
                error = str(e)
        if data is None:
            return {"query": query, "results": [], "answer": None, "error": error}
        # A trusted-only search can come back empty for niche topics; widen once.
        if trusted and not data.get("results"):
            return self.search(query, max_results=max_results, trusted=False, depth=depth)
        return {
            "query": query,
            "answer": data.get("answer"),
            "results": [
                {"title": x.get("title", ""), "url": x.get("url", ""), "content": (x.get("content") or "")[:1200]}
                for x in data.get("results", [])
            ],
        }
