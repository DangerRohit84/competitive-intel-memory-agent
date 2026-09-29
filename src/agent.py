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
    return "competitor:{}".format((competitor or "").strip())


def recall_for_battlecard(rfp_text: str, competitor: str) -> list:
    """3-way Hindsight recall, deduped by fact id. Patched in tests."""
    tag = _competitor_tag(competitor)
    queries = [
        "{} pricing features objections".format(competitor),  # factual
        "What changed for {} since March 2026?".format(competitor),  # temporal
        "{} win loss battlecard strengths weaknesses".format(competitor),  # battlecard
    ]
    seen = {}
    for q in queries:
        try:
            facts = hc.recall(query=q, tags=[tag], budget="mid", max_tokens=2000)
        except Exception:
            facts = []
        for f in facts or []:
            fid = f.get("id") or f.get("document_id")
            if fid and fid not in seen:
                seen[fid] = f
            elif not fid:
                seen["idx-{}".format(len(seen))] = f
    # Extra safety: also fold RFP keywords (helps offline store rank correctly)
    if rfp_text:
        try:
            extra = hc.recall(query="{} {}".format(competitor, rfp_text[:200]), tags=[tag], budget="mid", max_tokens=2000)
        except Exception:
            extra = []
        for f in extra or []:
            fid = f.get("id") or f.get("document_id")
            if fid and fid not in seen:
                seen[fid] = f
    return list(seen.values())


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
    """Main entry (teammate Streamlit imports this)."""
    competitor = (competitor or "").strip() or "Unknown"
    facts = recall_for_battlecard(rfp_text or "", competitor) or []
    win = compute_win_rate(competitor)

    citations = [
        {"n": i, "id": f.get("id"), "text": f.get("text", ""), "document_id": f.get("document_id")}
        for i, f in enumerate(facts, 1)
    ]
    memory_impact = {
        "facts_used": len(facts),
        "win_rate": win["win_rate"],
        "wins": win["wins"],
        "losses": win["losses"],
        "seed_records": win["seed_records"],
        "recalls": 3,
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
    return {"battlecard_md": md, "citations": citations, "memory_impact": memory_impact}
