import asyncio
import base64
import json
import logging
import os
import random
import threading
import httpx
from typing import List, Dict, Any, Optional
from backend.app.services.ai.base import LLMProvider, EmbeddingProvider
from backend.app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingUnavailable(RuntimeError):
    """Gemini could not produce a real embedding (and the local-substitute fallback is switched off)."""


# Old behaviour: any embedding failure silently returned a local hash vector, which was then stored in the same
# column as real Gemini vectors (and cached for searches), quietly corrupting dedup and ranking. Off by default.
ALLOW_LOCAL_EMBEDDING_FALLBACK: bool = os.getenv("EMBEDDING_ALLOW_LOCAL_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on")
EMBEDDING_RETRY_PASSES: int = int(os.getenv("EMBEDDING_RETRY_PASSES", "1"))
EMBEDDING_RETRY_DELAY_S: float = float(os.getenv("EMBEDDING_RETRY_DELAY_S", "4"))


# --------------------------------------------------------------------- shared HTTP client
# Every Gemini call used to open (and TLS-handshake) its own httpx client. One long-lived client per
# event loop keeps connections alive and pooled, which matters when a deck fires hundreds of calls.
_CLIENT_STATE: Dict[str, Any] = {"entry": None}


def get_shared_client() -> httpx.AsyncClient:
    """The pooled client for the running event loop (a new one is made if the loop changed/closed)."""
    loop = asyncio.get_running_loop()
    entry = _CLIENT_STATE.get("entry")
    if entry is not None and entry[0] is loop and not loop.is_closed() and not entry[1].is_closed:
        return entry[1]
    client = httpx.AsyncClient(
        timeout=90.0,
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32, keepalive_expiry=30.0),
    )
    _CLIENT_STATE["entry"] = (loop, client)
    return client


class _TimeoutView:
    """The pooled client with a per-call timeout applied to every request made through it."""
    def __init__(self, client: httpx.AsyncClient, timeout: float):
        self._client = client
        self._timeout = timeout

    async def post(self, url, *, json=None, headers=None, **kw):
        kw.setdefault("timeout", self._timeout)
        return await self._client.post(url, json=json, headers=headers, **kw)


class _shared_client:
    """`async with _shared_client(60.0) as client:` hands out the pooled client and never closes it."""
    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout

    async def __aenter__(self) -> _TimeoutView:
        return _TimeoutView(get_shared_client(), self._timeout)

    async def __aexit__(self, *exc) -> bool:
        return False


# --------------------------------------------------------------------- token usage tracking
# Running totals per call type so cost is visible: {label: {"calls", "in", "out", "think"}}.
_USAGE_TOTALS: Dict[str, Dict[str, int]] = {}
_USAGE_LOCK = threading.Lock()


def record_usage(label: str, usage: Dict[str, int], model: Optional[str] = None) -> None:
    with _USAGE_LOCK:
        t = _USAGE_TOTALS.setdefault(label, {"calls": 0, "in": 0, "out": 0, "think": 0, "cached": 0})
        t["calls"] += 1
        t["in"] += int(usage.get("in", 0) or 0)
        t["out"] += int(usage.get("out", 0) or 0)
        t["think"] += int(usage.get("think", 0) or 0)
        t["cached"] = t.get("cached", 0) + int(usage.get("cached", 0) or 0)

    # Wire to Cost Ledger and active CostScope (Cost Tracker Phase 2)
    try:
        from backend.app.services.costs.ledger import get_current_cost_scope, record_unattributed_call
        scope = get_current_cost_scope()
        op_map = {
            "vision": "ingest_vision",
            "tagging": "ingest_tagging",
            "embedding": "ingest_embedding",
            "multimodal": "ingest_vision",
        }
        op = op_map.get(label, f"ingest_{label}")
        m = model or "gemini-3.5-flash-lite"
        if scope is not None:
            scope.add_gemini_usage(
                operation=op,
                model=m,
                tokens_in=int(usage.get("in", 0) or 0),
                tokens_out=int(usage.get("out", 0) or 0),
                thinking_tokens=int(usage.get("think", 0) or 0),
                cached_tokens=int(usage.get("cached", 0) or 0),
                calls=1,
            )
        else:
            record_unattributed_call(
                category="gemini",
                operation=op,
                model=m,
                units={
                    "tokens_in": int(usage.get("in", 0) or 0),
                    "tokens_out": int(usage.get("out", 0) or 0),
                    "thinking_tokens": int(usage.get("think", 0) or 0),
                    "cached_tokens": int(usage.get("cached", 0) or 0),
                    "calls": 1,
                },
            )
    except Exception as e:
        logger.debug("Could not record usage in cost ledger: %s", e)


def usage_snapshot(reset: bool = False) -> Dict[str, Dict[str, int]]:
    """Copy of the per-label totals since the process started (or since the last reset)."""
    with _USAGE_LOCK:
        snap = {k: dict(v) for k, v in _USAGE_TOTALS.items()}
        if reset:
            _USAGE_TOTALS.clear()
        return snap


def _usage_from_response(data: Dict[str, Any]) -> Dict[str, int]:
    um = data.get("usageMetadata", {}) or {}
    return {
        "in": um.get("promptTokenCount", 0) or 0,
        "out": um.get("candidatesTokenCount", 0) or 0,
        "think": um.get("thoughtsTokenCount", 0) or 0,
        "cached": um.get("cachedContentTokenCount", 0) or 0,
    }


def _thinking_config(model: str, level: str) -> Optional[Dict[str, Any]]:
    """thinking_config for `model` at `level`. 'minimal' switches thinking off where the model allows it
    (2.5 Flash family: budget 0) or sets the lowest level (3.x). None when the model has no such control."""
    lvl = (level or "").lower()
    if any(tag in model for tag in ("3.1", "3.5", "3.6", "3.8", "4-")):
        return {"thinking_level": lvl.upper()}
    if "2.5" in model:
        if "pro" in model:                       # 2.5 Pro cannot turn thinking fully off
            return {"thinking_budget": 128}
        return {"thinking_budget": 0 if lvl == "minimal" else 128}
    return None


# Image descriptions: bounded length, no hidden reasoning tokens. The tagger reads only the first
# 400-600 characters of a description, so a concise answer carries the same signal for far fewer tokens.
VISION_MAX_OUTPUT_TOKENS: int = int(os.getenv("VISION_MAX_OUTPUT_TOKENS", "300"))
VISION_THINKING_LEVEL: str = os.getenv("VISION_THINKING_LEVEL", "minimal")
VISION_PROMPT = (
    "Analyze this quiz presentation image. Reply in at most 70 words, plain text, no markdown, no preamble. "
    "First transcribe any meaningful text visible on the image accurately (OCR). "
    "Then describe the key visual content (person, monument, diagram, flag, animal, historical event, etc.) "
    "and any visual clues relevant to a quiz question."
)


# Timeouts and dropped connections are retried like a 429/503 (set 0 to fail them straight away: a call that
# timed out on our side may still have been billed, so each retry can cost one more call).
RETRY_ON_TRANSPORT_ERROR: bool = os.getenv("GEMINI_RETRY_ON_TIMEOUT", "1").strip().lower() not in ("0", "false", "no", "off")
RETRY_AFTER_CAP_S: float = float(os.getenv("GEMINI_RETRY_AFTER_CAP_S", "60"))
RETRY_BACKOFF_CAP_S: float = float(os.getenv("GEMINI_RETRY_BACKOFF_CAP_S", "30"))


def _retry_wait_seconds(resp: Optional[httpx.Response], attempt: int) -> float:
    """Exponential backoff with jitter (2s, 4s, 8s ... capped), raised to Google's Retry-After when it sends one."""
    wait = min(RETRY_BACKOFF_CAP_S, 2.0 * (2 ** attempt)) + random.uniform(0.1, 0.9)
    if resp is not None:
        try:
            header = resp.headers.get("retry-after")
            if header:
                wait = max(wait, min(RETRY_AFTER_CAP_S, float(header)))
        except Exception:
            pass
    return wait


async def _post_with_retry(
    client: httpx.AsyncClient, url: str, payload: Dict[str, Any], api_key: str, max_retries: int = 3,
    timeout: Optional[float] = None, retry_transport: bool = True,
) -> httpx.Response:
    """POST with retries on 429/503. Timeouts and dropped connections are retried too unless retry_transport is
    False, which interactive callers (search, quiz generation) use so a hanging Gemini fails in one timeout
    instead of holding the request through several."""
    headers = {"x-goog-api-key": api_key} if api_key else {}
    extra = {"timeout": timeout} if timeout is not None else {}
    for attempt in range(max_retries + 1):
        try:
            resp = await client.post(url, json=payload, headers=headers, **extra)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
            # Transient network trouble only; configuration errors (ProxyError, UnsupportedProtocol) are not retried.
            if RETRY_ON_TRANSPORT_ERROR and retry_transport and attempt < max_retries:
                wait_sec = _retry_wait_seconds(None, attempt)
                logger.warning(
                    f"[GeminiProvider] {type(e).__name__}. Retrying in {wait_sec:.2f}s (attempt {attempt+1}/{max_retries})..."
                )
                await asyncio.sleep(wait_sec)
                continue
            raise
        if resp.status_code in (429, 503) and attempt < max_retries:
            wait_sec = _retry_wait_seconds(resp, attempt)
            logger.warning(
                f"[GeminiProvider] Rate limit or busy ({resp.status_code}). Backing off {wait_sec:.2f}s (attempt {attempt+1}/{max_retries})..."
            )
            await asyncio.sleep(wait_sec)
            continue
        return resp
    return resp

class GeminiLLMProvider(LLMProvider):
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or settings.GEMINI_API_KEY or ""
        key = key.strip()
        if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
            key = key[1:-1].strip()
        self.api_key = key
        self.model = settings.GEMINI_MODEL or "gemini-3.5-flash-lite"

    def _extract_text(self, data: Dict[str, Any]) -> str:
        candidates = data.get("candidates", [])
        if not candidates:
            feedback = data.get("promptFeedback", {})
            block_reason = feedback.get("blockReason")
            if block_reason:
                raise ValueError(f"Gemini API blocked the request: {block_reason} (feedback: {feedback})")
            raise ValueError(f"Gemini API returned no candidates: {data}")

        first_candidate = candidates[0]
        content = first_candidate.get("content", {})
        parts = content.get("parts", [])
        if not parts:
            finish_reason = first_candidate.get("finishReason")
            raise ValueError(f"Gemini candidate has no parts (finishReason: {finish_reason}): {first_candidate}")

        # In Gemini 2.5 Flash, reasoning/thoughts are returned as parts with 'thought': True.
        # The final output is in the non-thought part.
        for part in reversed(parts):
            if "text" in part and not part.get("thought", False):
                return part["text"]


        # Fallback to the last part with text if all had thought flag or flag wasn't set
        for part in reversed(parts):
            if "text" in part:
                return part["text"]

        raise ValueError(f"No text part found in Gemini candidate parts: {parts}")



    async def generate_text(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        async with _shared_client(60.0) as client:
            resp = await _post_with_retry(client, url, payload, self.api_key, retry_transport=False)
            if resp.is_error:
                logger.error(f"[GeminiLLMProvider] API error ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            data = resp.json()
            return self._extract_text(data)

    async def generate_json(self, prompt: str, system_instruction: Optional[str] = None) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        gen_cfg: Dict[str, Any] = {
            "response_mime_type": "application/json",
            "maxOutputTokens": 1024,
            "temperature": 0.0,
        }
        if any(tag in self.model for tag in ("3.1", "3.5", "3.6", "3.8", "4-")):
            gen_cfg["thinking_config"] = {"thinking_level": "MINIMAL"}
        else:
            gen_cfg["thinking_config"] = {"thinking_budget": 0}

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        logger.info(f"[GeminiLLMProvider] Sending generate_json request to model '{self.model}'")
        async with _shared_client(60.0) as client:
            resp = await _post_with_retry(client, url, payload, self.api_key, retry_transport=False)
            if resp.is_error:
                if "thinking" in resp.text.lower() and "thinking_config" in gen_cfg:
                    gen_cfg.pop("thinking_config", None)
                    resp = await _post_with_retry(client, url, payload, self.api_key, retry_transport=False)
                if resp.is_error:
                    logger.error(f"[GeminiLLMProvider] API error ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            data = resp.json()
            usage = _usage_from_response(data)
            record_usage("generate_other", usage, model=self.model)
            text = self._extract_text(data)
            logger.info(f"[GeminiLLMProvider] Received response from model '{self.model}': {text}")
            clean_text = text.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            elif clean_text.startswith("```"):
                clean_text = clean_text[3:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
            return json.loads(clean_text.strip())

    async def generate_json_schema(
        self,
        prompt: str,
        schema: Dict[str, Any],
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        thinking_level: Optional[str] = None,
        temperature: float = 0.0,
    ) -> tuple[Dict[str, Any], Dict[str, int]]:
        """Generate JSON constrained by `schema` (OpenAPI-style, upper-case types, enums supported).

        Supports per-call model override (so the tagger can run on 3.5 Flash-Lite while the generator
        stays on 2.5 Flash) and a thinking level ('minimal', 'low', 'medium', 'high', or None for default).

        Returns (parsed_json, usage) where usage = {'in', 'out', 'think', 'cached'} (token counts).
        """
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured")

        chosen_model = model or self.model
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent"
        gen_cfg: Dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_schema": schema,
            "temperature": temperature,
        }
        if thinking_level is not None:
            # Gemini 3.x uses thinking_level; 2.5 uses thinking_budget. Pass thinking_level if the model name
            # suggests 3.x, otherwise thinking_budget=0 for minimal. Caller can set thinking_level=None to skip.
            if any(tag in chosen_model for tag in ("3.1", "3.5", "3.6", "3.8", "4-")):
                gen_cfg["thinking_config"] = {"thinking_level": thinking_level.upper()}
            else:
                gen_cfg["thinking_config"] = {"thinking_budget": 0 if thinking_level == "low" else 128}

        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        logger.info(f"[GeminiLLMProvider] generate_json_schema → {chosen_model}")
        async with _shared_client(90.0) as client:
            resp = await _post_with_retry(client, url, payload, self.api_key)
            if resp.is_error:
                # If the model rejects thinking_config (older SDK/model), retry once without it.
                if "thinking" in resp.text.lower() and "thinking_config" in gen_cfg:
                    gen_cfg.pop("thinking_config", None)
                    resp = await _post_with_retry(client, url, payload, self.api_key)
                if resp.is_error:
                    logger.error(f"[GeminiLLMProvider] API error ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            data = resp.json()

        text = self._extract_text(data)
        clean = text.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        elif clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        parsed = json.loads(clean.strip())

        usage = _usage_from_response(data)
        record_usage("tagging", usage, model=chosen_model)
        return parsed, usage

    async def generate_multimodal_ex(
        self,
        prompt: str,
        media_parts: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        thinking_level: Optional[str] = None,
        usage_label: str = "multimodal",
    ) -> tuple[str, Dict[str, int]]:
        """Like generate_multimodal, but can cap the output length / reasoning and returns token usage.

        With no max_output_tokens and no thinking_level the request is identical to the old one."""
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        parts = []
        for media in media_parts:
            parts.append({
                "inline_data": {
                    "mime_type": media["mime_type"],
                    "data": media["data"]
                }
            })
        parts.append({"text": prompt})

        payload: Dict[str, Any] = {"contents": [{"parts": parts}]}
        gen_cfg: Dict[str, Any] = {}
        if max_output_tokens:
            gen_cfg["max_output_tokens"] = int(max_output_tokens)
        if thinking_level:
            tc = _thinking_config(self.model, thinking_level)
            if tc:
                gen_cfg["thinking_config"] = tc
        if gen_cfg:
            payload["generationConfig"] = gen_cfg
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        async with _shared_client(90.0) as client:
            resp = await _post_with_retry(client, url, payload, self.api_key)
            if resp.is_error and "thinking" in resp.text.lower() and "thinking_config" in gen_cfg:
                # The model rejected the thinking control (older model): retry once without it.
                gen_cfg.pop("thinking_config", None)
                if not gen_cfg:
                    payload.pop("generationConfig", None)
                resp = await _post_with_retry(client, url, payload, self.api_key)
            if resp.is_error:
                logger.error(f"[GeminiLLMProvider] API error ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            data = resp.json()
        usage = _usage_from_response(data)
        record_usage(usage_label, usage, model=self.model)
        return self._extract_text(data), usage

    async def generate_multimodal(
        self,
        prompt: str,
        media_parts: List[Dict[str, str]],
        system_instruction: Optional[str] = None
    ) -> str:
        text, _usage = await self.generate_multimodal_ex(
            prompt, media_parts, system_instruction=system_instruction, usage_label="multimodal")
        return text

    async def describe_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        """OCR + short description of one slide image: capped length, minimal reasoning (see VISION_*)."""
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
        media = [{"mime_type": mime_type, "data": b64_data}]
        try:
            try:
                text, _ = await self.generate_multimodal_ex(
                    VISION_PROMPT, media, max_output_tokens=VISION_MAX_OUTPUT_TOKENS,
                    thinking_level=VISION_THINKING_LEVEL, usage_label="vision")
                return text
            except ValueError as e:
                # Output cut off before any text (finishReason MAX_TOKENS): give it one more, larger try.
                if "MAX_TOKENS" not in str(e):
                    raise
                text, _ = await self.generate_multimodal_ex(
                    VISION_PROMPT, media, max_output_tokens=VISION_MAX_OUTPUT_TOKENS * 3,
                    thinking_level=VISION_THINKING_LEVEL, usage_label="vision")
                return text
        finally:
            del b64_data

    async def transcribe_audio(self, audio_bytes: bytes, mime_type: str = "audio/mp3") -> str:
        b64_data = base64.b64encode(audio_bytes).decode("utf-8")
        prompt = (
            "Listen to this audio clip from a quiz presentation. "
            "Transcribe all spoken words word-for-word. "
            "If there is music, theme music, or sound effects, describe them in brackets (e.g. [Dramatic fanfare], [Birdsong])."
        )
        return await self.generate_multimodal(prompt, [{"mime_type": mime_type, "data": b64_data}])

class GeminiEmbeddingProvider(EmbeddingProvider):
    """Google deprecated text-embedding-004 and embedding-001 (confirmed by a Google staff
    reply on the AI developer forum, Nov 2026) -- both now 404 on v1beta's embedContent, with
    no outage window or warning period, and every call was silently falling back to the local
    deterministic embedding (see except block below) instead of a real semantic vector. This
    degraded search/dedup quality for however long the deprecation has been in effect, for
    every question ingested in that window -- a one-time re-embedding backfill of existing
    rows is worth doing once this is confirmed working (see QSHALA_PRIORITY_PLAN_STATUS.md).

    Replacement is gemini-embedding-001, Google's recommended successor. Its default output is
    3072-dim (already L2-normalized), not this app's fixed 768-dim pgvector column
    (settings.EMBEDDING_DIM), so every request asks for a truncated 768-dim output via
    embedContentConfig.outputDimensionality -- and per Google's docs, anything other than the
    3072-dim default comes back NOT normalized and must be normalized by hand, which
    _normalize() below does. taskType is set per call site: RETRIEVAL_QUERY for a search query
    (get_embedding, called from hybrid_retriever.py) vs RETRIEVAL_DOCUMENT for content being
    indexed (get_embeddings, called from pipeline.py during ingestion) -- asymmetric embeddings
    perform meaningfully better for retrieval than embedding both sides the same way.
    """
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or settings.GEMINI_API_KEY or ""
        key = key.strip()
        if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
            key = key[1:-1].strip()
        self.api_key = key
        self.model = "gemini-embedding-001"

    @property
    def may_substitute_local(self) -> bool:
        """True when this provider can hand back a local hash vector instead of a Gemini one (fallback switched on,
        or no API key), so a stored vector cannot be assumed to come from self.model."""
        return ALLOW_LOCAL_EMBEDDING_FALLBACK or not self.api_key

    @staticmethod
    def _normalize(values: List[float]) -> List[float]:
        """gemini-embedding-001 only auto-normalizes its native 3072-dim output; any truncated
        outputDimensionality (768 here) comes back un-normalized and must be L2-normalized by
        hand for cosine similarity (pgvector's distance op) to behave correctly."""
        norm = sum(v * v for v in values) ** 0.5
        if norm == 0:
            return values
        return [v / norm for v in values]

    @classmethod
    def _coerce_dim(cls, values: List[float], text: str) -> List[float]:
        """Confirmed in production (Nov 2 2026): batchEmbedContents returned the native 3072-dim
        output even though every request item carried embedContentConfig.outputDimensionality=768
        -- a psycopg.errors.DataException ("expected 768 dimensions, not 3072") crashed the
        questions INSERT and failed the whole deck. Google's docs don't fully specify whether
        batchEmbedContents honors the same per-item embedContentConfig nesting as the single
        embedContent endpoint, so rather than guess at an undocumented request shape, this
        enforces the output shape instead: gemini-embedding-001 is trained with Matryoshka
        Representation Learning, meaning a prefix of the native 3072-dim vector is itself a valid
        lower-dimensional embedding -- Google's own outputDimensionality truncates this way, so
        doing it client-side when the server didn't is mathematically the same operation, just
        performed locally. Returns an un-normalized dim-length vector for _normalize() to
        L2-normalize as usual (the slice is not normalized, matching every other truncated path).
        Raises ValueError if the vector is shorter than the target dim -- that can't be coerced,
        only reported, so the caller's except block falls back to the local deterministic
        embedding instead of ever inserting a wrong-shaped vector."""
        dim = settings.EMBEDDING_DIM
        if len(values) == dim:
            return values
        if len(values) > dim:
            logger.warning(
                "[GeminiEmbeddingProvider] got %d-dim embedding, expected %d -- truncating "
                "client-side (text=%r)", len(values), dim, text[:80],
            )
            return values[:dim]
        raise ValueError(f"embedding shorter than EMBEDDING_DIM ({len(values)} < {dim})")

    async def get_embedding(self, text: str) -> List[float]:
        """One query-style embedding. Raises EmbeddingUnavailable when Gemini cannot provide a real vector,
        unless EMBEDDING_ALLOW_LOCAL_FALLBACK=1, which restores the old behaviour of silently substituting a
        local hash vector (a different vector space from the stored Gemini vectors)."""
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent"
        payload = {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": text}]},
            "embedContentConfig": {
                "taskType": "RETRIEVAL_QUERY",
                "outputDimensionality": settings.EMBEDDING_DIM,
            },
        }

        try:
            async with _shared_client(30.0) as client:
                resp = await _post_with_retry(client, url, payload, self.api_key, retry_transport=False)
                if resp.is_error:
                    logger.error(f"[GeminiEmbeddingProvider] API error ({resp.status_code}): {resp.text}")
                resp.raise_for_status()
                data = resp.json()
                um = data.get("usageMetadata") or {}
                tokens = um.get("promptTokenCount") or max(1, len(text) // 4)
                record_usage("embedding", {"in": tokens, "calls": 1}, model=self.model)
                values = self._coerce_dim(data["embedding"]["values"], text)
                return self._normalize(values)
        except Exception as e:
            if ALLOW_LOCAL_EMBEDDING_FALLBACK:
                logger.warning(f"[GeminiEmbeddingProvider] API call failed ({e}), falling back to local deterministic embedding.")
                from backend.app.services.ai.local_provider import LocalEmbeddingProvider
                return await LocalEmbeddingProvider(dim=settings.EMBEDDING_DIM).get_embedding(text)
            status = getattr(getattr(e, "response", None), "status_code", None)
            reason = type(e).__name__ + (" " + str(status) if status else "")
            logger.warning("[GeminiEmbeddingProvider] embedding unavailable (%s); not substituting a local vector", reason)
            raise EmbeddingUnavailable(reason) from e

    async def _embed_one_or_none(self, text: str) -> Optional[List[float]]:
        try:
            return await self.get_embedding(text)
        except Exception:
            return None

    async def get_embeddings(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Document embeddings for `texts`, same order. An entry is None when Gemini could not embed that text
        (after retries); the caller stores no vector for it rather than a local hash vector that would sit in the
        same column as real Gemini vectors. EMBEDDING_ALLOW_LOCAL_FALLBACK=1 restores the old substitution."""
        if not texts:
            return []
        if not self.api_key:
            from backend.app.services.ai.local_provider import LocalEmbeddingProvider
            local_p = LocalEmbeddingProvider(dim=settings.EMBEDDING_DIM)
            return [await local_p.get_embedding(t) for t in texts]

        # Use batchEmbedContents endpoint in chunks of up to 96
        chunk_size = 96
        results: List[Optional[List[float]]] = []
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:batchEmbedContents"

        for i in range(0, len(texts), chunk_size):
            chunk = texts[i:i + chunk_size]
            payload = {
                "requests": [
                    {
                        "model": f"models/{self.model}",
                        "content": {"parts": [{"text": t}]},
                        "embedContentConfig": {
                            "taskType": "RETRIEVAL_DOCUMENT",
                            "outputDimensionality": settings.EMBEDDING_DIM,
                        },
                    }
                    for t in chunk
                ]
            }
            try:
                async with _shared_client(45.0) as client:
                    resp = await _post_with_retry(client, url, payload, self.api_key)
                    if resp.is_error:
                        logger.warning(f"[GeminiEmbeddingProvider] batchEmbedContents error ({resp.status_code}), falling back to serial")
                        for t in chunk:
                            results.append(await self._embed_one_or_none(t))
                    else:
                        data_obj = resp.json()
                        um = data_obj.get("usageMetadata") or {}
                        chunk_tokens = um.get("promptTokenCount") or sum(max(1, len(t) // 4) for t in chunk)
                        record_usage("embedding", {"in": chunk_tokens, "calls": len(chunk)}, model=self.model)
                        if len(data := data_obj.get("embeddings", [])) != len(chunk):
                            # batchEmbedContents returned a different count than requested -- can't
                            # safely zip results back to texts by position, so re-fetch this whole
                            # chunk serially rather than risk silently mismatching embeddings to texts.
                            logger.warning(
                                "[GeminiEmbeddingProvider] batchEmbedContents returned %d embeddings "
                                "for %d texts, falling back to serial for this chunk", len(data), len(chunk),
                            )
                            for t in chunk:
                                results.append(await self._embed_one_or_none(t))
                        else:
                            for t, e in zip(chunk, data):
                                try:
                                    values = self._coerce_dim(e["values"], t)
                                    results.append(self._normalize(values))
                                except (ValueError, KeyError, TypeError):
                                    # Too short to coerce (see _coerce_dim) -- fall back to a single
                                    # get_embedding() call for just this text.
                                    logger.warning(
                                        "[GeminiEmbeddingProvider] batch item too short to coerce, "
                                        "refetching serially (text=%r)", t[:80],
                                    )
                                    results.append(await self._embed_one_or_none(t))
            except Exception as batch_err:
                logger.warning(f"[GeminiEmbeddingProvider] batchEmbedContents failed ({type(batch_err).__name__}), falling back to serial")
                # results may already hold part of this chunk if the failure came part-way through it
                del results[i:]
                for t in chunk:
                    results.append(await self._embed_one_or_none(t))

        # Second chance for anything that failed: wait out a rate-limit window, then ask again one by one.
        for _ in range(EMBEDDING_RETRY_PASSES):
            missing = [k for k, v in enumerate(results) if v is None]
            if not missing:
                break
            if EMBEDDING_RETRY_DELAY_S > 0:
                await asyncio.sleep(EMBEDDING_RETRY_DELAY_S)
            for k in missing:
                results[k] = await self._embed_one_or_none(texts[k])
        n_missing = sum(1 for v in results if v is None)
        if n_missing:
            logger.warning("[GeminiEmbeddingProvider] %d of %d texts could not be embedded; storing no vector for them",
                           n_missing, len(texts))
        return results
