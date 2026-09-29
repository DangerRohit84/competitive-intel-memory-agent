"""Regression test for StreamlitAPIException widget-state fix.

Root cause: Load handler set st.session_state["rfp_text_input"] AFTER
text_area with same key was already instantiated this run - Streamlit
forbids modifying widget state after instantiation.

Fix: pending pattern - handler only sets _pending_* then reruns;
top init section applies pending BEFORE widget creation.

Run: pytest tests/test_streamlit_state.py -v
"""
import os


def _source():
    path = os.path.join(os.path.dirname(__file__), "..", "streamlit_app.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_load_handler_uses_pending_pattern():
    """load_handler_stateUnderTest_usesPendingNotDirectWidgetWrite."""
    src = _source()
    # Handler must set pending keys + rerun.
    assert '_pending_rfp' in src, "missing _pending_rfp"
    assert '_pending_competitor' in src, "missing _pending_competitor"
    assert '_pending_load' in src, "missing _pending_load flag"
    # Handler block must NOT directly touch widget keys.
    # Isolate the Load-button handler (between 'Load this RFP' and next st.divider).
    start = src.index("Load this RFP")
    handler = src[start:start + 800]
    assert 'rfp_text_input"] =' not in handler, (
        "load handler must NOT write rfp_text_input directly: {}".format(handler[:400])
    )
    assert 'competitor_select"] =' not in handler, (
        "load handler must NOT write competitor_select directly"
    )


def test_pending_applied_before_widgets():
    """pendingApply_beforeInstantiation_setsWidgetStateEarly."""
    src = _source()
    pending_idx = src.index('st.session_state.get("_pending_load")')
    selectbox_idx = src.index('key="competitor_select"')
    textarea_idx = src.index('key="rfp_text_input"')
    assert pending_idx < selectbox_idx < textarea_idx, (
        "pending-apply must come BEFORE widget instantiation "
        f"(pending={pending_idx}, select={selectbox_idx}, text={textarea_idx})"
    )
    # Pending block must set widget keys + clear pending.
    block = src[pending_idx:pending_idx + 1200]
    assert 'st.session_state["rfp_text_input"]' in block
    assert 'st.session_state["competitor_select"]' in block
    assert '_pending_rfp' in block and 'pop(' in block


def test_generate_persistence_kept():
    """generatePersistence_kept_afterLoadFix."""
    src = _source()
    for key in ('"_result"', '"_recalled"', '"_mode"', '"_gen_competitor"', '"_gen_rfp"'):
        assert key in src, f"Generate persistence key {key} must be kept"
