"""Integration tests for plugins.json schema integrity and README synchronization."""

import json
import re

from utils import PLUGIN_CATEGORIES, PLUGINS_JSON_PATH, README_PATH, github_slug


class TestDatasetIntegrity:
    def test_plugins_json_exists_and_valid(self):
        assert PLUGINS_JSON_PATH.exists(), "plugins.json must exist"
        with open(PLUGINS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list), "plugins.json must be a list of plugin objects"
        assert len(data) > 0, "plugins.json must not be empty"

    def test_plugins_schema_fields(self):
        with open(PLUGINS_JSON_PATH, "r", encoding="utf-8") as f:
            plugins = json.load(f)

        required_keys = {
            "plugin_id",
            "owner",
            "repo",
            "name",
            "description",
            "category",
            "repo_url",
            "stars",
            "forks",
            "last_updated",
            "license",
        }

        for i, plugin in enumerate(plugins):
            assert isinstance(plugin, dict), f"Plugin at index {i} must be a dictionary"
            pid = plugin.get("plugin_id", i)
            for key in required_keys:
                assert key in plugin, f"Plugin '{pid}' missing required key '{key}'"
                assert plugin[key] is not None, f"Plugin '{pid}' has None for '{key}'"
            assert isinstance(plugin["stars"], int), f"Plugin '{pid}' stars must be integer"
            assert plugin["stars"] >= 0, f"Plugin '{pid}' stars cannot be negative"
            assert isinstance(plugin["forks"], int), f"Plugin '{pid}' forks must be integer"
            assert plugin["forks"] >= 0, f"Plugin '{pid}' forks cannot be negative"
            assert len(plugin["owner"]) > 0, f"Plugin at index {i} has empty owner"
            assert len(plugin["repo"]) > 0, f"Plugin at index {i} has empty repo"

    def test_no_duplicate_plugin_ids(self):
        with open(PLUGINS_JSON_PATH, "r", encoding="utf-8") as f:
            plugins = json.load(f)

        seen = set()
        duplicates = []
        for p in plugins:
            pid = p["plugin_id"].lower()
            if pid in seen:
                duplicates.append(pid)
            seen.add(pid)

        assert not duplicates, f"Duplicate plugin IDs found in plugins.json: {duplicates}"

    def test_categories_are_known(self):
        with open(PLUGINS_JSON_PATH, "r", encoding="utf-8") as f:
            plugins = json.load(f)

        for p in plugins:
            assert p["category"] in PLUGIN_CATEGORIES, (
                f"Plugin '{p['plugin_id']}' has unknown category '{p['category']}'"
            )


class TestReadmeSynchronization:
    def test_readme_markers_exist(self):
        assert README_PATH.exists(), "README.md must exist"
        content = README_PATH.read_text(encoding="utf-8")
        assert "<!-- PLUGINS_LIST_START -->" in content
        assert "<!-- PLUGINS_LIST_END -->" in content
        assert "<!-- TOTAL_PLUGINS_COUNT -->" in content
        assert "<!-- LAST_UPDATED -->" in content

    def test_readme_badge_count_matches_dataset(self):
        with open(PLUGINS_JSON_PATH, "r", encoding="utf-8") as f:
            plugins = json.load(f)

        content = README_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"<!-- TOTAL_PLUGINS_COUNT -->.*?(\d+).*?<!-- /TOTAL_PLUGINS_COUNT -->",
            content,
        )
        assert match is not None, "TOTAL_PLUGINS_COUNT badge marker not found in README.md"
        badge_count = int(match.group(1))
        assert badge_count == len(plugins), (
            f"README count badge ({badge_count}) does not match plugins.json total ({len(plugins)})"
        )

    def test_all_plugin_repos_in_readme(self):
        with open(PLUGINS_JSON_PATH, "r", encoding="utf-8") as f:
            plugins = json.load(f)

        content = README_PATH.read_text(encoding="utf-8").lower()
        seen_repos = set()
        for p in plugins:
            repo_url = p.get("repo_url", "").lower()
            if repo_url in seen_repos:
                continue
            seen_repos.add(repo_url)
            assert repo_url in content, f"Repository {p.get('repo_url')} missing from README.md"

    def test_toc_anchor_links_match_gfm_headings(self):
        """Ensure all TOC links match exact GitHub-rendered heading slugs."""
        content = README_PATH.read_text(encoding="utf-8")
        for category in PLUGIN_CATEGORIES:
            heading = f"### {category}"
            if heading in content:
                expected_anchor = f"(#{github_slug(category)})"
                assert expected_anchor in content, (
                    f"TOC link for '{category}' mismatches GFM anchor '{expected_anchor}'"
                )
