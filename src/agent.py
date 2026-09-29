"""Battlecard agent: Hindsight recall (3-way) + Groq grounded generation.

generate_battlecard(rfp_text, competitor) -> {battlecard_md, citations, memory_impact}

- 3-way recall: factual / temporal ("what changed since March?") / battlecard
  all scoped tags=["competitor:<Name>"], tags_match=any_strict (inside wrapper)
- Groq chat (qwen/qwen3-32b) grounded ONLY on recalled facts, [Memory N] citations
- Win-rate computed in CODE from seed.json records — never LLM math
- No-match: honest "No memory found", empty citations, no hallucinated [Memory N]
- Groq TPM/429 errors: backoff + smaller prompt, then offline template fallback
"""
import json
import os
import time

from src import hindsight_client as hc

SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed.json")
CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "qwen/qwen3-32b")


def _competitor_tag(competitor: str) -> str:
    # Canonicalize case so Acme==acme==ACME; keeps tags=["competitor:X"] convention.
    # Stored tags use Title case (seed.py); downstream _offline_search is also
    # case-insensitive (lower() both sides) as belt-and-braces.
    name = (competitor or "").strip()
    return "competitor:{}".format(name.title() if name else "")


REFLECT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "conclusion": {"type": "string"},
        "cited_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["conclusion", "cited_ids"],
    "additionalProperties": False,
}


def _dedupe_into(seen: dict, facts: list) -> None:
    for f in facts or []:
        fid = f.get("id") or f.get("document_id")
        if fid and fid not in seen:
            seen[fid] = f
        elif not fid:
            seen["idx-{}".format(len(seen))] = f


def recall_for_battlecard(rfp_text: str, competitor: str) -> list:
    """Generic (factual + battlecard + RFP) recall, deduped by fact id.

    Temporal query is SPLIT OUT (see recall_temporal_for_battlecard) — do NOT
    dedupe temporal into generic facts. All recalls use recall_with_retry
    (retries=3) to survive async consolidation lag. Patched in tests.
    """
    tag = _competitor_tag(competitor)
    queries = [
        "{} pricing features objections".format(competitor),  # factual
        "{} win loss battlecard strengths weaknesses".format(competitor),  # battlecard
    ]
    seen = {}
    for q in queries:
        try:
            facts = hc.recall_with_retry(query=q, tags=[tag], budget="mid", max_tokens=2000, retries=3)
        except Exception:
            facts = []
        _dedupe_into(seen, facts)
    # Extra safety: also fold RFP keywords (helps offline store rank correctly)
    if rfp_text:
        try:
            extra = hc.recall_with_retry(
                query="{} {}".format(competitor, rfp_text[:200]),
                tags=[tag],
                budget="mid",
                max_tokens=2000,
                retries=3,
            )
        except Exception:
            extra = []
        _dedupe_into(seen, extra)
    return list(seen.values())


def recall_temporal_for_battlecard(competitor: str) -> list:
    """Separate temporal recall — NOT deduped into generic facts.

    Query: "what changed since March?" Returns {temporal_facts with timestamp}.
    Uses recall_with_retry(retries=3) for consolidation lag. Preserves
    timestamp/occurred_start + document_id + context + tags (any_strict via wrapper).
    """
    tag = _competitor_tag(competitor)
    q = "What changed for {} since March 2026?".format(competitor)
    try:
        facts = hc.recall_with_retry(query=q, tags=[tag], budget="mid", max_tokens=2000, retries=3)
    except Exception:
        facts = []
    return list(facts or [])


def reflect_for_battlecard(competitor: str, rfp_text: str, facts: list) -> dict:
    """Hindsight reflect after 3-way recall.

    Calls hc.reflect(query, tags=[competitor tag], include_facts=True,
    response_schema={conclusion, cited_ids, confidence}) and returns
    based_on.memories IDs as synthesis_cites.

    Offline fallback (no keys): synthesize from recalled facts with
    [Memory N -> intel-00X] citations — grounded ONLY on recalled facts.
    Returns {synthesis_md, synthesis_cites, conclusion, cited_ids, confidence, offline}.
    """
    tag = _competitor_tag(competitor)
    query = "Synthesize battlecard for {}: {}. Ground only on recalled facts.".format(
        competitor, (rfp_text or "")[:300]
    )
    try:
        res = hc.reflect(query, tags=[tag], include_facts=True, response_schema=REFLECT_RESPONSE_SCHEMA)
    except Exception:
        res = {"text": "", "facts": [], "offline": True, "structured_output": None}
    res = res or {}
    live_facts = res.get("facts") or []
    structured = res.get("structured_output") or {}
    offline = bool(res.get("offline", True))

    if not offline and (res.get("text") or structured):
        # Live path: prefer structured_output, fall back to text + based_on facts
        conclusion = structured.get("conclusion") or res.get("text") or ""
        cited_ids = list(structured.get("cited_ids") or [f.get("id") or f.get("document_id") for f in live_facts])
        try:
            confidence = float(structured.get("confidence", 0.8))
        except Exception:
            confidence = 0.8
        # Map cited_ids back to Memory N via live facts (fallback to recalled facts)
        pool = live_facts or facts or []
        id_to_n = {}
        for i, f in enumerate(pool, 1):
            for key in (f.get("id"), f.get("document_id")):
                if key and key not in id_to_n:
                    id_to_n[key] = i
        synthesis_cites = [
            {"n": id_to_n.get(cid, idx + 1), "id": cid} for idx, cid in enumerate(cited_ids) if cid
        ]
        # Ensure synthesis_md carries [Memory N] grounding
        synth_lines = ["## Synthesis (Hindsight reflect)", "", conclusion, ""]
        for c in synthesis_cites:
            synth_lines.append("- [Memory {} -> {}]".format(c["n"], c["id"]))
        return {
            "synthesis_md": "\n".join(synth_lines),
            "synthesis_cites": synthesis_cites,
            "conclusion": conclusion,
            "cited_ids": cited_ids,
            "confidence": confidence,
            "offline": False,
        }

    # Offline fallback: synthesize strictly from recalled facts
    pool = facts or []
    synthesis_cites = [
        {"n": i, "id": f.get("id"), "document_id": f.get("document_id"), "text": f.get("text", "")}
        for i, f in enumerate(pool[:5], 1)
    ]
    if not pool:
        return {
            "synthesis_md": "No memories to synthesize.",
            "synthesis_cites": [],
            "conclusion": "No memories to synthesize.",
            "cited_ids": [],
            "confidence": 0.0,
            "offline": True,
        }
    lines = ["## Synthesis (offline, grounded on recalled facts)", ""]
    for i, f in enumerate(pool[:5], 1):
        fid = f.get("id") or f.get("document_id") or "unknown"
        lines.append("- {} [Memory {} -> {}]".format(str(f.get("text", ""))[:220], i, fid))
    return {
        "synthesis_md": "\n".join(lines),
        "synthesis_cites": synthesis_cites,
        "conclusion": "\n".join(lines),
        "cited_ids": [c["id"] for c in synthesis_cites if c.get("id")],
        "confidence": 0.5,
        "offline": True,
    }


def get_memory_stats() -> dict:
    """Type counts for UI dashboard: {world, experience, observation, total}.

    Delegates to hindsight_client (offline store + live best-effort).
    """
    try:
        return hc.get_memory_stats()
    except Exception:
        return {"world": 0, "experience": 0, "observation": 0, "total": 0}


def _load_seed_records(competitor: str) -> list:
    try:
        with open(SEED_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return []
    c = (competitor or "").lower()
    return [it for it in items if str(it.get("competitor", "")).lower() == c]


def compute_win_rate(competitor: str) -> dict:
    """CODE-computed win-rate from seed records (never LLM)."""
    records = _load_seed_records(competitor)
    wins = sum(1 for r in records if r.get("type") == "win")
    losses = sum(1 for r in records if r.get("type") == "loss")
    total = wins + losses
    rate = round(wins / total, 2) if total else 0.0
    return {"wins": wins, "losses": losses, "decided": total, "win_rate": rate, "seed_records": len(records)}


def _facts_block(facts: list) -> str:
    lines = []
    for i, f in enumerate(facts, 1):
        lines.append("[Memory {}] (id={}) {}".format(i, f.get("id"), f.get("text", "")))
    return "\n".join(lines)


def _groq_complete(prompt: str, max_tokens: int = 1200) -> str:
    """Groq chat completion with TPM/429 backoff. Raises on final failure (caller falls back)."""
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key or api_key in ("gsk_...", ""):
        raise RuntimeError("GROQ_API_KEY missing — offline fallback")
    from groq import Groq  # lazy so tests without groq installed still import

    client = Groq(api_key=api_key)
    attempts = [(max_tokens, 0), (max_tokens // 2, 2.0)]
    last_exc = None
    for tok, wait in attempts:
        try:
            resp = client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a competitive-intel battlecard writer. "
                            "Use ONLY the provided [Memory N] facts. Every claim must cite [Memory N]. "
                            "If facts are thin, say what is unknown. Never invent prices, dates, or wins."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=tok,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            if "429" in msg or "tpm" in msg or "rate" in msg or "token" in msg:
                time.sleep(wait or 2.0)
                continue
            raise
    raise last_exc


def _offline_battlecard(competitor: str, rfp_text: str, facts: list, win: dict) -> str:
    md = ["# Battlecard: {} vs Us".format(competitor), ""]
    md.append("RFP focus: {}".format((rfp_text or "")[:200]))
    md.append("")
    md.append("## Win rate (from {} past deals, computed in code)".format(win["decided"]))
    md.append(
        "Won {w} / Lost {l} -> win rate {r:.0%} [code-computed, not LLM].".format(
            w=win["wins"], l=win["losses"], r=win["win_rate"]
        )
    )
    md.append("")
    md.append("## What memory says")
    for i, f in enumerate(facts, 1):
        md.append("- {} [Memory {}]".format(f.get("text", ""), i))
    md.append("")
    md.append("## Talk track")
    md.append("- Lead with our differentiators cited above; anchor on [Memory 1].")
    md.append("- Address price objections with win/loss evidence [Memory 2]." if len(facts) > 1 else "- Qualify further.")
    return "\n".join(md)


def generate_battlecard(rfp_text: str, competitor: str) -> dict:
    """Main entry (teammate Streamlit imports this).

    Flow: 3-way recall (generic + separate temporal, both recall_with_retry)
    -> reflect (structured {conclusion, cited_ids, confidence}) -> Groq grounded
    ONLY on recalled facts -> code-computed win-rate. Returns
    {battlecard_md, citations, memory_impact, synthesis_md, synthesis_cites,
     temporal_facts}. Stable document_id/context/tags(any_strict) preserved
    inside hindsight_client wrapper.
    """
    competitor = (competitor or "").strip() or "Unknown"
    facts = recall_for_battlecard(rfp_text or "", competitor) or []
    try:
        temporal_facts = recall_temporal_for_battlecard(competitor) or []
    except Exception:
        temporal_facts = []
    win = compute_win_rate(competitor)

    citations = [
        {"n": i, "id": f.get("id"), "text": f.get("text", ""), "document_id": f.get("document_id")}
        for i, f in enumerate(facts, 1)
    ]
    # Reflect AFTER recall — grounded synthesis with based_on.memories IDs.
    try:
        reflection = reflect_for_battlecard(competitor, rfp_text or "", facts)
    except Exception:
        reflection = {
            "synthesis_md": "No memories to synthesize.",
            "synthesis_cites": [],
            "conclusion": "",
            "cited_ids": [],
            "confidence": 0.0,
            "offline": True,
        }
    memory_impact = {
        "facts_used": len(facts),
        "temporal_facts": len(temporal_facts),
        "win_rate": win["win_rate"],
        "wins": win["wins"],
        "losses": win["losses"],
        "seed_records": win["seed_records"],
        "recalls": 3,
        "reflect_offline": bool(reflection.get("offline", True)),
        "synthesis_cites": len(reflection.get("synthesis_cites") or []),
    }

    if not facts:
        tag_hint = "competitor:" + competitor
        return {
            "battlecard_md": (
                "No memory found for **{}** in Hindsight bank `{}`.\n\n"
                "Seed it first: `python seed.py` (or check tags `{}`). "
                "No battlecard generated - refusing to hallucinate.".format(competitor, hc.BANK_ID, tag_hint)
            ),
            "citations": [],
            "memory_impact": memory_impact,
            "synthesis_md": "No memories to synthesize.",
            "synthesis_cites": [],
            "temporal_facts": [
                {
                    "id": f.get("id"),
                    "document_id": f.get("document_id"),
                    "text": f.get("text", ""),
                    "timestamp": f.get("timestamp") or f.get("occurred_start"),
                    "tags": f.get("tags") or [],
                }
                for f in temporal_facts
            ],
        }

    prompt = (
        "Competitor: {}\nRFP: {}\n\nMemory facts (cite as [Memory N]):\n{}\n\n"
        "Win/loss (computed in code, state as-is): {}W-{}L, win rate {:.0%}.\n\n"
        "Write a concise battlecard: Pricing, Strengths, Weaknesses, How we win, Landmines. "
        "Every bullet must end with [Memory N].".format(
            competitor, (rfp_text or "")[:800], _facts_block(facts), win["wins"], win["losses"], win["win_rate"]
        )
    )
    try:
        md = _groq_complete(prompt)
        if not md or "[Memory" not in md:
            # LLM dropped citations → enforce offline template (citations guaranteed)
            raise RuntimeError("LLM output missing [Memory N] citations")
    except Exception:
        md = _offline_battlecard(competitor, rfp_text or "", facts, win)
    return {
        "battlecard_md": md,
        "citations": citations,
        "memory_impact": memory_impact,
        "synthesis_md": reflection.get("synthesis_md", ""),
        "synthesis_cites": reflection.get("synthesis_cites", []),
        "temporal_facts": [
            {
                "id": f.get("id"),
                "document_id": f.get("document_id"),
                "text": f.get("text", ""),
                "timestamp": f.get("timestamp") or f.get("occurred_start"),
                "tags": f.get("tags") or [],
            }
            for f in temporal_facts
        ],
    }
