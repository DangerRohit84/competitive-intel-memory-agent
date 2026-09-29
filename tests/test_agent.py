"""TDD tests for Competitive Intel Memory Agent.

3 required tests:
1. recall returns facts
2. battlecard cites Memory IDs
3. no-match says honestly no memory

All tests run OFFLINE (no HINDSIGHT_*/GROQ_* keys needed) via mocks + in-memory fallback.
Run: pytest tests/test_agent.py -v
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# --- 1. recall returns facts (offline fallback path) ---
def test_recall_returns_facts():
    """retain() then recall() must return structured facts with id/text."""
    from src import hindsight_client as hc

    # Force offline mode: no live client
    with patch.object(hc, "_get_live_client", return_value=None):
        hc.clear_offline_store()
        hc.retain(
            content="Acme quoted $48,000/yr for 200 seats, undercutting us by 12%.",
            context="competitive intel pricing",
            timestamp="2026-08-04T10:30:00Z",
            document_id="test-intel-001",
            tags=["competitor:Acme", "pricing"],
            metadata={"deal_id": "deal-test-001"},
        )
        facts = hc.recall(
            query="Acme pricing",
            tags=["competitor:Acme"],
            budget="mid",
            max_tokens=2000,
            types=["world", "experience", "observation"],
        )
    assert isinstance(facts, list), "recall must return a list"
    assert len(facts) >= 1, "recall must return at least 1 fact after retain"
    f0 = facts[0]
    assert "id" in f0 and "text" in f0, "fact must have id + text, got: {}".format(f0)
    assert "Acme" in f0["text"] or "48000" in f0["text"] or "$48" in f0["text"]


# --- 2. battlecard cites Memory IDs ---
def test_battlecard_cites_memory_ids():
    """generate_battlecard must ground on recalled facts with [Memory N] citations."""
    from src import agent as ag

    fake_facts = [
        {
            "id": "intel-001",
            "text": "Acme quoted $48,000/yr for 200 seats, undercutting us by 12%.",
            "type": "world",
            "tags": ["competitor:Acme"],
            "document_id": "intel-001",
        },
        {
            "id": "intel-003",
            "text": "WON vs Acme at Globex ($96K ARR) on audit retention + India data residency.",
            "type": "experience",
            "tags": ["competitor:Acme"],
            "document_id": "intel-003",
        },
    ]
    fake_llm_md = (
        "# Battlecard: Acme\n\n"
        "## Pricing\nAcme at $48K/yr undercuts us [Memory 1].\n\n"
        "## How we win\nWon on audit retention + residency [Memory 2].\n"
    )
    with patch.object(ag, "recall_for_battlecard", return_value=fake_facts):
        with patch.object(ag, "_groq_complete", return_value=fake_llm_md):
            result = ag.generate_battlecard(
                rfp_text="Need 200 seats, audit logs, India hosting.",
                competitor="Acme",
            )
    assert isinstance(result, dict)
    assert "battlecard_md" in result and "citations" in result and "memory_impact" in result
    assert "[Memory 1]" in result["battlecard_md"], "must cite Memory 1"
    assert "[Memory 2]" in result["battlecard_md"], "must cite Memory 2"
    assert len(result["citations"]) >= 2, "citations list must map Memory N -> fact id"
    ids = [c.get("id") for c in result["citations"]]
    assert "intel-001" in ids and "intel-003" in ids


# --- 3. no-match says honestly no memory ---
def test_no_match_says_honestly_no_memory():
    """Empty recall must produce honest 'no memory' message, not hallucinated battlecard."""
    from src import agent as ag

    with patch.object(ag, "recall_for_battlecard", return_value=[]):
        result = ag.generate_battlecard(
            rfp_text="Need 200 seats.",
            competitor="UnknownCo",
        )
    assert isinstance(result, dict)
    assert result["citations"] == [], "no facts -> empty citations"
    assert "no memory" in result["battlecard_md"].lower(), (
        "must honestly say no memory, got: {}".format(result["battlecard_md"][:200])
    )
    # must not contain fake citations
    assert "[Memory 1]" not in result["battlecard_md"]
