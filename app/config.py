"""Runtime settings and Nemotron model discovery on Nebius Token Factory."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("paperwise")

BASE_DIR = Path(__file__).resolve().parent.parent

# Preference order per role. Discovery picks the first ID the account can actually see.
REASONING_PREFS = [
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/Nemotron-3-Ultra-550b-a55b",
    "nvidia/llama-3_3-nemotron-super-49b-v1_5",
    "nvidia/llama-3_1-nemotron-ultra-253b-v1",
]
FAST_PREFS = [
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "nvidia/nemotron-3-nano-30b-a3b",
    "nvidia/Nemotron-3_5-Lightning",
    "nvidia/nemotron-3-super-120b-a12b",
]
VISION_PREFS = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "nvidia/Nemotron-3-Nano-Omni",
    "nvidia/nemotron-nano-12b-v2-vl",
    # Token Factory has no Nemotron vision model today; fall back to open vision models for transcription only.
    "openbmb/MiniCPM-V-4_5",
    "google/gemma-3-27b-it",
]


@dataclass
class Settings:
    nebius_api_key: str = field(default_factory=lambda: os.getenv("NEBIUS_API_KEY", ""))
    nebius_base_url: str = field(
        default_factory=lambda: os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
    )
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    model_reasoning: str = field(default_factory=lambda: os.getenv("MODEL_REASONING", ""))
    model_fast: str = field(default_factory=lambda: os.getenv("MODEL_FAST", ""))
    model_vision: str = field(default_factory=lambda: os.getenv("MODEL_VISION", ""))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))))
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "12")))
    max_docs_per_workspace: int = field(default_factory=lambda: int(os.getenv("MAX_DOCS_PER_WORKSPACE", "40")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paperwise.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"


def _pick(available: list[str], prefs: list[str], must: tuple[str, ...], avoid: tuple[str, ...] = ()) -> str:
    lower = {m.lower(): m for m in available}
    for p in prefs:
        if p.lower() in lower:
            return lower[p.lower()]
    for m in available:
        ml = m.lower()
        if all(k in ml for k in must) and not any(a in ml for a in avoid):
            return m
    return ""


def resolve_models(settings: Settings, available: list[str]) -> dict[str, str]:
    """Map roles -> model IDs. Explicit env vars win; otherwise discover from /v1/models."""
    reasoning = settings.model_reasoning or _pick(available, REASONING_PREFS, ("nemotron", "super")) or _pick(
        available, [], ("nemotron",), ("omni", "vl", "embed", "guard", "rerank")
    )
    fast = settings.model_fast or _pick(available, FAST_PREFS, ("nemotron", "nano"), ("omni", "vl")) or reasoning
    vision = settings.model_vision or _pick(available, VISION_PREFS, ("nemotron", "omni")) or _pick(
        available, [], ("nemotron", "vl")
    )
    if not reasoning:
        # Fall back to documented ID so errors are explicit rather than silent.
        reasoning = REASONING_PREFS[0]
        fast = fast or reasoning
    return {"reasoning": reasoning, "fast": fast, "vision": vision}


settings = Settings()
