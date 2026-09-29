# Plan: Competitive Intel Memory Agent CORE
Date: 2026-09-29 | Spec: docs/superpowers/specs/2026-09-29-competitive-intel-design.md

## Objective
Ship senior-dev CORE (Hindsight wrapper + battlecard agent + tests) for HackwithHyderabad 3.0 demo.

## Steps (TDD: RED → GREEN → REFACTOR)
1. [x] Config: requirements.txt, .env.example, .gitignore, spec+plan docs
2. [ ] RED: tests/test_agent.py — 3 failing tests (recall returns facts, battlecard cites Memory IDs, no-match honest)
3. [ ] GREEN: src/hindsight_client.py wrapper (offline fallback, tags_match any_strict, stable doc_id, context always)
4. [ ] GREEN: src/agent.py generate_battlecard (3-way recall, Groq grounded, CODE win-rate, {battlecard_md, citations, memory_impact})
5. [ ] Verify: pytest -v green, seed.py --dry-run 18 items, git commit

## Out of scope (teammate owns)
- streamlit_app.py, seed.json edits
