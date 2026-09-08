"""Shared utilities for Awesome Omarchy Plugins.

Provides atomic file IO, robust GitHub URL parsing, API authentication,
star/fork formatting, GFM slug generation, and dataset normalization.

Adapted from awesome-android-games (same workflow, plugin-oriented schema).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
PLUGINS_JSON_PATH = ROOT_DIR / "plugins.json"
README_PATH = ROOT_DIR / "README.md"
BY_UPDATED_PATH = ROOT_DIR / "BY_UPDATED.md"

# Upstream marketplace snapshot this catalog is derived from.
REGISTRY_URL = (
    "https://raw.githubusercontent.com/omacom/omarchy-plugin-marketplace"
    "/refs/heads/main/registry.json"
)

# Canonical categories, ordered by catalog size (largest first).
PLUGIN_CATEGORIES: List[str] = [
    "Widgets",
    "Productivity",
    "System",
    "Hardware",
    "Desktop",
    "Developer Tools",
    "Appearance",
    "Other",
    "Kids",
]

# Fallback keyword mapping used only when an entry has no explicit category
# (e.g. community submissions without --category). Registry entries always
# carry an authoritative category, which is trusted verbatim.
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "Widgets": ["widget", "bar", "pillbar", "clock", "calendar", "usage", "monitor"],
    "Productivity": ["launcher", "productivity", "pomodoro", "todo", "notes", "timer"],
    "System": ["system", "updates", "backup", "vpn", "wireguard", "network", "battery"],
    "Hardware": ["hardware", "power", "fan", "elgato", "keyboard", "bluetooth", "audio"],
    "Desktop": ["desktop", "shell", "suite", "sidebar", "workspace", "hyprland"],
    "Developer Tools": ["developer", "git", "docker", "ai", "codex", "opencode", "cli"],
    "Appearance": ["appearance", "theme", "wallpaper", "cursor", "lock", "font"],
    "Kids": ["kids", "education", "parental"],
}


def github_slug(text: str) -> str:
    """Generate exact GitHub Flavored Markdown (GFM) header anchor slug."""
    text = text.lower()
    # Strip all characters except word characters, whitespace, and hyphens
    text = re.sub(r"[^\w\s-]", "", text)
    # Replace space characters with hyphens
    return re.sub(r" ", "-", text)


def infer_category(text: str) -> str:
    """Infer canonical category from name/description/tags as a last resort."""
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            pattern = rf"\b{re.escape(kw)}\b"
            if re.search(pattern, text_lower):
                return category
    return "Other"


def format_stars(count: int) -> str:
    """Format star count into compact human-readable string (e.g. 1.2k, 28.7k)."""
    return format_count(count)


def format_count(count: int) -> str:
    """Format a count into compact human-readable string (e.g. 1.2k, 3.4M)."""
    try:
        count = int(count)
    except (ValueError, TypeError):
        return "0"

    # Avoid boundary rounding artifact where 999_950 formats to '1000.0k'
    if count >= 999_950:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def parse_repo_url(url: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract owner, repo, and host from a Git repository URL.

    Supports:
    - https://github.com/owner/repo
    - git@github.com:owner/repo.git
    - owner/repo (defaults host to github.com)

    Returns:
        tuple (owner, repo, host) or (None, None, None)
    """
    if not url:
        return None, None, None

    clean = url.strip()

    # SSH pattern: git@<host>:<owner>/<repo>.git
    ssh_match = re.match(r"^git@([^:]+):([^/\s]+)/([^/\s#]+?)(?:\.git)?/?$", clean)
    if ssh_match:
        host = ssh_match.group(1).lower()
        owner = ssh_match.group(2)
        repo = ssh_match.group(3).removesuffix(".git")
        return owner, repo, host

    # HTTP/HTTPS URLs
    if clean.startswith("http://") or clean.startswith("https://"):
        parsed = urlparse(clean)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        raw_parts = [p for p in parsed.path.strip("/").split("/") if p]
        parts = []
        for p in raw_parts:
            if p in ("-", "tree", "blob", "src", "browse", "repository", "archive"):
                break
            parts.append(p)
        if len(parts) >= 2:
            owner = parts[0]
            repo = parts[1].removesuffix(".git")
            return owner, repo, netloc
        return None, None, None

    # Plain owner/repo format (allowing trailing slashes / .git)
    clean_slug = clean.strip("/")
    parts = [p for p in clean_slug.split("/") if p]
    if len(parts) == 2 and not clean.startswith("http"):
        return parts[0], parts[1].removesuffix(".git"), "github.com"

    return None, None, None


def get_github_token(explicit_token: Optional[str] = None) -> Optional[str]:
    """Retrieve GitHub token from explicit arg, env variables, or gh CLI."""
    if explicit_token:
        return explicit_token
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_github_headers(token: Optional[str] = None) -> Dict[str, str]:
    """Build standard GitHub API request headers."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Awesome-Omarchy-Plugins-Updater/1.0",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def atomic_write_text(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically write text content to file using temp file, ensuring 0o644 mode."""
    file_path = Path(file_path)
    parent = file_path.parent
    parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=parent,
        delete=False,
        encoding=encoding,
    ) as temp_file:
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_path = Path(temp_file.name)

    try:
        os.chmod(temp_path, 0o644)
    except OSError:
        pass

    os.replace(temp_path, file_path)


def atomic_write_json(file_path: Path, data: Any, indent: int = 2) -> None:
    """Atomically write JSON data to file with formatted indent."""
    content = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
    atomic_write_text(file_path, content)


def load_plugins(file_path: Path = PLUGINS_JSON_PATH) -> List[Dict[str, Any]]:
    """Load plugin dataset from JSON file."""
    if not file_path.exists():
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_plugins_atomic(
    plugins: List[Dict[str, Any]], file_path: Path = PLUGINS_JSON_PATH
) -> None:
    """Save plugin dataset atomically."""
    atomic_write_json(file_path, plugins)


def normalize_plugin_entry(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure standard keys and types exist for a plugin entry."""
    raw = raw or {}
    owner = str(raw.get("owner") or "").strip()
    repo = str(raw.get("repo") or "").strip()
    repo_url = str(raw.get("repo_url") or "").strip()
    if not repo_url and owner and repo:
        repo_url = f"https://github.com/{owner}/{repo}"
    plugin_id = str(raw.get("plugin_id") or repo or "unknown").strip()
    name = str(raw.get("name") or repo or "Unknown").strip()
    desc = re.sub(r"\s+", " ", str(raw.get("description") or "")).strip()
    tags = raw.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    # Trust the registry's authoritative category; infer only when missing.
    raw_category = raw.get("category")
    if raw_category and str(raw_category).strip():
        category = str(raw_category).strip()
    else:
        category = infer_category(f"{name} {desc} {' '.join(tags)}")

    def _int(value: Any) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    entry: Dict[str, Any] = {
        "plugin_id": plugin_id,
        "owner": owner,
        "repo": repo,
        "name": name,
        "description": desc,
        "category": category,
        "tags": list(tags),
        "repo_url": repo_url,
        "type": str(raw.get("type") or "plugin-source"),
        "author": str(raw.get("author") or ""),
        "version": str(raw.get("version") or ""),
        "addedAt": str(raw.get("addedAt") or ""),
        "stars": _int(raw.get("stars", 0)),
        "forks": _int(raw.get("forks", 0)),
        "last_updated": str(raw.get("last_updated") or "N/A"),
        "updated_at": str(raw.get("updated_at") or raw.get("last_updated") or "N/A"),
        "license": str(raw.get("license") or "Unknown"),
        "language": str(raw.get("language") or ""),
        "archived": bool(raw.get("archived", False)),
        "open_issues": _int(raw.get("open_issues", 0)),
        "default_branch": str(raw.get("default_branch") or "main"),
    }
    return entry
