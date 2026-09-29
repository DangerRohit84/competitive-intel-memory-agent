"""Seed competitive intel into Hindsight memory.

Usage:
    python seed.py              # retain all items from seed.json
    python seed.py --dry-run    # print what would be retained
    python seed.py --seed-file custom.json --dry-run

Each item is retained with:
    context="competitive intel <type>"
    document_id=<stable id from seed.json>
    tags=["competitor:<Name>"] + item tags
"""

import argparse
import json
import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

DEFAULT_SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed.json")


def load_seed_items(seed_file):
    with open(seed_file, "r", encoding="utf-8") as f:
        items = json.load(f)
    return items


def build_retain_kwargs(item):
    """Build kwargs for hindsight retain call. Single place for mapping."""
    competitor = item.get("competitor", "Unknown")
    # Canonical tag is lowercase (agent _competitor_tag matches); display
    # case preserved in metadata.competitor.
    display = str(competitor or "Unknown").strip() or "Unknown"
    intel_type = item.get("type", "general")
    tags = ["competitor:{}".format(display.lower())]
    for t in item.get("tags", []):
        tags.append(t)
    return {
        "text": item.get("content", ""),
        "context": "competitive intel {}".format(intel_type),
        "document_id": item.get("id"),
        "timestamp": item.get("timestamp"),
        "tags": tags,
        "metadata": {
            "deal_id": item.get("deal_id"),
            "timestamp": item.get("timestamp"),
            "intel_type": intel_type,
            "competitor": display,
        },
    }


def retain_all(items, dry_run=False):
    results = []
    if dry_run:
        for item in items:
            kwargs = build_retain_kwargs(item)
            print("[DRY-RUN] retain document_id={} context={!r} tags={}".format(
                kwargs["document_id"], kwargs["context"], kwargs["tags"]))
            print("          text: {}...".format(kwargs["text"][:120]))
            results.append({"id": kwargs["document_id"], "dry_run": True})
        print("\n[DRY-RUN] Would retain {} items. No writes performed.".format(len(items)))
        return results

    try:
        from src.hindsight_client import retain
    except Exception as exc:
        print("ERROR: could not import src.hindsight_client.retain: {}".format(exc),
              file=sys.stderr)
        print("HINT: copy .env.example to .env and set HINDSIGHT_* keys, "
              "or run with --dry-run for offline check.", file=sys.stderr)
        sys.exit(1)

    for item in items:
        kwargs = build_retain_kwargs(item)
        try:
            res = retain(**kwargs)
            print("OK retained {} ({})".format(kwargs["document_id"], kwargs["context"]))
            results.append({"id": kwargs["document_id"], "ok": True, "result": str(res)})
        except Exception as exc:
            print("FAIL {} : {}".format(kwargs["document_id"], exc), file=sys.stderr)
            results.append({"id": kwargs["document_id"], "ok": False, "error": str(exc)})
    ok = sum(1 for r in results if r.get("ok"))
    print("\nRetained {}/{} items.".format(ok, len(items)))
    return results


def main():
    parser = argparse.ArgumentParser(description="Seed competitive intel to Hindsight")
    parser.add_argument("--seed-file", default=DEFAULT_SEED_FILE)
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be retained without calling Hindsight")
    args = parser.parse_args()

    if not os.path.exists(args.seed_file):
        print("Seed file not found: {}".format(args.seed_file), file=sys.stderr)
        sys.exit(1)
    items = load_seed_items(args.seed_file)
    print("Loaded {} items from {}".format(len(items), args.seed_file))
    retain_all(items, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
