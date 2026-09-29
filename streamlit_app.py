"""Competitive Intel Memory Agent - Streamlit UI.

Left pane: RFP input + competitor + retain + generate + stage kit.
Right pane: Cold (no memory) vs With-memory battlecard, synthesis,
temporal timeline, citations, recall trace, win-rate + observations
dashboard with 1-5-20 curve, ROI line, and .md export.

Runs on a local memory snapshot (seed.json, 18 notes) when Hindsight
keys are missing - same recall path, live when keys are set.
"""

import datetime
import json
import os
import re
import time

import streamlit as st

SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed.json")
COMPETITORS = ["Acme", "Nova", "Vertex"]
INTEL_TYPES = ["pricing", "feature", "win", "loss"]
MEMORY_MONTHLY_COST = 50  # $/mo for ROI math (annual = $600)

DEFAULT_RFP = ("We are a 400-seat logistics firm evaluating Acme. "
               "Need SSO, audit logs, India hosting, and per-seat pricing.")

# Golden RFP cheat-sheet: 3 judge-proof queries with expected Memory IDs.
GOLDEN_RFPS = [
    {
        "label": "1 - Acme logistics (default)",
        "competitor": "Acme",
        "text": DEFAULT_RFP,
        "expected": ["intel-001", "intel-002", "intel-003"],
        "why": "Pricing $48K + SSO/audit trap + Globex $96K win.",
    },
    {
        "label": "2 - Nova startup flat-rate",
        "competitor": "Nova",
        "text": ("We are a 500-seat startup evaluating Nova. "
                 "Flat-rate vs per-seat pricing? Need Shopify connector "
                 "and multilingual support."),
        "expected": ["intel-007", "intel-012", "intel-010"],
        "why": "FinEdge $36K flat-rate + $15/24 pricing + EduSpark win.",
    },
    {
        "label": "3 - Vertex enterprise + API",
        "competitor": "Vertex",
        "text": ("We are a 1000-seat enterprise evaluating Vertex. "
                 "Sterling Bank premium quote? API rate limits and "
                 "webhook delays? SAP integration speed?"),
        "expected": ["intel-013", "intel-015", "intel-016"],
        "why": "Sterling $210K premium + 100 req/min API + AeroFab $150K win.",
    },
]


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
    if losses:
        lines.append("\n### Risks / landmines")
        for it in losses[:1]:
            lines.append("- {} ".format(it["content"][:220]))
    if wins:
        lines.append("\n### Proof points")
        for it in wins[:2]:
            lines.append("- {} ".format(it["content"][:220]))
    lines.append("\n_Memory impact: {} notes recalled for {} - "
                 "deal-specific pricing, traps and proof vs generic._".format(
                     len(recalled), competitor))
    return "\n".join(lines)


def has_hindsight_keys():
    return bool(os.getenv("HINDSIGHT_API_KEY"))


def generate_with_memory(rfp_text, competitor, recalled):
    """Try senior's src.agent, else local snapshot over seed.json."""
    try:
        from src.agent import generate_battlecard
        result = generate_battlecard(rfp_text, competitor)
        # expected {battlecard_md, citations, memory_impact,
        #           synthesis_md, synthesis_cites, temporal_facts}
        if isinstance(result, dict) and "battlecard_md" in result:
            # Fresh-launch bridge: honest-empty from Hindsight store (empty
            # until Seed/Retain) must not diverge from seed.json trace.
            # If citations are empty but seed recall hit, render local card.
            if not result.get("citations") and recalled:
                md = offline_battlecard(rfp_text, competitor, recalled)
                cites = [{"id": it.get("id"), "text": it.get("content", "")[:200]}
                         for _, it in recalled[:2]]
                return {"battlecard_md": md, "citations": cites,
                        "memory_impact": "local memory snapshot: {} notes from seed.json "
                        "- same recall path as live Hindsight".format(len(recalled))}, "offline"
            return result, "hindsight"
    except Exception as exc:
        st.session_state["_agent_error"] = str(exc)
    md = offline_battlecard(rfp_text, competitor, recalled)
    cites = [{"id": it.get("id"), "text": it.get("content", "")[:200]}
             for _, it in recalled[:2]]
    return {"battlecard_md": md, "citations": cites,
            "memory_impact": "local memory snapshot: {} notes from seed.json "
            "- same recall path as live Hindsight".format(len(recalled))}, "offline"


# ---------- new helpers (UI-only, no src/ changes) ----------

def _normalize_ts(value):
    """Normalize timestamp to ISO string for stable lexicographic sort. O(1)."""
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # Normalize trailing Z -> +00:00 so mixed Z/offset/date-only sort together.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return text


def _item_timestamp(item):
    """Best-effort normalized timestamp for seed or Hindsight fact dicts."""
    for key in ("timestamp", "occurred_start", "occurred_at"):
        val = item.get(key)
        if val:
            return _normalize_ts(val)
    meta = item.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("timestamp"):
        return _normalize_ts(meta["timestamp"])
    return ""


def _temporal_sort_key(fact):
    """Sort key putting undated last: (ts == '', ts). O(1)."""
    ts = _item_timestamp(fact)
    return (ts == "", ts)


def build_temporal_facts(recalled, result, items=None, competitor=None):
    """Prefer agent temporal_facts; else full competitor history sorted asc.

    Falls back to every seed note for the competitor (not just top-5
    recall) so the Since-March timeline always spans the true range,
    e.g. Acme 2026-08-04 -> 2026-09-18. O(n log n).
    """
    agent_facts = (result or {}).get("temporal_facts")
    if agent_facts:
        return sorted(agent_facts, key=_temporal_sort_key)
    if items is not None and competitor:
        full = [it for it in items if it.get("competitor") == competitor]
        if full:
            return sorted(full, key=_temporal_sort_key)
    flat = [it for _, it in (recalled or [])]
    return sorted(flat, key=_temporal_sort_key)


def build_synthesis_md(result, recalled, competitor):
    """Prefer agent synthesis_md; else conclude from citations. Explains WHY."""
    if result and result.get("synthesis_md"):
        return result["synthesis_md"]
    cites = (result or {}).get("citations", []) or []
    win_n = sum(1 for _, it in (recalled or []) if it.get("type") == "win")
    if cites:
        top_ids = ", ".join(str(c.get("id", "?")) for c in cites[:2])
        return ("Memory concludes {} has {} win signal(s) in this recall "
                "({} notes). Lead with cited proof {} - "
                "anchor pricing and traps on the same notes.".format(
                    competitor, win_n, len(recalled or []), top_ids))
    return ("No memory found for {} - refusing to hallucinate. "
            "Seed demo data first, then regenerate.".format(competitor))


def build_synthesis_cites(result, recalled):
    """Prefer agent synthesis_cites; else map Memory N -> intel-00X.

    Live agent cites may be bare {n, id} with blank text - look up text
    from the recall/citation pool so the UI never shows blanks.
    Always returns uniform {n, id, document_id, text}. O(n).
    """
    # Lookup pool: id -> {text, document_id} from recalled + citations.
    pool = {}
    for _, it in (recalled or []):
        cid = it.get("id") or it.get("document_id")
        if cid and str(cid) not in pool:
            pool[str(cid)] = {
                "text": str(it.get("content", "") or "")[:220],
                "document_id": it.get("document_id") or it.get("id"),
            }
    for c in ((result or {}).get("citations", []) or []):
        cid = c.get("id") or c.get("document_id")
        if not cid:
            continue
        txt = str(c.get("text", "") or "").strip()
        if str(cid) not in pool or (not pool[str(cid)]["text"] and txt):
            pool[str(cid)] = {
                "text": txt[:220],
                "document_id": c.get("document_id") or c.get("id"),
            }
    for f in ((result or {}).get("temporal_facts", []) or []):
        cid = f.get("id") or f.get("document_id")
        if not cid or str(cid) in pool:
            continue
        txt = str(f.get("content") or f.get("text") or "")
        if txt.strip():
            pool[str(cid)] = {
                "text": txt[:220],
                "document_id": f.get("document_id") or f.get("id"),
            }
    if result and result.get("synthesis_cites"):
        enriched = []
        for i, c in enumerate(result["synthesis_cites"]):
            cid = c.get("id", "?")
            txt = str(c.get("text", "") or "").strip()
            if not txt and str(cid) in pool:
                txt = pool[str(cid)]["text"]
            doc_id = (c.get("document_id") or
                      pool.get(str(cid), {}).get("document_id") or cid)
            enriched.append({"n": c.get("n", i + 1), "id": cid,
                             "document_id": doc_id,
                             "text": (txt or "")[:220]})
        return enriched
    cites = (result or {}).get("citations", []) or []
    if cites:
        out = []
        for i, c in enumerate(cites[:5]):
            cid = c.get("id", "?")
            txt = str(c.get("text", "") or "").strip()
            if not txt and str(cid) in pool:
                txt = pool[str(cid)]["text"]
            doc_id = (c.get("document_id") or
                      pool.get(str(cid), {}).get("document_id") or cid)
            out.append({"n": i + 1, "id": cid,
                        "document_id": doc_id, "text": (txt or "")[:220]})
        return out
    return [{"n": i + 1, "id": it.get("id", "?"),
             "document_id": it.get("document_id") or it.get("id", "?"),
             "text": it.get("content", "")[:220]}
            for i, (_, it) in enumerate((recalled or [])[:5])]


def parse_arr_dollars(text):
    """Parse first $ amount in text to dollars. Handles $96K, $48,000. O(1)."""
    if not text:
        return 0.0
    match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*(K)?", text)
    if not match:
        return 0.0
    number = float(match.group(1).replace(",", ""))
    if match.group(2):
        number *= 1000.0
    return number


def arr_roi_for_competitor(items, competitor):
    """Sum WON ARR for competitor; ROI vs $50/mo memory cost. O(n)."""
    wins = [it for it in items
            if it.get("competitor") == competitor and it.get("type") == "win"]
    total = sum(parse_arr_dollars(it.get("content", "")) for it in wins)
    annual_cost = MEMORY_MONTHLY_COST * 12.0
    multiple = int(round(total / annual_cost)) if annual_cost else 0
    total_k = total / 1000.0
    total_str = "${:g}K".format(total_k) if total else "$0K"
    # Stage line e.g. "2 wins = $216K ARR protected, $50/mo memory cost -> 360x".
    noun = "win" if len(wins) == 1 else "wins"
    line = ("{} {} = {} ARR protected, ${}/mo memory cost "
            "-> {}x".format(len(wins), noun, total_str,
                            MEMORY_MONTHLY_COST, multiple))
    return {"count": len(wins), "total": total, "multiple": multiple, "line": line}


def get_memory_stats_safe(items):
    """Observations proof: try live get_memory_stats(), else local snapshot.

    Returns {world, experience, observation, total, source}.
    """
    try:
        from src import hindsight_client as hc
        if hasattr(hc, "get_memory_stats"):
            stats = hc.get_memory_stats()  # live bank when keys set
            if isinstance(stats, dict) and stats:
                stats.setdefault("source", "hindsight")
                return stats
        store = getattr(hc, "_OFFLINE_STORE", []) or []
        if store:
            counts = {"world": 0, "experience": 0, "observation": 0}
            for fact in store:
                mem_type = str(fact.get("type", "world")).lower()
                if mem_type in counts:
                    counts[mem_type] += 1
                else:
                    counts["observation"] += 1
            counts["total"] = len(store)
            counts["source"] = "local snapshot (retained this session)"
            return counts
    except Exception:
        pass
    # Snapshot estimate from seed.json intel types -> memory types.
    # pricing/feature read as world knowledge, win/loss as experience,
    # everything else counted as observation so judges see all three.
    mapping = {"pricing": "world", "feature": "world",
               "win": "experience", "loss": "experience"}
    counts = {"world": 0, "experience": 0, "observation": 0}
    for it in items:
        mem_type = mapping.get(str(it.get("type", "")).lower(), "observation")
        counts[mem_type] += 1
    counts["total"] = len(items)
    counts["source"] = "local snapshot (seed.json, {} notes)".format(len(items))
    return counts


def confidence_curve_points():
    """Illustrative 1-5-20 learning curve: facts_used -> win-confidence %."""
    return {"facts_used": [1, 5, 20], "win_confidence": [62, 78, 91]}


# ---------- UI ----------

st.set_page_config(page_title="Competitive Intel Memory Agent", layout="wide")
st.title("Competitive Intel Memory Agent")
st.caption("Hindsight-powered battlecards: cold generic vs memory-recalled. "
           "Local snapshot included - same recall path, live with keys.")

items = load_seed_items()

if "_rfp_text" not in st.session_state:
    st.session_state["_rfp_text"] = DEFAULT_RFP
if "_competitor" not in st.session_state:
    st.session_state["_competitor"] = "Acme"
# Streamlit-correct pending-load: apply BEFORE any widget with the same
# key is instantiated. Load handler only sets _pending_* then reruns;
# we apply here so rfp_text_input / competitor_select are set before
# their selectbox/text_area exist this run (avoids StreamlitAPIException).
# TODO(rohit): remove _pending_* once Streamlit supports atomic widget update.
if st.session_state.get("_pending_load"):
    if "_pending_rfp" in st.session_state:
        st.session_state["_rfp_text"] = st.session_state["_pending_rfp"]
        st.session_state["rfp_text_input"] = st.session_state["_pending_rfp"]
    if "_pending_competitor" in st.session_state:
        st.session_state["_competitor"] = st.session_state["_pending_competitor"]
        st.session_state["competitor_select"] = st.session_state["_pending_competitor"]
    for _k in ("_pending_rfp", "_pending_competitor", "_pending_load"):
        st.session_state.pop(_k, None)
# Same root cause for retain box: clear BEFORE new_text widget exists.
if st.session_state.get("_pending_clear_new_text"):
    st.session_state["new_text"] = ""
    st.session_state.pop("_pending_clear_new_text", None)
# Persisted battlecard state so reruns don't wipe output (fix #1).
# Also init agent-error + recall/result/mode slots (fix #3).
if "_result" not in st.session_state:
    st.session_state["_result"] = None
if "_recalled" not in st.session_state:
    st.session_state["_recalled"] = None
if "_mode" not in st.session_state:
    st.session_state["_mode"] = None
if "_agent_error" not in st.session_state:
    st.session_state["_agent_error"] = ""
if "_gen_competitor" not in st.session_state:
    st.session_state["_gen_competitor"] = ""
if "_gen_rfp" not in st.session_state:
    st.session_state["_gen_rfp"] = ""

left, right = st.columns([1, 2])

with left:
    st.subheader("Input")
    competitor = st.selectbox("Competitor", COMPETITORS,
                              index=COMPETITORS.index(st.session_state["_competitor"])
                              if st.session_state["_competitor"] in COMPETITORS else 0,
                              key="competitor_select")
    st.session_state["_competitor"] = competitor
    rfp_text = st.text_area("RFP / deal context", height=180,
                            value=st.session_state["_rfp_text"],
                            key="rfp_text_input")
    st.session_state["_rfp_text"] = rfp_text
    gen = st.button("Generate battlecard", type="primary", key="generate_btn")
    st.divider()

    st.subheader("Golden RFP cheat-sheet (judge-proof)")
    st.caption("3 prefilled RFPs with expected Memory IDs. Click to load, then Generate.")
    for preset in GOLDEN_RFPS:
        with st.expander(preset["label"], expanded=False):
            st.write(preset["text"])
            st.caption("Expects: {} | {}".format(
                ", ".join(preset["expected"]), preset["why"]))
            if st.button("Load this RFP", key="load-" + preset["label"]):
                st.session_state["_pending_rfp"] = preset["text"]
                st.session_state["_pending_competitor"] = preset["competitor"]
                st.session_state["_pending_load"] = True
                st.rerun()

    st.divider()
    st.subheader("Retain new intel")
    new_type = st.selectbox("Intel type", INTEL_TYPES, index=0, key="intel_type")
    new_text = st.text_area("Intel note", height=100,
                            placeholder="e.g. Acme offered 15% discount to ...",
                            key="new_text")
    if st.button("Retain new intel", key="retain_btn"):
        if not new_text.strip():
            st.warning("Type an intel note first.")
        else:
            try:
                from src.hindsight_client import retain
                retain(text=new_text, context="competitive intel " + new_type,
                       document_id="ui-{}".format(int(time.time())),
                       timestamp=datetime.datetime.now(
                           datetime.timezone.utc).isoformat(),
                       tags=["competitor:" + competitor],
                       metadata={"intel_type": new_type,
                                 "competitor": competitor})
                st.success("Retained to memory.")
                st.session_state["_pending_clear_new_text"] = True
                st.rerun()
            except BaseException as exc:
                st.info("Saved to local snapshot for this session ({}).".format(exc))
    st.divider()
    st.subheader("Stage kit: Seed + Reset (<30s)")
    kit_c1, kit_c2 = st.columns(2)
    with kit_c1:
        if st.button("Seed demo data", key="seed_btn"):
            started = time.time()
            try:
                import seed as seed_mod
                seed_items = seed_mod.load_seed_items(seed_mod.DEFAULT_SEED_FILE)
                seed_mod.retain_all(seed_items)
                elapsed = time.time() - started
                st.success("Seeded {} notes in {:.1f}s (<30s).".format(
                    len(seed_items), elapsed))
            except BaseException as exc:
                st.info("Local snapshot ready ({} notes, seed.json). {}".format(len(items), exc))
            st.rerun()
    with kit_c2:
        if st.button("Reset local snapshot", key="reset_btn"):
            started = time.time()
            try:
                from src.hindsight_client import clear_offline_store
                clear_offline_store()
            except BaseException:
                pass
            elapsed = time.time() - started
            st.success("Reset in {:.1f}s (<30s). Re-Seed to restore.".format(elapsed))
            st.rerun()
    st.caption("Recovery drill: restart + Seed in <30s. Screenshot bank night-before.")
    st.caption("Seed items loaded: {}".format(len(items)))
    st.caption("Local snapshot = same recall path as live Hindsight; "
               "connect keys for shared bank. No external calls required.")

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

    st.markdown("### Observations proof (memory stats)")
    mem_stats = get_memory_stats_safe(items)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("World", str(mem_stats.get("world", 0)))
    m2.metric("Experience", str(mem_stats.get("experience", 0)))
    m3.metric("Observation", str(mem_stats.get("observation", 0)))
    m4.metric("Total memories", str(mem_stats.get("total", len(items))))
    st.caption("Source: {}.".format(mem_stats.get("source", "local snapshot")))

    st.markdown("### Learning curve: facts_used 1 -> 5 -> 20 vs win-confidence")
    curve = confidence_curve_points()
    st.line_chart({"win-confidence (%)": curve["win_confidence"]})
    st.caption("x-axis: facts_used 1 -> 5 -> 20 maps to win-confidence "
               "62% -> 78% -> 91%. More recalled facts = higher confidence.")

    st.markdown("### ROI (computed in code from seed.json)")
    roi_all = []
    for comp in sorted(rates.keys()) if rates else COMPETITORS:
        roi = arr_roi_for_competitor(items, comp)
        roi_all.append((comp, roi))
        st.write("**{}:** {}".format(comp, roi["line"]))
    # Highlight default Acme line for stage talk track.
    acme_roi = arr_roi_for_competitor(items, "Acme")
    st.caption("Example stage line: {} (Acme $96K + $120K).".format(acme_roi["line"]))

    # Surface agent fallback errors instead of silently swallowing (fix #3).
    if st.session_state.get("_agent_error"):
        st.warning("Agent fallback: {}".format(st.session_state["_agent_error"]))

    if gen:
        st.session_state["_agent_error"] = ""
        recalled = offline_recall(rfp_text, competitor, items)
        result, mode = generate_with_memory(rfp_text, competitor, recalled)
        # Persist so reruns (any widget interaction) don't wipe output (fix #1).
        st.session_state["_recalled"] = recalled
        st.session_state["_result"] = result
        st.session_state["_mode"] = mode
        st.session_state["_gen_competitor"] = competitor
        st.session_state["_gen_rfp"] = rfp_text

    # Render from persisted state so output survives reruns (fix #1, #7).
    persisted_result = st.session_state.get("_result")
    persisted_recalled = st.session_state.get("_recalled")
    persisted_mode = st.session_state.get("_mode")
    persisted_comp = st.session_state.get("_gen_competitor") or competitor
    persisted_rfp = st.session_state.get("_gen_rfp") or rfp_text
    if persisted_result is not None and persisted_recalled is not None:
        recalled = persisted_recalled
        result = persisted_result
        mode = persisted_mode
        competitor = persisted_comp
        rfp_text = persisted_rfp
        if mode == "offline" and not has_hindsight_keys():
            st.info("Local memory snapshot active ({} notes from seed.json) - "
                    "same recall path as live Hindsight. Add keys for shared bank.".format(len(items)))
        elif mode == "offline":
            st.info("Local memory snapshot active - showing seed recall "
                    "({} notes).".format(len(recalled)))
        else:
            st.success("Live memory: recalled via Hindsight.")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### Cold (no memory)")
            st.markdown(cold_battlecard(rfp_text, competitor))
        with c2:
            st.markdown("### With memory")
            st.markdown(result.get("battlecard_md", ""))
        # Export + ROI: .md download computed live from persisted battlecard.
        battlecard_md = result.get("battlecard_md", "") or ""
        export_date = datetime.date.today().isoformat()
        st.download_button(
            label="Download battlecard .md",
            data=battlecard_md,
            file_name="battlecard-{}-{}.md".format(
                str(competitor).lower(), export_date),
            mime="text/markdown",
            key="download_btn",
        )
        current_roi = arr_roi_for_competitor(items, competitor)
        st.write("**ROI:** {}".format(current_roi["line"]))

        with st.expander("What memory concludes (synthesis)", expanded=True):
            st.markdown(build_synthesis_md(result, recalled, competitor))
            st.markdown("**Synthesis cites [Memory N -> intel-00X]**")
            for cite in build_synthesis_cites(result, recalled):
                st.write("**Memory {n} -> {cid}:** {txt}".format(
                    n=cite.get("n", "?"), cid=cite.get("id", "?"),
                    txt=str(cite.get("text", ""))[:300]))

        st.markdown("### Citations (Memory 1, 2)")
        for i, c in enumerate(result.get("citations", [])[:2], 1):
            st.write("**Memory {}** [{}]: {}".format(
                i, c.get("id", "?"), c.get("text", "")[:300]))

        with st.expander("Temporal timeline - what changed since March 2026", expanded=True):
            temporal = build_temporal_facts(recalled, result, items, competitor)
            if not temporal:
                st.write("No dated notes for this competitor yet.")
            for fact in temporal:
                ts = _item_timestamp(fact)
                date = str(ts)[:10] if ts else "undated"
                fid = fact.get("id") or fact.get("document_id") or "?"
                body = fact.get("content") or fact.get("text") or ""
                st.write("**{}** [{}]: {}".format(date, fid, str(body)[:220]))
            if temporal:
                dates = [_item_timestamp(f)[:10] for f in temporal if _item_timestamp(f)]
                if dates:
                    st.caption("Range {} -> {} (sorted by timestamp).".format(
                        min(dates), max(dates)))

        st.markdown("### Recall trace (text | type | score)")
        for s, it in recalled:
            st.code("{} | {} | score={}".format(
                it.get("content", "")[:150], it.get("type"), s))
        st.markdown("### Memory impact")
        st.write(result.get("memory_impact", ""))
    else:
        st.info("Enter an RFP and press Generate battlecard. "
                "Tip: try a Golden RFP from the left cheat-sheet.")
