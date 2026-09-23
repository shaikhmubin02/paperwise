"""Thin wrapper over the OpenAI-compatible Nebius Token Factory API."""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import threading
import time
from typing import Any

from .config import Settings, resolve_models

log = logging.getLogger("paperwise")

THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def clean_text(text: str | None) -> str:
    text = THINK_RE.sub("", text or "")
    # Unterminated think block (model ran out of tokens mid-thought): drop everything before </think> if present.
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a model response, tolerating fences and chatter."""
    text = FENCE_RE.sub("", clean_text(text)).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Take the LARGEST parseable object: if the outer object is malformed we must not silently return a
    # nested fragment (e.g. just the "sender" sub-object) as if it were the whole answer.
    decoder = json.JSONDecoder()
    best, best_len = None, 0
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, end = decoder.raw_decode(text[i:])
            except json.JSONDecodeError:
                continue
            if end > best_len:
                best, best_len = obj, end
    if best is None:
        raise ValueError("No JSON found in model response")
    return best


def _json_problem(text: str, required: tuple[str, ...]) -> str | None:
    try:
        obj = extract_json(text)
    except ValueError:
        return "not valid JSON"
    if required:
        missing = [k for k in required if not isinstance(obj, dict) or k not in obj]
        if missing:
            return f"missing keys: {', '.join(missing)}"
    return None


def image_to_data_url(data: bytes, max_side: int = 1800) -> str:
    """Downscale large photos (phone cameras) to keep vision token counts sane."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class LLM:
    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=settings.nebius_base_url,
                api_key=settings.nebius_api_key or "missing-key",
                timeout=180,
                max_retries=2,
            )
        self.client = client
        self._models: dict[str, str] | None = None
        self._lock = threading.Lock()
        self.tools_supported: bool | None = None

    # ---- model discovery -------------------------------------------------
    @property
    def models(self) -> dict[str, str]:
        with self._lock:
            if self._models is None:
                available: list[str] = []
                try:
                    available = [m.id for m in self.client.models.list().data]
                except Exception as e:  # noqa: BLE001 - discovery is best effort
                    log.warning("Model discovery failed (%s); using configured/default IDs", e)
                self._models = resolve_models(self.settings, available)
                log.info("Resolved models: %s", self._models)
            return self._models

    def model(self, role: str) -> str:
        return self.models.get(role) or self.models["reasoning"]

    # ---- calls -----------------------------------------------------------
    def complete(self, role: str, messages: list[dict], *, temperature: float = 0.2, max_tokens: int = 4096,
                 **kw) -> tuple[str, dict]:
        model = self.model(role)
        t0 = time.time()
        resp = self.client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, **kw
        )
        msg = resp.choices[0].message
        meta = {"model": model, "ms": int((time.time() - t0) * 1000)}
        usage = getattr(resp, "usage", None)
        if usage is not None:
            meta["tokens"] = getattr(usage, "total_tokens", None)
        return clean_text(msg.content), meta

    def complete_json(self, role: str, messages: list[dict], *, required: tuple[str, ...] = (),
                      **kw) -> tuple[Any, dict]:
        """Call the model for JSON; on invalid JSON or missing required keys, ask it once to repair."""
        text, meta = self.complete(role, messages, **kw)
        problem = _json_problem(text, required)
        if problem is None:
            return extract_json(text), meta
        log.warning("Model JSON needs repair (%s)", problem)
        repair = messages + [
            {"role": "assistant", "content": text[:12000]},
            {"role": "user", "content": f"That response was unusable ({problem}). Reply with ONLY one complete, "
                                        "valid JSON object matching the requested schema, no prose."},
        ]
        text, meta2 = self.complete(role, repair, **kw)
        meta["ms"] += meta2["ms"]
        return extract_json(text), meta

    def transcribe_image(self, data: bytes, prompt: str) -> tuple[str, dict]:
        if not self.models.get("vision"):
            raise RuntimeError(
                "No NVIDIA vision model is available on this account. Upload a PDF or paste the text instead."
            )
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_to_data_url(data)}},
            ],
        }]
        return self.complete("vision", messages, temperature=0.0, max_tokens=6000)

    def raw(self, role: str, messages: list[dict], **kw):
        """Return the raw message object (used by the tool-calling agent)."""
        model = self.model(role)
        resp = self.client.chat.completions.create(model=model, messages=messages, **kw)
        return resp.choices[0].message, model
