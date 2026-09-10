"""Unit tests for shared utilities."""

from update_stats import get_sort_key
from utils import (
    PLUGIN_CATEGORIES,
    atomic_write_text,
    format_count,
    format_stars,
    github_slug,
    is_dead,
    is_excluded,
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

    def test_ssh_protocol_prefix(self):
        owner, repo, host = parse_repo_url("ssh://git@github.com/owner/my-plugin.git")
        assert (owner, repo, host) == ("owner", "my-plugin", "github.com")

    def test_schemeless_url(self):
        owner, repo, host = parse_repo_url("github.com/owner/my-plugin")
        assert (owner, repo, host) == ("owner", "my-plugin", "github.com")

    def test_repo_named_src_or_archive(self):
        owner, repo, host = parse_repo_url("https://github.com/my-org/src")
        assert (owner, repo, host) == ("my-org", "src", "github.com")
        owner2, repo2, host2 = parse_repo_url("https://github.com/archive/dweb-mirror")
        assert (owner2, repo2, host2) == ("archive", "dweb-mirror", "github.com")

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


class TestSortKeys:
    def test_updated_sort_orders_by_date_then_stars(self):
        key, reverse = get_sort_key("updated")
        assert reverse is True
        rows = [
            {"last_updated": "2026-01-01", "stars": 999},
            {"last_updated": "2026-09-01", "stars": 1},
            {"last_updated": "N/A", "stars": 5000},
        ]
        ordered = sorted(rows, key=key, reverse=reverse)
        assert [r["last_updated"] for r in ordered] == ["2026-09-01", "2026-01-01", "N/A"]

    def test_stars_sort_orders_by_stars(self):
        key, reverse = get_sort_key("stars")
        assert reverse is True
        rows = [
            {"stars": 5, "forks": 0, "last_updated": "2026-09-01"},
            {"stars": 50, "forks": 0, "last_updated": "2026-01-01"},
        ]
        ordered = sorted(rows, key=key, reverse=reverse)
        assert [r["stars"] for r in ordered] == [50, 5]


class TestIsDead:
    def test_explicit_dead_flag(self):
        assert is_dead({"dead": True, "stars": 100, "last_updated": "2026-09-01"})

    def test_archived_is_dead(self):
        assert is_dead({"archived": True, "stars": 500, "last_updated": "2026-09-01"})

    def test_na_last_updated_is_dead(self):
        assert is_dead({"last_updated": "N/A", "stars": 10})
        assert is_dead({"last_updated": None, "stars": 10})

    def test_abandoned_under_three_stars(self):
        # >150 days old and 0, 1, or 2 stars -> DEAD
        import datetime
        fixed_today = datetime.date(2026, 9, 8)
        assert is_dead({"stars": 0, "last_updated": "2026-04-10"}, today=fixed_today)
        assert is_dead({"stars": 1, "last_updated": "2026-04-10"}, today=fixed_today)
        assert is_dead({"stars": 2, "last_updated": "2026-04-10"}, today=fixed_today)

    def test_abandoned_boundary_under_one_fifty_days(self):
        # 149 days old and 0 stars -> NOT dead (still within 150-day window)
        import datetime
        fixed_today = datetime.date(2026, 9, 8)
        assert not is_dead({"stars": 0, "last_updated": "2026-04-12"}, today=fixed_today)

    def test_abandoned_survives_with_three_or_more_stars(self):
        import datetime
        fixed_today = datetime.date(2026, 9, 8)
        # >150 days old but has >= 3 stars -> NOT dead
        assert not is_dead({"stars": 3, "last_updated": "2025-01-01"}, today=fixed_today)
        assert not is_dead({"stars": 10, "last_updated": "2025-01-01"}, today=fixed_today)

    def test_fresh_repo_survives_with_zero_stars(self):
        import datetime
        fixed_today = datetime.date(2026, 9, 8)
        # Recent activity (<150 days) even with 0 stars -> NOT dead
        assert not is_dead({"stars": 0, "last_updated": "2026-09-01"}, today=fixed_today)


class TestExclusions:
    def test_healthy_plugin_listed(self):
        assert not is_excluded({"stars": 0, "last_updated": "2026-09-01", "archived": False})

    def test_archived_always_excluded(self):
        assert is_excluded({"stars": 500, "last_updated": "2026-09-01", "archived": True})

    def test_dead_repo_always_excluded(self):
        assert is_excluded({"stars": 10, "last_updated": "N/A", "archived": False})
        assert is_excluded({"stars": 100, "last_updated": "2026-09-01", "dead": True})

    def test_abandoned_repo_excluded(self):
        import datetime
        fixed_today = datetime.date(2026, 9, 8)
        assert is_excluded({"stars": 1, "last_updated": "2025-01-01"}, today=fixed_today)

    def test_min_stars_opt_in(self):
        plugin = {"stars": 3, "last_updated": "2026-09-01", "archived": False}
        assert not is_excluded(plugin)
        assert is_excluded(plugin, min_stars=5)

    def test_stale_days_opt_in(self):
        plugin = {"stars": 100, "last_updated": "2025-01-01", "archived": False}
        assert not is_excluded(plugin)
        assert is_excluded(plugin, stale_days=30)
        assert not is_excluded(plugin, stale_days=9999)


class TestAtomicWrite:
    def test_successful_write(self, tmp_path):
        target = tmp_path / "test.txt"
        atomic_write_text(target, "hello world")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_cleanup_on_failure(self, tmp_path, monkeypatch):
        target = tmp_path / "sub" / "fail.txt"
        target.parent.mkdir(parents=True)

        def mock_replace(src, dst):
            raise OSError("Simulated replace failure")

        import os

        monkeypatch.setattr(os, "replace", mock_replace)

        import pytest

        with pytest.raises(OSError, match="Simulated replace failure"):
            atomic_write_text(target, "content")

        # Verify no temp files were leaked in target.parent
        leftover = list(target.parent.glob("tmp*"))
        assert len(leftover) == 0, f"Leaked temporary files: {leftover}"
