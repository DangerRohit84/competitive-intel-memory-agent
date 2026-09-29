"""Competitive Intel Memory Agent - Streamlit UI.

Left pane: RFP input + competitor + retain + generate.
Right pane: Cold (no memory) vs With-memory battlecard, citations,
recall trace, win-rate dashboard from seed.json.

Runs offline using seed.json when Hindsight keys are missing.
"""

import json
import os
import re

import streamlit as st

SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed.json")
COMPETITORS = ["Acme", "Nova", "Vertex"]
INTEL_TYPES = ["pricing", "feature", "win", "loss"]


# ---------- data helpers ----------

def load_seed_items():
    try:
        with open(SEED_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def win_rates(items):
    """Compute win rate per competitor from win/loss items. O(n)."""
    stats = {}
    for it in items:
        c = it.get("competitor", "?")
        t = it.get("type")
        if t not in ("win", "loss"):
            continue
        s = stats.setdefault(c, {"win": 0, "loss": 0})
        s[t] += 1
    out = {}
    for c, s in stats.items():
        total = s["win"] + s["loss"]
        out[c] = {
            "wins": s["win"],
            "losses": s["loss"],
            "win_rate": round(100.0 * s["win"] / total, 1) if total else 0.0,
        }
    return out


def score_item(rfp_text, item):
    """Simple keyword overlap score for offline recall. O(w)."""
    words = set(re.findall(r"[a-z]{3,}", rfp_text.lower()))
    text = (item.get("content", "") + " " + " ".join(item.get("tags", []))).lower()
    return sum(1 for w in words if w in text)


def offline_recall(rfp_text, competitor, items, top_k=5):
    scored = []
    for it in items:
        if it.get("competitor") != competitor:
            continue
        s = score_item(rfp_text, it)
        # boost exact type keywords so pricing RFPs surface pricing notes
        scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    # fall back to recency order when no overlap
    if all(s == 0 for s, _ in scored):
        by_date = sorted(items, key=lambda x: x.get("timestamp", ""),
                         reverse=True)
        filtered = [x for x in by_date if x.get("competitor") == competitor]
        return [(0, x) for x in filtered[:top_k]]
    return scored[:top_k]


def cold_battlecard(rfp_text, competitor):
    return (
        "## Battlecard vs {} (generic, no memory)\n\n".format(competitor)
        + "- **Positioning:** We offer a secure, scalable platform for your needs.\n"
        + "- **Pricing:** Competitive per-seat pricing with annual discount.\n"
        + "- **Proof:** Trusted by customers across industries.\n"
        + "- **Next step:** Schedule a technical deep-dive.\n\n"
        + "_Generated without memory - no deal-specific evidence._"
    )


def offline_battlecard(rfp_text, competitor, recalled):
    lines = ["## Battlecard vs {} (memory-assisted)\n".format(competitor)]
    pricing = [it for _, it in recalled if it.get("type") == "pricing"]
    feats = [it for _, it in recalled if it.get("type") == "feature"]
    wins = [it for _, it in recalled if it.get("type") == "win"]
    losses = [it for _, it in recalled if it.get("type") == "loss"]
    if pricing:
        lines.append("### Pricing intel")
        for it in pricing[:2]:
            lines.append("- {} ({} | {})".format(
                it["content"][:220], it.get("deal_id"), it.get("timestamp", "")[:10]))
    if feats:
        lines.append("\n### Traps to set")
        for it in feats[:2]:
            lines.append("- {} ".format(it["content"][:220]))
    if wins:
        lines.append("\n### Proof points")
        for it in wins[:2]:
            lines.append("- {} ".format(it["content"][:220]))
    if losses:
        lines.append("\n### Risks / landmines")
        for it in losses[:1]:
            lines.append("- {} ".format(it["content"][:220]))
    lines.append("\n_Memory impact: {} notes recalled for {} - "
                 "deal-specific pricing, traps and proof vs generic._".format(
                     len(recalled), competitor))
    return "\n".join(lines)


def has_hindsight_keys():
    return bool(os.getenv("HINDSIGHT_API_KEY"))


def generate_with_memory(rfp_text, competitor, recalled):
    """Try senior's src.agent, else offline fallback over seed.json."""
    try:
        from src.agent import generate_battlecard
        result = generate_battlecard(rfp_text, competitor)
        # expected {battlecard_md, citations, memory_impact}
        if isinstance(result, dict) and "battlecard_md" in result:
            # Fresh-launch bridge: honest-empty from Hindsight store (empty
            # until Seed/Retain) must not diverge from seed.json trace.
            # If citations are empty but seed recall hit, render offline card.
            if not result.get("citations") and recalled:
                md = offline_battlecard(rfp_text, competitor, recalled)
                cites = [{"id": it.get("id"), "text": it.get("content", "")[:200]}
                         for _, it in recalled[:2]]
                return {"battlecard_md": md, "citations": cites,
                        "memory_impact": "offline fallback: {} notes from seed.json".format(
                            len(recalled))}, "offline"
            return result, "hindsight"
    except Exception as exc:
        st.session_state["_agent_error"] = str(exc)
    md = offline_battlecard(rfp_text, competitor, recalled)
    cites = [{"id": it.get("id"), "text": it.get("content", "")[:200]}
             for _, it in recalled[:2]]
    return {"battlecard_md": md, "citations": cites,
            "memory_impact": "offline fallback: {} notes from seed.json".format(
                len(recalled))}, "offline"


# ---------- UI ----------

st.set_page_config(page_title="Competitive Intel Memory Agent", layout="wide")
st.title("Competitive Intel Memory Agent")
st.caption("Hindsight-powered battlecards: cold generic vs memory-recalled.")

items = load_seed_items()
left, right = st.columns([1, 2])

with left:
    st.subheader("Input")
    competitor = st.selectbox("Competitor", COMPETITORS, index=0)
    rfp_text = st.text_area("RFP / deal context", height=180,
                            value="We are a 400-seat logistics firm evaluating Acme. "
                                  "Need SSO, audit logs, India hosting, and per-seat pricing.")
    gen = st.button("Generate battlecard", type="primary")
    st.divider()
    st.subheader("Retain new intel")
    new_type = st.selectbox("Intel type", INTEL_TYPES, index=0)
    new_text = st.text_area("Intel note", height=100,
                            placeholder="e.g. Acme offered 15% discount to ...")
    if st.button("Retain new intel"):
        if not new_text.strip():
            st.warning("Type an intel note first.")
        else:
            try:
                from src.hindsight_client import retain
                retain(text=new_text, context="competitive intel " + new_type,
                       tags=["competitor:" + competitor])
                st.success("Retained to Hindsight.")
            except Exception as exc:
                st.warning("Hindsight unavailable ({}). Saved locally to session only.".format(exc))
    st.divider()
    if st.button("Seed demo data"):
        try:
            import seed as seed_mod
            seed_mod.retain_all(seed_mod.load_seed_items(seed_mod.DEFAULT_SEED_FILE))
            st.success("Seeded via Hindsight.")
        except Exception as exc:
            st.warning("Hindsight unavailable ({}). seed.json already powers offline mode.".format(exc))
    st.caption("Seed items loaded: {}".format(len(items)))

with right:
    st.subheader("Win-rate dashboard (from seed.json)")
    rates = win_rates(items)
    if rates:
        cols = st.columns(len(rates))
        for col, (c, s) in zip(cols, sorted(rates.items())):
            col.metric("{} win rate".format(c),
                       "{}%".format(s["win_rate"]),
                       "{}W-{}L".format(s["wins"], s["losses"]))
    else:
        st.info("No win/loss data found.")

    if gen:
        recalled = offline_recall(rfp_text, competitor, items)
        result, mode = generate_with_memory(rfp_text, competitor, recalled)
        if mode == "offline" and not has_hindsight_keys():
            st.info("Offline fallback: Hindsight keys not set - recalling from local seed.json.")
        elif mode == "offline":
            st.warning("Agent fallback: {}".format(
                st.session_state.get("_agent_error", "unknown")))
        else:
            st.success("Live memory: recalled via Hindsight.")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### Cold (no memory)")
            st.markdown(cold_battlecard(rfp_text, competitor))
        with c2:
            st.markdown("### With memory")
            st.markdown(result.get("battlecard_md", ""))
        st.markdown("### Citations (Memory 1, 2)")
        for i, c in enumerate(result.get("citations", [])[:2], 1):
            st.write("**Memory {}** [{}]: {}".format(
                i, c.get("id", "?"), c.get("text", "")[:300]))
        st.markdown("### Recall trace (text | type | score)")
        for s, it in recalled:
            st.code("{} | {} | score={}".format(
                it.get("content", "")[:150], it.get("type"), s))
        st.markdown("### Memory impact")
        st.write(result.get("memory_impact", ""))
    else:
        st.info("Enter an RFP and press Generate battlecard.")
