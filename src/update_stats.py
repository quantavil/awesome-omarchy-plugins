#!/usr/bin/env python3
"""Awesome Omarchy Plugins - GitHub Stats Updater.

Fetches live stars, forks, last activity, and license info per repository,
then regenerates the categorized README.md list.

Dataset: plugins.json (derived from the omarchy-plugin-marketplace
registry.json, enriched with live GitHub metadata).

Supports async concurrency, category grouping, and atomic file writes.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from rich.console import Console
from rich.table import Table

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
    is_excluded,
    load_plugins,
    normalize_plugin_entry,
    parse_repo_url,
    save_plugins_atomic,
)

console = Console()

START_MARKER = "<!-- PLUGINS_LIST_START -->"
END_MARKER = "<!-- PLUGINS_LIST_END -->"
COUNT_MARKER_REGEX = r"<!-- TOTAL_PLUGINS_COUNT -->.*?<!-- /TOTAL_PLUGINS_COUNT -->"
UPDATED_MARKER_REGEX = r"<!-- LAST_UPDATED -->.*?<!-- /LAST_UPDATED -->"


async def fetch_repo_stats_async(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    headers: Dict[str, str],
    sem: asyncio.Semaphore,
    cached_stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fetch repository metadata asynchronously, falling back to cached stats."""
    stats: Dict[str, Any] = {
        "stars": cached_stats.get("stars", 0) if cached_stats else 0,
        "forks": cached_stats.get("forks", 0) if cached_stats else 0,
        "last_updated": cached_stats.get("last_updated", "N/A") if cached_stats else "N/A",
        "updated_at": cached_stats.get("updated_at", "N/A") if cached_stats else "N/A",
        "license": cached_stats.get("license", "Unknown") if cached_stats else "Unknown",
        "language": cached_stats.get("language", "") if cached_stats else "",
        "default_branch": cached_stats.get("default_branch", "main") if cached_stats else "main",
        "archived": cached_stats.get("archived", False) if cached_stats else False,
        "open_issues": cached_stats.get("open_issues", 0) if cached_stats else 0,
    }

    async with sem:
        try:
            repo_url = f"https://api.github.com/repos/{owner}/{repo}"
            resp = await client.get(
                repo_url, headers=headers, timeout=12.0, follow_redirects=True
            )
            if resp.status_code == 200:
                data = resp.json()
                stats["stars"] = data.get("stargazers_count", stats["stars"])
                stats["forks"] = data.get("forks_count", stats["forks"])
                stats["archived"] = data.get("archived", stats["archived"])
                stats["language"] = data.get("language") or stats["language"]
                stats["open_issues"] = data.get("open_issues_count", stats["open_issues"])
                if data.get("license") and data["license"].get("spdx_id"):
                    spdx = data["license"]["spdx_id"]
                    if spdx != "NOASSERTION":
                        stats["license"] = spdx
                stats["default_branch"] = data.get("default_branch", "main")

                pushed_at = data.get("pushed_at")
                if pushed_at:
                    stats["last_updated"] = pushed_at.split("T")[0]
                    stats["updated_at"] = pushed_at.split("T")[0]

                if cached_stats:
                    if not cached_stats.get("description") and data.get("description"):
                        stats["description"] = data["description"].strip()
                    cached_name = cached_stats.get("name")
                    if (not cached_name or cached_name == repo) and data.get("name"):
                        stats["name"] = data["name"].strip()
            elif resp.status_code == 403:
                console.print(
                    f"[yellow]Rate limit for {owner}/{repo}. Using cached stats.[/yellow]"
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
    plugin_id = plugin.get("plugin_id", "")
    stars_formatted = format_count(plugin.get("stars", 0))
    forks_formatted = format_count(plugin.get("forks", 0))
    last_updated = plugin.get("last_updated", "N/A")
    language = plugin.get("language") or "QML"
    license_str = plugin.get("license", "")
    archived_badge = " *(Archived)*" if plugin.get("archived") else ""

    meta_parts = [
        f"⭐ **{stars_formatted}**",
        f"🍴 {forks_formatted}",
        f"Last updated: `{last_updated}`",
        f"`{language}`",
    ]
    if license_str and license_str not in ("Unknown", "NOASSERTION"):
        meta_parts.append(f"`{license_str}`")
    if plugin_id:
        meta_parts.append(f"`{plugin_id}`")

    meta_line = " · ".join(meta_parts)
    return f"- **[{name}]({url})**{archived_badge} : {desc}\n  - {meta_line}"


def generate_markdown_list(plugins: List[Dict[str, Any]], grouped: bool = True) -> str:
    """Generate Markdown representation of the plugin list, grouped by category."""
    if not grouped:
        return "\n".join(generate_plugin_markdown_item(p) for p in plugins)

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
        count = len(category_map[category])
        toc_lines.append(f"- [{category}](#{slug}) ({count})")

    content_sections = ["\n".join(toc_lines), "\n---"]

    for category in active_categories:
        content_sections.append(f"\n### {category}\n")
        for p in category_map[category]:
            content_sections.append(generate_plugin_markdown_item(p))

    return "\n".join(content_sections)


def update_readme(
    markdown_list: str, total_count: int, path: Path = README_PATH
) -> bool:
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


def add_plugin(
    url: str,
    plugin_id: Optional[str] = None,
    name: Optional[str] = None,
    desc: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[str] = None,
) -> None:
    """Add or update a plugin repository in plugins.json."""
    owner, repo, _host = parse_repo_url(url)
    if not owner or not repo:
        console.print(f"[red]Error: Invalid repository URL or format: '{url}'[/red]")
        return

    plugins = load_plugins()
    for p in plugins:
        if (
            p["owner"].lower() == owner.lower()
            and p["repo"].lower() == repo.lower()
            and (not plugin_id or p.get("plugin_id", "").lower() == plugin_id.lower())
        ):
            console.print(
                f"[yellow]Plugin {owner}/{repo} already exists in plugins.json. "
                "Updating details...[/yellow]"
            )
            if plugin_id:
                p["plugin_id"] = plugin_id
            if name:
                p["name"] = name
            if desc:
                p["description"] = desc
            if category:
                p["category"] = category
            if tags:
                p["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            save_plugins_atomic([normalize_plugin_entry(item) for item in plugins])
            console.print(f"[green]Successfully updated {owner}/{repo}![/green]")
            return

    new_data: Dict[str, Any] = {
        "plugin_id": plugin_id or repo,
        "owner": owner,
        "repo": repo,
        "name": name or repo,
        "description": desc or "",
        "category": category or "",
        "tags": [t.strip() for t in tags.split(",") if t.strip()] if tags else [],
        "repo_url": f"https://github.com/{owner}/{repo}",
        "type": "plugin-source",
    }
    plugins.append(normalize_plugin_entry(new_data))
    save_plugins_atomic(plugins)
    console.print(
        f"[green]Added {owner}/{repo} to plugins.json ({len(plugins)} total plugins)![/green]"
    )


def remove_plugin(url_or_id: str) -> bool:
    """Remove a plugin from plugins.json by repository URL, slug, or plugin ID."""
    owner, repo, _host = parse_repo_url(url_or_id)
    plugins = load_plugins()
    initial_len = len(plugins)

    def _match(p: Dict[str, Any]) -> bool:
        if owner and repo:
            if p["owner"].lower() == owner.lower() and p["repo"].lower() == repo.lower():
                return True
        return p.get("plugin_id", "").lower() == url_or_id.strip().lower()

    filtered = [p for p in plugins if not _match(p)]

    if len(filtered) == initial_len:
        console.print(f"[yellow]Plugin '{url_or_id}' not found in plugins.json.[/yellow]")
        return False

    save_plugins_atomic([normalize_plugin_entry(item) for item in filtered])
    console.print(
        f"[green]Removed '{url_or_id}' from plugins.json "
        f"({len(filtered)} total plugins)![/green]"
    )
    return True


def get_sort_key(sort_mode: str) -> tuple[Callable[[Dict[str, Any]], Any], bool]:
    """Return sort key function and reverse boolean."""
    if sort_mode == "stars":
        return (
            lambda p: (
                p.get("stars", 0),
                p.get("forks", 0),
                p.get("last_updated")
                if p.get("last_updated") not in (None, "N/A", "")
                else "0000-00-00",
            ),
            True,
        )
    elif sort_mode == "name":
        return lambda p: p.get("name", p["repo"]).lower(), False
    elif sort_mode == "category":
        return lambda p: (p.get("category", ""), p.get("stars", 0)), True
    # Default: "updated"
    return (
        lambda p: (
            p.get("last_updated")
            if p.get("last_updated") not in (None, "N/A", "")
            else "0000-00-00",
            p.get("stars", 0),
        ),
        True,
    )


async def main_async(args: argparse.Namespace) -> None:
    """Asynchronous entry point for stats updater."""
    if args.add:
        add_plugin(
            args.add,
            plugin_id=args.plugin_id,
            name=args.name,
            desc=args.desc,
            category=args.category,
            tags=args.tags,
        )
    elif args.remove:
        if not remove_plugin(args.remove):
            console.print(f"[red]Could not remove '{args.remove}'.[/red]")
            return

    plugins = load_plugins()
    if not plugins:
        console.print("[red]No plugins found in plugins.json.[/red]")
        sys.exit(1)

    # Fetch live stats once per unique repository (several plugin IDs can
    # share a single repo, e.g. multi-plugin sources and suites).
    unique_repos: Dict[str, Dict[str, Any]] = {}
    for plugin in plugins:
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
        f"[bold cyan]Fetching live stats for {len(plugins)} plugins "
        f"({len(unique_repos)} unique repos) asynchronously...[/bold cyan]"
    )

    token = get_github_token(args.token)
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

        for plugin in plugins:
            key = f"{plugin['owner'].lower()}/{plugin['repo'].lower()}"
            plugin.update(repo_stats[key])

    # Persist updated stats atomically
    normalized = [normalize_plugin_entry(p) for p in plugins]
    save_plugins_atomic(normalized)

    # Apply sorting globally and within category groups
    sort_key, reverse_order = get_sort_key(args.sort)
    normalized.sort(key=sort_key, reverse=reverse_order)

    # Quality filter: archived/dead repos are always hidden from generated
    # lists (kept in plugins.json); star/staleness cutoffs are opt-in.
    listed = [p for p in normalized if not is_excluded(p, args.min_stars, args.stale_days)]
    excluded = [p for p in normalized if is_excluded(p, args.min_stars, args.stale_days)]
    if excluded:
        console.print(
            f"[yellow]Excluded {len(excluded)} entries from generated lists "
            f"(archived/dead/stale/low-star):[/yellow]"
        )
        for p in excluded[:20]:
            console.print(f"  - {p.get('plugin_id')} ({p.get('owner')}/{p.get('repo')})")
        if len(excluded) > 20:
            console.print(f"  ... and {len(excluded) - 20} more")

    # Display preview table in console
    table = Table(title="Awesome Omarchy Plugins - Live Stats")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Plugin", style="bold white")
    table.add_column("Category", style="cyan")
    table.add_column("Stars", justify="right", style="yellow")
    table.add_column("Forks", justify="right", style="yellow")
    table.add_column("Last Updated", justify="center", style="green")
    table.add_column("License", style="dim")

    for i, p in enumerate(listed, 1):
        table.add_row(
            str(i),
            p.get("name", p["repo"]),
            p.get("category", "Other"),
            format_count(p.get("stars", 0)),
            format_count(p.get("forks", 0)),
            p.get("last_updated", "N/A"),
            p.get("license", "Unknown"),
        )

    console.print(table)

    # Generate Markdown (grouped mode unless --flat is explicitly passed)
    markdown_list = generate_markdown_list(listed, grouped=not args.flat)

    if args.dry_run:
        console.print("\n[bold]Generated Markdown Output:[/bold]\n")
        print(markdown_list)
        return

    if update_readme(markdown_list, len(listed)):
        console.print(
            f"[bold green]✓ Successfully updated README.md with "
            f"{len(listed)} plugins![/bold green]"
        )
    else:
        console.print(
            "[yellow]Tip: Ensure README.md exists with markers "
            "<!-- PLUGINS_LIST_START --> and <!-- PLUGINS_LIST_END -->[/yellow]"
        )

    # Second view: same catalog, each category sorted by most recently updated.
    updated_key, updated_reverse = get_sort_key("updated")
    by_updated = sorted(listed, key=updated_key, reverse=updated_reverse)
    by_updated_markdown = generate_markdown_list(by_updated, grouped=not args.flat)
    if update_readme(by_updated_markdown, len(by_updated), path=BY_UPDATED_PATH):
        console.print(
            f"[bold green]✓ Successfully updated BY_UPDATED.md with "
            f"{len(by_updated)} plugins![/bold green]"
        )
    else:
        console.print(
            "[yellow]Tip: Ensure BY_UPDATED.md exists with markers "
            "<!-- PLUGINS_LIST_START --> and <!-- PLUGINS_LIST_END -->[/yellow]"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Awesome Omarchy Plugins - Stats Updater")
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
        "--add",
        type=str,
        help="Add a new plugin by GitHub URL or owner/repo (e.g., https://github.com/owner/repo)",
    )
    parser.add_argument(
        "--remove",
        type=str,
        help="Remove a plugin by repository URL, owner/repo, or plugin ID",
    )
    parser.add_argument("--plugin-id", type=str, help="Plugin ID (used with --add)")
    parser.add_argument("--name", type=str, help="Plugin display name (used with --add)")
    parser.add_argument("--desc", type=str, help="Plugin description (used with --add)")
    parser.add_argument("--category", type=str, help="Category (used with --add)")
    parser.add_argument("--tags", type=str, help="Comma-separated tags (used with --add)")
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
