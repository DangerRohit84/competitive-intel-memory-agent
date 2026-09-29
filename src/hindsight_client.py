"""Hindsight Cloud wrapper for Competitive Intel Memory Agent.

LOCKED patterns (see docs/superpowers/specs/2026-09-29-competitive-intel-design.md):
- context ALWAYS (shapes LLM fact extraction)
- stable document_id (upsert — re-retain replaces, no dupes)
- tags ["competitor:<Name>", ...] + recall tags_match="any_strict"
- backdated ISO timestamps (enables "what changed since March?" temporal recall)
- async consolidation lag: retain end-of-turn, recall next-turn (+ recall_with_retry)
- offline fallback: in-memory store so tests/demo pass without keys

Module-level funcs retain/recall/reflect delegate to singleton HindsightMemoryClient.
Compatible with teammate seed.py: retain(text=..., context=..., document_id=..., tags=..., metadata=...)

Env:
  HINDSIGHT_API_KEY, HINDSIGHT_BANK_ID (default competitive-intel),
  HINDSIGHT_BASE_URL (default https://api.hindsight.vectorize.io)
"""
import os
import time
import uuid
from datetime import datetime, timezone

BANK_ID = os.getenv("HINDSIGHT_BANK_ID", "competitive-intel")
BASE_URL = os.getenv("HINDSIGHT_BASE_URL", "https://api.hindsight.vectorize.io")
DEFAULT_TYPES = ["world", "experience", "observation"]
DEFAULT_BUDGET = "mid"
DEFAULT_MAX_TOKENS = 2000

# In-memory offline store: list of fact dicts. Upsert by document_id.
_OFFLINE_STORE: list = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_timestamp(ts) -> str:
    if ts is None:
        return _now_iso()
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    s = str(ts).strip()
    if not s or s.lower() == "unset":
        return _now_iso()
    return s


def clear_offline_store() -> None:
    """Test helper: reset in-memory fallback."""
    _OFFLINE_STORE.clear()


def _offline_upsert(entry: dict) -> None:
    doc_id = entry.get("document_id")
    for i, existing in enumerate(_OFFLINE_STORE):
        if existing.get("document_id") == doc_id and doc_id:
            _OFFLINE_STORE[i] = entry
            return
    _OFFLINE_STORE.append(entry)


def _offline_search(query: str, tags=None, max_results: int = 10) -> list:
    """Keyword + tag-overlap scoring. Respects any_strict: filter to tagged matches."""
    q = (query or "").lower()
    qtokens = [t for t in q.replace(",", " ").replace(".", " ").split() if len(t) > 2]
    scored = []
    for fact in _OFFLINE_STORE:
        ftags = fact.get("tags") or []
        # any_strict: when filter tags supplied, drop untagged / non-matching facts
        # Case-insensitive: Acme==acme==ACME (win-rate already lowercases).
        if tags:
            ftags_lower = set(str(t).lower() for t in ftags)
            tags_lower = set(str(t).lower() for t in tags)
            if not ftags or not ftags_lower & tags_lower:
                continue
        text = (fact.get("text") or "").lower()
        score = 0
        for tok in qtokens:
            if tok in text:
                score += 1
        # tag overlap bonus (case-insensitive)
        if tags:
            ftags_lower = set(str(t).lower() for t in ftags)
            tags_lower = set(str(t).lower() for t in tags)
            score += len(ftags_lower & tags_lower) * 2
        # competitor boost: query mentions competitor explicitly
        scored.append((score, fact))
    scored.sort(key=lambda x: x[0], reverse=True)
    # if query empty or no overlap, still return tag-filtered facts (for battlecard 3-way recall)
    if scored and scored[0][0] == 0:
        return [f for _, f in scored[:max_results]]
    return [f for s, f in scored if s > 0][:max_results] or [f for _, f in scored[:max_results]]


def _get_live_client():
    """Return live Hindsight SDK client or None (offline mode).

    Separated for testability: tests patch this to force offline.
    """
    api_key = os.getenv("HINDSIGHT_API_KEY", "")
    if not api_key or api_key in ("hsk_...", "", "test"):
        return None
    try:
        from hindsight_client import Hindsight as _HindsightSDK  # pip package, not this file

        # Cloud uses bearer token; local uses no key. Pass key when present.
        try:
            return _HindsightSDK(base_url=BASE_URL, api_key=api_key)
        except TypeError:
            # older SDK without api_key kwarg
            return _HindsightSDK(base_url=BASE_URL)
    except Exception:
        return None


def _result_to_fact(r, idx: int = 0) -> dict:
    """Normalize SDK RecallResult (or dict) to plain fact dict."""
    if isinstance(r, dict):
        return {
            "id": r.get("id") or r.get("document_id") or "fact-{}".format(idx),
            "text": r.get("text") or r.get("content") or "",
            "type": r.get("type") or "world",
            "tags": r.get("tags") or [],
            "document_id": r.get("document_id"),
            "context": r.get("context"),
            "occurred_start": r.get("occurred_start"),
            "score": r.get("score"),
        }
    return {
        "id": getattr(r, "id", "fact-{}".format(idx)),
        "text": getattr(r, "text", ""),
        "type": getattr(r, "type", "world"),
        "tags": list(getattr(r, "tags", None) or []),
        "document_id": getattr(r, "document_id", None),
        "context": getattr(r, "context", None),
        "occurred_start": getattr(r, "occurred_start", None),
        "score": getattr(getattr(r, "scores", None), "final", None) if hasattr(r, "scores") else None,
    }


class HindsightMemoryClient:
    """Thin wrapper with offline fallback + demo-safe defaults."""

    def __init__(self, bank_id: str = None, base_url: str = None):
        self.bank_id = bank_id or BANK_ID
        self.base_url = base_url or BASE_URL

    # -- retain --
    def retain(
        self,
        content=None,
        text=None,
        context=None,
        timestamp=None,
        document_id=None,
        tags=None,
        metadata=None,
        **kwargs,
    ) -> dict:
        # seed.py compat: text=... ; spec: content=...
        body = content if content is not None else text
        if body is None:
            body = kwargs.get("content") or kwargs.get("text") or ""
        if not body:
            raise ValueError("retain requires content (or text)")
        # context ALWAYS — default, never None (shapes extraction)
        ctx = context or kwargs.get("context") or "general"
        # stable document_id — upsert key; generate only if caller omitted
        doc_id = document_id or kwargs.get("document_id") or "doc-{}".format(uuid.uuid4().hex[:8])
        tag_list = list(tags or kwargs.get("tags") or [])
        meta = dict(metadata or kwargs.get("metadata") or {})
        # backdated timestamp: top-level wins, else metadata.timestamp, else now
        ts_raw = timestamp if timestamp is not None else kwargs.get("timestamp", meta.get("timestamp"))
        ts_iso = _normalize_timestamp(ts_raw)
        meta.setdefault("timestamp", ts_iso)

        entry = {
            "id": doc_id,
            "text": body,
            "content": body,
            "context": ctx,
            "timestamp": ts_iso,
            "document_id": doc_id,
            "tags": tag_list,
            "metadata": meta,
            "type": meta.get("intel_type", "world"),
        }
        # Always keep local cache (upsert) so offline recall works post-retain
        _offline_upsert(entry)

        client = _get_live_client()
        if client is None:
            return {"ok": True, "document_id": doc_id, "offline": True}
        try:
            # SDK: retain(bank_id, content, context, timestamp, document_id, metadata, tags)
            # timestamp may be datetime or ISO str — pass ISO str (server parses)
            client.retain(
                bank_id=self.bank_id,
                content=body,
                context=ctx,
                timestamp=ts_iso,
                document_id=doc_id,
                metadata={k: str(v) for k, v in meta.items() if v is not None},
                tags=tag_list,
            )
            return {"ok": True, "document_id": doc_id, "offline": False}
        except Exception as exc:
            # Never crash demo on network/TPM errors — offline cache already saved
            return {"ok": True, "document_id": doc_id, "offline": True, "warning": str(exc)}

    # -- recall --
    def recall(
        self,
        query: str,
        tags=None,
        budget: str = DEFAULT_BUDGET,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        types=None,
        **kwargs,
    ) -> list:
        types = types or DEFAULT_TYPES
        max_results = max(1, min(20, max_tokens // 150))  # ~150 tok/fact heuristic
        client = _get_live_client()
        if client is not None:
            try:
                resp = client.recall(
                    bank_id=self.bank_id,
                    query=query,
                    tags=tags,
                    tags_match="any_strict",  # LOCKED: tagged-only scope
                    budget=budget or DEFAULT_BUDGET,
                    max_tokens=max_tokens or DEFAULT_MAX_TOKENS,
                    types=types,
                )
                results = getattr(resp, "results", None) or []
                facts = [_result_to_fact(r, i) for i, r in enumerate(results)]
                if facts:
                    return facts
                # live returned empty → fall through to offline cache (consolidation lag)
            except Exception:
                pass  # fall through to offline
        return _offline_search(query, tags=tags, max_results=max_results)

    def recall_with_retry(self, query: str, tags=None, retries: int = 3, delay_s: float = 2.0, **kw) -> list:
        """Handle async consolidation lag: retain is end-of-turn, recall is next-turn.

        Observations consolidate async — immediate recall may miss them.
        Poll up to `retries` times; return first non-empty hit.
        """
        last: list = []
        for attempt in range(retries):
            last = self.recall(query, tags=tags, **kw)
            if last:
                return last
            if attempt < retries - 1:
                time.sleep(delay_s)
        return last

    # -- reflect --
    def reflect(
        self,
        query: str,
        tags=None,
        include_facts: bool = True,
        response_schema: dict = None,
        **kwargs,
    ) -> dict:
        """Reflect with structured output + based_on.memories IDs.

        - tags_match="any_strict" preserved (tagged-only scope).
        - response_schema passed through for {conclusion, cited_ids, confidence}.
        - Returns {text, facts, structured_output, offline}.
        - Offline fallback: synthesize from recall (no keys) — caller builds
          [Memory N -> intel-00X] citations from returned facts.
        """
        client = _get_live_client()
        if client is not None:
            try:
                reflect_kwargs = dict(
                    bank_id=self.bank_id,
                    query=query,
                    tags=tags,
                    tags_match="any_strict",  # LOCKED: tagged-only scope
                    include_facts=include_facts,
                )
                if response_schema is not None:
                    reflect_kwargs["response_schema"] = response_schema
                # allow caller overrides (budget/context/...) without breaking locked defaults
                reflect_kwargs.update(kwargs)
                resp = client.reflect(**reflect_kwargs)
                based = []
                based_on = getattr(resp, "based_on", None)
                if based_on and getattr(based_on, "memories", None):
                    based = [_result_to_fact(f, i) for i, f in enumerate(based_on.memories)]
                structured = getattr(resp, "structured_output", None)
                return {
                    "text": getattr(resp, "text", ""),
                    "facts": based,
                    "structured_output": structured,
                    "offline": False,
                }
            except Exception:
                pass  # fall through; offline cache already available
        facts = self.recall(query, tags=tags)
        summary = "\n".join("- {}".format(f.get("text", "")) for f in facts[:5]) or "No memories found."
        return {"text": summary, "facts": facts, "structured_output": None, "offline": True}

    def get_memory_stats(self) -> dict:
        """Type counts for UI dashboard: {world, experience, observation, total}.

        Offline store first (always works); live list_memories best-effort merge
        (takes max per bucket to survive consolidation lag).
        Maps seed intel_types (pricing/feature->world, win/loss->experience)
        so seed.json dashboard is meaningful offline.
        """
        def _bucket(t: str) -> str:
            t = str(t or "world").lower()
            if t in ("world", "experience", "observation"):
                return t
            if t in ("win", "loss"):
                return "experience"
            # pricing/feature/general/unknown -> world knowledge
            return "world"

        counts = {"world": 0, "experience": 0, "observation": 0, "total": 0}
        for fact in _OFFLINE_STORE:
            counts[_bucket(fact.get("type"))] += 1
        counts["total"] = len(_OFFLINE_STORE)

        client = _get_live_client()
        if client is not None:
            try:
                live_counts = {}
                for t in ("world", "experience", "observation"):
                    try:
                        resp = client.list_memories(bank_id=self.bank_id, type=t, limit=1)
                        tot = getattr(resp, "total", None)
                        if tot is None:
                            items = getattr(resp, "items", None) or []
                            tot = len(items)
                        live_counts[t] = int(tot or 0)
                    except Exception:
                        live_counts[t] = 0
                # merge: max() avoids undercount when offline has unsynced retains
                # or live has older synced docs not in this process's memory
                for t in ("world", "experience", "observation"):
                    if live_counts.get(t, 0) > counts[t]:
                        counts[t] = live_counts[t]
                counts["total"] = counts["world"] + counts["experience"] + counts["observation"]
            except Exception:
                pass  # best-effort only; offline counts already valid
        return counts


# Singleton + module-level funcs (seed.py imports `retain` directly)
_default_client = HindsightMemoryClient()


def retain(content=None, text=None, context=None, timestamp=None, document_id=None, tags=None, metadata=None, **kwargs):
    return _default_client.retain(
        content=content, text=text, context=context, timestamp=timestamp,
        document_id=document_id, tags=tags, metadata=metadata, **kwargs,
    )


def recall(query, tags=None, budget=DEFAULT_BUDGET, max_tokens=DEFAULT_MAX_TOKENS, types=None, **kwargs):
    if types is None:
        types = list(DEFAULT_TYPES)
    return _default_client.recall(query, tags=tags, budget=budget, max_tokens=max_tokens, types=types, **kwargs)


def reflect(query, tags=None, include_facts=True, response_schema=None, **kwargs):
    return _default_client.reflect(
        query, tags=tags, include_facts=include_facts, response_schema=response_schema, **kwargs
    )


def recall_with_retry(query, tags=None, retries=3, delay_s=2.0, **kw):
    return _default_client.recall_with_retry(query, tags=tags, retries=retries, delay_s=delay_s, **kw)


def get_memory_stats() -> dict:
    """Module-level helper for UI dashboard: {world, experience, observation, total}."""
    return _default_client.get_memory_stats()
