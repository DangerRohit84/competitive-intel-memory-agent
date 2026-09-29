# Competitive Intel Memory Agent — Design Spec
Date: 2026-09-29 | HackwithHyderabad 3.0 | Owner: senior-dev (CORE) + teammate (Streamlit UI)

## 1. Goal
AI that remembers competitor moves over months + past win/loss, outputs **cited battlecard**.
Demo: RFP text + competitor → battlecard with `[Memory 1]`, `[Memory 2]` citations, win-rate computed in CODE.

## 2. Architecture
```
seed.json (18 items) --retain--> Hindsight Cloud Bank `competitive-intel`
        context="competitive intel <type>" | document_id=<stable id> | tags=["competitor:<Name>", ...]
        timestamp=backdated ISO (from seed.json) — enables "what changed since March?" temporal recall

RFP text + competitor --3-way recall--> Hindsight --grounded Groq chat--> battlecard.md
  recall-1 factual: "Acme pricing features"
  recall-2 temporal: "What changed for Acme since March 2026?"
  recall-3 battle: "Acme win loss battlecard objections strengths weaknesses"
  all with tags=["competitor:<Name>"], tags_match="any_strict", budget="mid", max_tokens=2000,
  types=["world","experience","observation"]

Groq: chat model qwen/qwen3-32b (fast, cheap); Hindsight backend openai/gpt-oss-120b.
Win-rate: CODE counts type==win/loss in seed records for competitor — NEVER LLM math.
No-match: honestly say "No memory found for <competitor>" — no hallucination.
```

## 3. Hindsight patterns (LOCKED)
- **context always**: retain without context degrades extraction ("project terminated" means different things in perf-review vs roadmap).
- **stable document_id**: upsert semantics — re-retain same id replaces, no dupes. seed.json `id` is the key.
- **tags**: `competitor:<Name>` + domain tags; recall with `tags_match="any_strict"` (matching tagged only, excludes untagged/global).
- **backdated timestamps**: pass seed `timestamp` as retain `timestamp` so temporal queries work; else LLM anchors to now.
- **async consolidation lag**: retain is end-of-turn, recall is next-turn. Observations consolidate async — immediate recall may miss `observation` type. Mitigation: `recall_with_retry()` polls 3x with delay; seed script runs before demo, not during.
- **TPM errors (Groq 250K TPM)**: catch 429, backoff + reduce max_tokens, fallback to offline template.
- **offline fallback**: in-memory store with keyword match so tests/demo pass without keys.

## 4. Interfaces
- `src/hindsight_client.py`: `retain(content|text, context, timestamp, document_id, tags, metadata)`, `recall(query, tags, budget='mid', max_tokens=2000, types=[...])`, `reflect(query, tags, include_facts=True)`. Module-level funcs (for seed.py) + `HindsightMemoryClient` class.
- `src/agent.py`: `generate_battlecard(rfp_text, competitor) -> {battlecard_md, citations, memory_impact}`.
- `seed.json`: list of {id, competitor, type, content, timestamp, deal_id, tags}. 18 items (Acme/Nova/Vertex x6).
- `streamlit_app.py` (teammate, DO NOT TOUCH): imports `generate_battlecard`, renders markdown + citations.

## 5. Verification
- `pytest -v` 3 tests green. `python seed.py --dry-run` shows 18 retains. Live proof: retain 1 doc → recall finds it (or offline fallback proof when no keys).
