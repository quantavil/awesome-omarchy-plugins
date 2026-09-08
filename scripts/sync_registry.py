#!/usr/bin/env python3
"""Re-sync plugins.json skeleton from the upstream marketplace registry.

Fetches the latest registry.json, rebuilds plugin entries (new plugins get
added, retired IDs get flagged), and preserves already-enriched live stats
(stars, forks, descriptions) for entries that still exist upstream.

Usage:
    uv run python scripts/sync_registry.py [--registry-url URL] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import (  # noqa: E402
    REGISTRY_URL,
    load_plugins,
    normalize_plugin_entry,
    save_plugins_atomic,
)


def fetch_registry(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Awesome-Omarchy-Plugins-Sync/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def build_skeleton(registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for source in registry.get("sources", []):
        repo_url = source["repo"].rstrip("/")
        parts = urlparse(repo_url).path.strip("/").split("/")
        owner, repo = (parts[0], parts[1].removesuffix(".git")) if len(parts) >= 2 else ("?", "?")
        added = source.get("addedAt", "")
        stype = source.get("type", "plugin-source")
        if stype == "suite" and "catalog" in source:
            catalog = source["catalog"]
            entries.append(
                normalize_plugin_entry(
                    {
                        "plugin_id": catalog.get("id", repo),
                        "owner": owner,
                        "repo": repo,
                        "name": catalog.get("name", repo),
                        "description": catalog.get("description", ""),
                        "category": catalog.get("category", "Other"),
                        "tags": catalog.get("tags", []),
                        "repo_url": repo_url,
                        "type": "suite",
                        "author": catalog.get("author", ""),
                        "version": catalog.get("version", ""),
                        "addedAt": added,
                    }
                )
            )
        else:
            for pid, plugin in source.get("plugins", {}).items():
                entries.append(
                    normalize_plugin_entry(
                        {
                            "plugin_id": pid,
                            "owner": owner,
                            "repo": repo,
                            "name": plugin.get("name") or pid or repo,
                            "description": plugin.get("description", ""),
                            "category": plugin.get("category", "Other"),
                            "tags": plugin.get("tags", []),
                            "repo_url": repo_url,
                            "type": "plugin-source",
                            "addedAt": added,
                        }
                    )
                )
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync catalog skeleton from upstream registry")
    parser.add_argument("--registry-url", default=REGISTRY_URL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching registry from {args.registry_url} ...")
    registry = fetch_registry(args.registry_url)
    skeleton = build_skeleton(registry)

    if not skeleton:
        print("Error: Upstream registry returned empty skeleton! Aborting sync.", file=sys.stderr)
        sys.exit(1)

    print(f"Upstream: {len(registry.get('sources', []))} sources -> {len(skeleton)} plugin entries")

    existing: Dict[str, Dict[str, Any]] = {p["plugin_id"]: p for p in load_plugins()}

    # Safety check: prevent catastrophic catalog truncation on upstream outages
    if existing and len(skeleton) < len(existing) * 0.7:
        print(
            f"Error: Upstream returned {len(skeleton)} entries, "
            f"but existing catalog has {len(existing)}! "
            "Aborting sync to prevent catalog destruction.",
            file=sys.stderr,
        )
        sys.exit(1)

    preserved = added = 0
    merged: List[Dict[str, Any]] = []
    for entry in skeleton:
        old = existing.get(entry["plugin_id"])
        # Preserve enriched stats if valid last_updated, description, or stars exist
        is_enriched = old and (
            old.get("last_updated") not in (None, "", "N/A")
            or old.get("stars", 0) > 0
            or old.get("description")
        )
        if is_enriched:
            for key in (
                "stars",
                "forks",
                "last_updated",
                "updated_at",
                "license",
                "language",
                "archived",
                "dead",
                "open_issues",
                "default_branch",
                "description",
                "name",
                "category",
                "tags",
            ):
                if old.get(key) not in (None, "", 0, "N/A", "Unknown", []):
                    entry[key] = old[key]
                elif key in ("description", "name") and old.get(key):
                    entry[key] = old[key]
            preserved += 1
        else:
            added += 1
        merged.append(entry)

    retired = [pid for pid in existing if pid not in {e["plugin_id"] for e in skeleton}]
    print(
        f"Preserved stats for {preserved} entries, "
        f"{added} new/unenriched, {len(retired)} removed upstream"
    )
    if retired[:20]:
        print("Removed upstream (dropped from catalog):")
        for pid in retired[:20]:
            print(f"  - {pid}")

    if args.dry_run:
        print("Dry run: plugins.json not modified.")
        return

    save_plugins_atomic(merged)
    print(f"Saved {len(merged)} entries to plugins.json.")
    print("Next: run `uv run python src/update_stats.py` to enrich new entries with live stats.")


if __name__ == "__main__":
    main()
