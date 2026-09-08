"""Unit tests for shared utilities."""

from utils import (
    PLUGIN_CATEGORIES,
    format_count,
    format_stars,
    github_slug,
    normalize_plugin_entry,
    parse_repo_url,
)


class TestFormatCount:
    def test_small_numbers(self):
        assert format_count(0) == "0"
        assert format_count(999) == "999"

    def test_thousands(self):
        assert format_count(1000) == "1.0k"
        assert format_count(28851) == "28.9k"

    def test_millions(self):
        assert format_count(1_500_000) == "1.5M"

    def test_invalid(self):
        assert format_count(None) == "0"
        assert format_count("abc") == "0"

    def test_stars_alias(self):
        assert format_stars(1500) == "1.5k"


class TestGithubSlug:
    def test_basic(self):
        assert github_slug("Developer Tools") == "developer-tools"
        assert github_slug("Widgets") == "widgets"

    def test_special_chars(self):
        assert github_slug("Rhythm & Music") == "rhythm--music"


class TestParseRepoUrl:
    def test_https(self):
        owner, repo, host = parse_repo_url("https://github.com/owner/my-plugin")
        assert (owner, repo, host) == ("owner", "my-plugin", "github.com")

    def test_https_git_suffix(self):
        owner, repo, host = parse_repo_url("https://github.com/owner/my-plugin.git")
        assert (owner, repo) == ("owner", "my-plugin")

    def test_ssh(self):
        owner, repo, host = parse_repo_url("git@github.com:owner/my-plugin.git")
        assert (owner, repo, host) == ("owner", "my-plugin", "github.com")

    def test_short_slug(self):
        owner, repo, host = parse_repo_url("owner/my-plugin")
        assert (owner, repo, host) == ("owner", "my-plugin", "github.com")

    def test_invalid(self):
        assert parse_repo_url("not-a-url") == (None, None, None)
        assert parse_repo_url("") == (None, None, None)


class TestNormalizePluginEntry:
    def test_defaults(self):
        entry = normalize_plugin_entry({"owner": "o", "repo": "r"})
        assert entry["plugin_id"] == "r"
        assert entry["category"] == "Other"
        assert entry["stars"] == 0
        assert entry["forks"] == 0
        assert entry["repo_url"] == "https://github.com/o/r"

    def test_trusted_category(self):
        entry = normalize_plugin_entry(
            {"owner": "o", "repo": "r", "category": "Widgets", "description": "desktop shell suite"}
        )
        assert entry["category"] == "Widgets"

    def test_categories_known(self):
        assert "Widgets" in PLUGIN_CATEGORIES
        assert "Kids" in PLUGIN_CATEGORIES
        assert len(PLUGIN_CATEGORIES) == 9
