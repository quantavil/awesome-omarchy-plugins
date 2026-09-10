#!/usr/bin/env python3
"""Awesome Omarchy Plugins - Catalog Generator and Stats Updater.

Reads rich plugin data (plugins.json), optionally refreshes live stats
(stars, forks, last updated) from GitHub, and updates README.md and BY_UPDATED.md.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from rich.console import Console

# Ensure src is in python path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils import (  # noqa: E402
    BY_UPDATED_PATH,
    PLUGIN_CATEGORIES,
    README_PATH,
    atomic_write_text,
    format_count,
    get_github_headers,
    get_github_token,
    github_slug,
    is_dead,
    is_excluded,
    load_plugins,
    normalize_plugin_entry,
    save_plugins_atomic,
)

console = Console(stderr=True)

START_MARKER = "<!-- PLUGINS_LIST_START -->"
END_MARKER = "<!-- PLUGINS_LIST_END -->"
COUNT_MARKER_REGEX = r"<!-- TOTAL_PLUGINS_COUNT -->.*?<!-- /TOTAL_PLUGINS_COUNT -->"
UPDATED_MARKER_REGEX = r"<!-- LAST_UPDATED -->.*?<!-- /LAST_UPDATED -->"

REPO_TELEMETRY_KEYS = {
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
}


class RateLimitCircuitBreaker:
    """Thread-safe / task-safe rate-limit trip detector to avoid slamming GitHub API."""

    def __init__(self) -> None:
        self.tripped = False
        self.reset_timestamp = 0
        self.reason = ""

    def trip(self, reset_ts: int, reason: str = "Rate limit reached") -> None:
        self.tripped = True
        self.reset_timestamp = reset_ts
        self.reason = reason


circuit_breaker = RateLimitCircuitBreaker()


def _fetch_chunk(
    client: httpx.Client,
    chunk: List[str],
    unique_repos: Dict[str, Dict[str, Any]],
    headers: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:
    """Fetch metadata for a single batch of repositories via GitHub GraphQL."""
    query_lines = ["query BatchStats {"]
    for idx, k in enumerate(chunk):
        info = unique_repos[k]
        o = info["owner"]
        r = info["repo"]
        query_lines.append(f"""
          r{idx}: repository(owner: "{o}", name: "{r}") {{
            stargazerCount
            forkCount
            pushedAt
            isArchived
            primaryLanguage {{ name }}
            licenseInfo {{ spdxId }}
            defaultBranchRef {{ name }}
            openIssues: issues(states: OPEN) {{ totalCount }}
            description
          }}
        """)
    query_lines.append("}")
    query_str = "\n".join(query_lines)

    resp = client.post(
        "https://api.github.com/graphql",
        json={"query": query_str},
        headers=headers,
    )

    if resp.status_code in (403, 429):
        raise httpx.HTTPStatusError(
            f"GraphQL rate limit/forbidden (HTTP {resp.status_code})",
            request=resp.request,
            response=resp,
        )

    if resp.status_code != 200:
        raise httpx.HTTPStatusError(
            f"GraphQL HTTP {resp.status_code}",
            request=resp.request,
            response=resp,
        )

    res_json = resp.json()
    data = res_json.get("data") or {}
    errors = res_json.get("errors") or []

    error_map: Dict[str, str] = {}
    for err in errors:
        err_path = err.get("path")
        if err_path and isinstance(err_path, list):
            alias = err_path[0]
            err_type = err.get("type", "")
            error_map[alias] = err_type

    chunk_stats: Dict[str, Dict[str, Any]] = {}
    for idx, k in enumerate(chunk):
        alias = f"r{idx}"
        entry_data = data.get(alias)
        cached = unique_repos[k]["cached"]
        stats = dict(cached)

        if entry_data:
            stats["stars"] = entry_data.get("stargazerCount", 0)
            stats["forks"] = entry_data.get("forkCount", 0)
            stats["archived"] = entry_data.get("isArchived", False)
            stats["dead"] = False

            lang = entry_data.get("primaryLanguage")
            stats["language"] = lang.get("name") if lang else ""

            issues = entry_data.get("openIssues")
            stats["open_issues"] = issues.get("totalCount", 0) if issues else 0

            lic = entry_data.get("licenseInfo")
            if lic and lic.get("spdxId") and lic["spdxId"] != "NOASSERTION":
                stats["license"] = lic["spdxId"]

            branch = entry_data.get("defaultBranchRef")
            if branch and branch.get("name"):
                stats["default_branch"] = branch["name"]

            pushed = entry_data.get("pushedAt")
            if pushed:
                stats["last_updated"] = pushed.split("T")[0]
                stats["updated_at"] = pushed.split("T")[0]

            if entry_data.get("description"):
                stats["repo_description"] = entry_data["description"].strip()
        elif alias in error_map:
            if error_map[alias] == "NOT_FOUND":
                stats["dead"] = True
                stats["last_updated"] = "N/A"
                console.print(
                    f"[yellow]Repo {k} is NOT_FOUND in GraphQL. Marked as abandoned.[/yellow]"
                )
            else:
                console.print(
                    f"[yellow]GraphQL error on {k}: {error_map[alias]}. "
                    "Cached stats kept.[/yellow]"
                )
        chunk_stats[k] = stats

    return chunk_stats


def fetch_catalog_stats_graphql(
    unique_repos: Dict[str, Dict[str, Any]],
    token: str,
    batch_size: int = 50,
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Fetch repository metadata using GitHub GraphQL API in batches of 50.

    Querying 50 repositories in a single GraphQL call costs only 1 rate limit point,
    allowing 2,600 repositories to be fetched in ~52 requests (~2-3 seconds total).
    """
    headers = get_github_headers(token)
    keys = list(unique_repos.keys())
    repo_stats: Dict[str, Dict[str, Any]] = {}
    successful_batches = 0
    total_batches = (len(keys) + batch_size - 1) // batch_size if keys else 0

    console.print(
        f"[bold cyan]Fetching metadata via GraphQL batching "
        f"({len(keys)} unique repos in batches of {batch_size})...[/bold cyan]"
    )

    with httpx.Client(timeout=30.0) as client:
        for i in range(0, len(keys), batch_size):
            chunk = keys[i : i + batch_size]
            batch_num = i // batch_size + 1
            chunk_stats: Optional[Dict[str, Dict[str, Any]]] = None

            for attempt in (1, 2):
                try:
                    chunk_stats = _fetch_chunk(client, chunk, unique_repos, headers)
                    break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (403, 429):
                        console.print(
                            f"[bold red]GraphQL returned HTTP {e.response.status_code}. "
                            "Aborting batching and keeping cached stats.[/bold red]"
                        )
                        for remaining_k in keys[i:]:
                            repo_stats[remaining_k] = dict(unique_repos[remaining_k]["cached"])
                        return repo_stats if successful_batches > 0 else None
                    if attempt == 1:
                        console.print(
                            f"[yellow]Batch {batch_num} attempt 1 failed ({e}). "
                            "Retrying in 2s...[/yellow]"
                        )
                        time.sleep(2.0)
                    else:
                        console.print(
                            f"[red]Batch {batch_num} failed on attempt 2 ({e}).[/red]"
                        )
                except Exception as e:
                    if attempt == 1:
                        console.print(
                            f"[yellow]Batch {batch_num} attempt 1 failed ({e}). "
                            "Retrying in 2s...[/yellow]"
                        )
                        time.sleep(2.0)
                    else:
                        console.print(
                            f"[red]Batch {batch_num} failed on attempt 2 ({e}).[/red]"
                        )

            if chunk_stats is not None:
                repo_stats.update(chunk_stats)
                successful_batches += 1
                console.print(
                    f"  [green]Batch {batch_num}/{total_batches} complete "
                    f"({min(i + batch_size, len(keys))}/{len(keys)} repos)[/green]"
                )
            else:
                console.print(
                    f"[yellow]Keeping cached stats for batch {batch_num}. Continuing...[/yellow]"
                )
                for k in chunk:
                    repo_stats[k] = dict(unique_repos[k]["cached"])

    if total_batches > 0 and successful_batches == 0:
        console.print("[red]All GraphQL batches failed. Falling back to REST.[/red]")
        return None

    return repo_stats


async def fetch_repo_stats_async(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    headers: Dict[str, str],
    sem: asyncio.Semaphore,
    cached_stats: Dict[str, Any],
) -> Dict[str, Any]:
    """Fetch repository metadata via GitHub REST API with concurrency limits and circuit breaker."""
    stats = dict(cached_stats)
    repo_url = f"https://api.github.com/repos/{owner}/{repo}"

    if circuit_breaker.tripped:
        return stats

    async with sem:
        if circuit_breaker.tripped:
            return stats
        try:
            resp = await client.get(repo_url, headers=headers, timeout=12.0, follow_redirects=True)
            if resp.status_code == 200:
                data = resp.json()
                stats["stars"] = data.get("stargazers_count", 0)
                stats["forks"] = data.get("forks_count", 0)
                stats["archived"] = data.get("archived", False)
                stats["dead"] = False
                stats["language"] = data.get("language") or ""
                stats["open_issues"] = data.get("open_issues_count", 0)

                license_info = data.get("license")
                if license_info and license_info.get("spdx_id"):
                    spdx = license_info["spdx_id"]
                    if spdx != "NOASSERTION":
                        stats["license"] = spdx

                stats["default_branch"] = data.get("default_branch", "main")
                pushed_at = data.get("pushed_at")
                if pushed_at:
                    stats["last_updated"] = pushed_at.split("T")[0]
                    stats["updated_at"] = pushed_at.split("T")[0]

                if data.get("description"):
                    stats["repo_description"] = data["description"].strip()
            elif resp.status_code in (404, 410):
                stats["dead"] = True
                stats["last_updated"] = "N/A"
                console.print(
                    f"[yellow]Repo {owner}/{repo} returned HTTP {resp.status_code} "
                    "(Not Found). Marked as abandoned.[/yellow]"
                )
            elif resp.status_code in (403, 429):
                remaining = resp.headers.get("x-ratelimit-remaining")
                reset_ts = int(resp.headers.get("x-ratelimit-reset", 0))
                if remaining == "0" or resp.status_code == 429:
                    circuit_breaker.trip(reset_ts)
                    console.print(
                        f"[bold red]Rate limit exhausted. Tripping circuit breaker until "
                        f"{reset_ts}. Cached stats kept.[/bold red]"
                    )
                else:
                    console.print(
                        f"[yellow]Rate limit / abuse detection for {owner}/{repo}. "
                        "Using cached stats.[/yellow]"
                    )
            else:
                console.print(
                    f"[yellow]Failed {owner}/{repo} (HTTP {resp.status_code}). "
                    "Cached stats kept.[/yellow]"
                )
        except Exception as e:
            console.print(f"[red]Error fetching {owner}/{repo}: {e}. Using cached stats.[/red]")

    return stats


def generate_plugin_markdown_item(plugin: Dict[str, Any]) -> str:
    """Generate Markdown bullet lines for a single plugin."""
    name = plugin.get("name", plugin["repo"])
    url = plugin.get("repo_url") or f"https://github.com/{plugin['owner']}/{plugin['repo']}"
    desc = plugin.get("description", "").strip() or "Omarchy plugin."
    stars_formatted = format_count(plugin.get("stars", 0))
    forks_formatted = format_count(plugin.get("forks", 0))
    last_updated = plugin.get("last_updated", "N/A")
    archived_badge = " *(Archived)*" if plugin.get("archived") else ""

    owner = plugin.get("owner", "").strip()
    repo = plugin.get("repo", "").strip()
    slug = f"{owner}/{repo}" if owner and repo else url
    install_cmd = f"omarchy plugin add {slug} --enable"

    meta_parts = [
        f"⭐ **{stars_formatted}**",
        f"🍴 {forks_formatted}",
        f"Last updated: `{last_updated}`",
        f"`{install_cmd}`",
    ]

    meta_line = " · ".join(meta_parts)
    return f"- **[{name}]({url})**{archived_badge} : {desc}\n  - {meta_line}"


def generate_top_authors_list(plugins: List[Dict[str, Any]], limit: int = 10) -> str:
    """Generate simple numbered list of top plugin authors by cumulative stars."""
    author_stats: Dict[str, Dict[str, int]] = {}
    for p in plugins:
        owner = (p.get("owner") or "").strip()
        if not owner or owner == "?":
            continue
        stars = int(p.get("stars", 0) or 0)
        if owner not in author_stats:
            author_stats[owner] = {"stars": 0, "plugins": 0}
        author_stats[owner]["stars"] += stars
        author_stats[owner]["plugins"] += 1

    top_authors = sorted(
        author_stats.items(),
        key=lambda x: (x[1]["stars"], x[1]["plugins"]),
        reverse=True,
    )[:limit]

    lines = ["### 🏆 Top Plugin Authors\n"]
    for rank, (owner, stats) in enumerate(top_authors, 1):
        stars_str = format_count(stats["stars"])
        plugin_word = "plugin" if stats["plugins"] == 1 else "plugins"
        profile_url = f"https://github.com/{owner}"
        lines.append(
            f"{rank}. **[@{owner}]({profile_url})** — ⭐ {stars_str} "
            f"({stats['plugins']} {plugin_word})"
        )

    return "\n".join(lines)


def generate_markdown_list(
    plugins: List[Dict[str, Any]],
    grouped: bool = True,
    include_top_authors: bool = True,
) -> str:
    """Generate Markdown representation of the plugin list, grouped by category.

    Plugins with 3+ stars are showcased directly under each category.
    New and emerging plugins (0-2 stars) are neatly accessible in an in-category
    collapsible details block, keeping browsing focused while preserving discoverability.
    """
    if not grouped:
        top_plugins = [p for p in plugins if int(p.get("stars", 0) or 0) >= 3]
        new_plugins = [p for p in plugins if int(p.get("stars", 0) or 0) < 3]
        items = [generate_plugin_markdown_item(p) for p in top_plugins]
        if new_plugins:
            new_rendered = "\n".join(generate_plugin_markdown_item(p) for p in new_plugins)
            summary_title = (
                f"🐣 New & Emerging Plugins ({len(new_plugins)} plugins · 0–2 ⭐)"
            )
            items.append(
                f"\n<details>\n"
                f"<summary><b>{summary_title}</b></summary>\n\n"
                f"{new_rendered}\n\n"
                f"</details>"
            )
        return "\n".join(items)

    toc_lines = ["### Categories\n"]
    category_map: Dict[str, List[Dict[str, Any]]] = {c: [] for c in PLUGIN_CATEGORIES}

    for plugin in plugins:
        category = plugin.get("category") or "Other"
        if category not in category_map:
            category_map[category] = []
        category_map[category].append(plugin)

    # Canonical categories in predefined order, then any custom ones alphabetically.
    canonical_active = [c for c in PLUGIN_CATEGORIES if category_map.get(c)]
    custom_active = sorted(
        [c for c in category_map if c not in PLUGIN_CATEGORIES and category_map[c]]
    )
    active_categories = canonical_active + custom_active

    for category in active_categories:
        slug = github_slug(category)
        cat_plugins = category_map[category]
        top_count = sum(1 for p in cat_plugins if int(p.get("stars", 0) or 0) >= 3)
        new_count = len(cat_plugins) - top_count
        if top_count > 0 and new_count > 0:
            toc_lines.append(f"- [{category}](#{slug}) ({top_count} top · {new_count} new)")
        else:
            toc_lines.append(f"- [{category}](#{slug}) ({len(cat_plugins)})")

    content_sections = ["\n".join(toc_lines), "\n---"]

    if include_top_authors:
        content_sections.append(f"\n{generate_top_authors_list(plugins, limit=10)}\n")
        content_sections.append("\n---")

    for category in active_categories:
        content_sections.append(f"\n### {category}\n")
        cat_plugins = category_map[category]
        top_plugins = [p for p in cat_plugins if int(p.get("stars", 0) or 0) >= 3]
        new_plugins = [p for p in cat_plugins if int(p.get("stars", 0) or 0) < 3]

        if top_plugins:
            for p in top_plugins:
                content_sections.append(generate_plugin_markdown_item(p))

        if new_plugins:
            new_rendered = "\n".join(generate_plugin_markdown_item(p) for p in new_plugins)
            open_attr = " open" if not top_plugins else ""
            summary_title = (
                f"🐣 New & Emerging {category} ({len(new_plugins)} plugins · 0–2 ⭐)"
            )
            content_sections.append(
                f"\n<details{open_attr}>\n"
                f"<summary><b>{summary_title}</b></summary>\n\n"
                f"{new_rendered}\n\n"
                f"</details>"
            )

    return "\n".join(content_sections)


def update_readme(markdown_list: str, total_count: int, path: Path = README_PATH) -> bool:
    """Update a catalog markdown file with generated content between markers atomically."""
    if not path.exists():
        console.print(f"[red]Error: {path} not found![/red]")
        return False

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if START_MARKER not in content or END_MARKER not in content:
        console.print(
            f"[red]Error: Markers {START_MARKER} and {END_MARKER} not found in {path}[/red]"
        )
        return False

    pattern = re.compile(rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.DOTALL)
    replacement = f"{START_MARKER}\n{markdown_list}\n{END_MARKER}"
    new_content = pattern.sub(replacement, content)

    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    today_badge = today.replace("-", "--")
    badge_base = "https://img.shields.io/badge"
    count_replacement = (
        f'<!-- TOTAL_PLUGINS_COUNT --><a href="#plugins-list">'
        f'<img src="{badge_base}/Plugins-{total_count}-a78bfa.svg?style=flat-square" '
        f'alt="Tracked Plugins" /></a><!-- /TOTAL_PLUGINS_COUNT -->'
    )
    updated_replacement = (
        f'<!-- LAST_UPDATED --><a href="#plugins-list">'
        f'<img src="https://img.shields.io/badge/Updated-{today_badge}-blueviolet.svg'
        '?style=flat-square" '
        'alt="Last Updated" /></a><!-- /LAST_UPDATED -->'
    )

    new_content = re.sub(COUNT_MARKER_REGEX, count_replacement, new_content)
    new_content = re.sub(UPDATED_MARKER_REGEX, updated_replacement, new_content)

    atomic_write_text(path, new_content)
    return True


def get_sort_key(sort_mode: str) -> tuple[Callable[[Dict[str, Any]], Any], bool]:
    """Return sort key function and reverse boolean."""
    if sort_mode == "stars":
        return (
            lambda p: (
                p.get("stars") or 0,
                p.get("forks") or 0,
                p.get("last_updated")
                if p.get("last_updated") not in (None, "N/A", "")
                else "0000-00-00",
            ),
            True,
        )
    elif sort_mode == "name":
        return lambda p: str(p.get("name") or p.get("repo", "")).lower(), False
    elif sort_mode == "category":
        return lambda p: (str(p.get("category") or ""), p.get("stars") or 0), True
    # Default: "updated"
    return (
        lambda p: (
            p.get("last_updated")
            if p.get("last_updated") not in (None, "N/A", "")
            else "0000-00-00",
            p.get("stars") or 0,
        ),
        True,
    )


def regenerate_catalogs(
    plugins: List[Dict[str, Any]],
    sort_mode: str = "stars",
    flat: bool = False,
    min_stars: int = 0,
    stale_days: int = 0,
    dry_run: bool = False,
) -> None:
    """Regenerate README.md and BY_UPDATED.md from existing normalized plugins."""
    normalized = [normalize_plugin_entry(p) for p in plugins]
    save_plugins_atomic(normalized)

    sort_key, reverse_order = get_sort_key(sort_mode)
    normalized.sort(key=sort_key, reverse=reverse_order)

    listed = [p for p in normalized if not is_excluded(p, min_stars, stale_days)]
    excluded = [p for p in normalized if is_excluded(p, min_stars, stale_days)]
    if excluded:
        console.print(
            f"[yellow]Excluded {len(excluded)} entries from generated lists "
            f"(archived/abandoned/stale/low-star).[/yellow]"
        )

    markdown_list = generate_markdown_list(listed, grouped=not flat)
    if dry_run:
        print(markdown_list)
        return

    update_readme(markdown_list, len(listed), path=README_PATH)
    console.print(f"[bold green]✓ Updated README.md with {len(listed)} plugins![/bold green]")

    updated_key, updated_reverse = get_sort_key("updated")
    by_updated = sorted(listed, key=updated_key, reverse=updated_reverse)
    by_updated_markdown = generate_markdown_list(
        by_updated, grouped=not flat, include_top_authors=False
    )
    update_readme(by_updated_markdown, len(by_updated), path=BY_UPDATED_PATH)
    console.print(
        f"[bold green]✓ Updated BY_UPDATED.md with {len(by_updated)} plugins![/bold green]"
    )


async def main_async(args: argparse.Namespace) -> None:
    """Entry point: read rich data, optionally fetch stats, and update markdown catalogs."""
    plugins = load_plugins()
    if not plugins:
        console.print("[red]No plugins found in plugins.json.[/red]")
        sys.exit(1)

    token = get_github_token(args.token)

    if not args.render_only:
        # Group by unique repo (skipping abandoned/inactive repos to save API quota)
        unique_repos: Dict[str, Dict[str, Any]] = {}
        for plugin in plugins:
            if is_dead(plugin):
                continue
            key = f"{plugin['owner'].lower()}/{plugin['repo'].lower()}"
            unique_repos.setdefault(
                key,
                {
                    "owner": plugin["owner"],
                    "repo": plugin["repo"],
                    "cached": plugin,
                },
            )

        console.print(
            f"[bold cyan]Updating stats for {len(plugins)} plugins "
            f"({len(unique_repos)} unique repos)...[/bold cyan]"
        )

        repo_stats: Optional[Dict[str, Dict[str, Any]]] = None

        # Try GraphQL batching first if token is available (50 repos per query, ~52 queries)
        if token:
            repo_stats = fetch_catalog_stats_graphql(unique_repos, token)

        # Fall back to asynchronous REST if GraphQL failed or no token was provided
        if repo_stats is None:
            console.print(
                "[cyan]Running async REST updater with rate-limit circuit breaker...[/cyan]"
            )
            headers = get_github_headers(token)
            sem = asyncio.Semaphore(10)
            async with httpx.AsyncClient() as client:
                keys = list(unique_repos.keys())
                tasks = [
                    fetch_repo_stats_async(
                        client=client,
                        owner=unique_repos[k]["owner"],
                        repo=unique_repos[k]["repo"],
                        headers=headers,
                        sem=sem,
                        cached_stats=unique_repos[k]["cached"],
                    )
                    for k in keys
                ]
                results = await asyncio.gather(*tasks)
                repo_stats = dict(zip(keys, results))

        # Merge repository telemetry without overwriting plugin-specific name/desc in monorepos
        for plugin in plugins:
            key = f"{plugin['owner'].lower()}/{plugin['repo'].lower()}"
            stats = repo_stats.get(key)
            if not stats:
                continue
            for field in REPO_TELEMETRY_KEYS:
                if field in stats:
                    plugin[field] = stats[field]
            if not plugin.get("description") and stats.get("repo_description"):
                plugin["description"] = stats["repo_description"]

        # Re-evaluate abandonment status after merging live stats
        for plugin in plugins:
            if is_dead(plugin):
                plugin["dead"] = True

    # Persist and regenerate markdown
    regenerate_catalogs(
        plugins,
        sort_mode=args.sort,
        flat=args.flat,
        min_stars=args.min_stars,
        stale_days=args.stale_days,
        dry_run=args.dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Awesome Omarchy Plugins - Catalog Generator & Stats Updater"
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Regenerate README.md and BY_UPDATED.md from plugins.json without network requests",
    )
    parser.add_argument(
        "--sort",
        choices=["stars", "updated", "name", "category"],
        default="stars",
        help="Sorting criteria for plugins within categories or flat list (default: stars)",
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        help="Generate a single flat list instead of categorizing",
    )
    parser.add_argument(
        "--min-stars",
        type=int,
        default=0,
        help="Hide plugins below this star count from generated lists (default: 0, off)",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=0,
        help="Hide plugins not pushed within this many days (default: 0, disabled)",
    )
    parser.add_argument(
        "--token",
        type=str,
        help="GitHub Personal Access Token to bypass API rate limits",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print markdown list to stdout without writing to README.md",
    )

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
