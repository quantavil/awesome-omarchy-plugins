"""Tests for GraphQL batching resilience and owner/repo markdown rendering."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from src.update_stats import (
    fetch_catalog_stats_graphql,
    generate_plugin_markdown_item,
    generate_top_authors_list,
)


@pytest.fixture
def sample_repos():
    return {
        "user1/repo1": {
            "owner": "user1",
            "repo": "repo1",
            "cached": {
                "owner": "user1",
                "repo": "repo1",
                "stars": 10,
                "forks": 1,
                "last_updated": "2026-08-01",
                "archived": False,
                "dead": False,
            },
        },
        "user2/repo2": {
            "owner": "user2",
            "repo": "repo2",
            "cached": {
                "owner": "user2",
                "repo": "repo2",
                "stars": 20,
                "forks": 2,
                "last_updated": "2026-08-02",
                "archived": False,
                "dead": False,
            },
        },
        "user3/repo3": {
            "owner": "user3",
            "repo": "repo3",
            "cached": {
                "owner": "user3",
                "repo": "repo3",
                "stars": 30,
                "forks": 3,
                "last_updated": "2026-08-03",
                "archived": False,
                "dead": False,
            },
        },
    }


def make_graphql_response(status_code: int = 200, json_data: dict | None = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.request = MagicMock()
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


class TestGraphQLBatchResilience:
    @patch("time.sleep", return_value=None)
    def test_partial_batch_failure_preserves_successful_batches_and_cache(
        self, mock_sleep, sample_repos
    ):
        """Batch 2 fails both attempts. Batches 1 and 3 succeed. Result must not be None."""
        resp_batch1 = make_graphql_response(
            200,
            {
                "data": {
                    "r0": {
                        "stargazerCount": 111,
                        "forkCount": 11,
                        "pushedAt": "2026-09-10T12:00:00Z",
                        "isArchived": False,
                        "primaryLanguage": {"name": "Python"},
                        "licenseInfo": {"spdxId": "MIT"},
                        "defaultBranchRef": {"name": "main"},
                        "openIssues": {"totalCount": 0},
                        "description": "Updated repo 1",
                    }
                }
            },
        )
        resp_batch3 = make_graphql_response(
            200,
            {
                "data": {
                    "r0": {
                        "stargazerCount": 333,
                        "forkCount": 33,
                        "pushedAt": "2026-09-10T12:00:00Z",
                        "isArchived": False,
                        "primaryLanguage": {"name": "Rust"},
                        "licenseInfo": {"spdxId": "Apache-2.0"},
                        "defaultBranchRef": {"name": "main"},
                        "openIssues": {"totalCount": 2},
                        "description": "Updated repo 3",
                    }
                }
            },
        )

        with patch("httpx.Client.post") as mock_post:
            # Batch 1: 200
            # Batch 2: attempt 1 ConnectError, attempt 2 ConnectError
            # Batch 3: 200
            mock_post.side_effect = [
                resp_batch1,
                httpx.ConnectError("Network drop on batch 2"),
                httpx.ConnectError("Network drop on batch 2 retry"),
                resp_batch3,
            ]

            result = fetch_catalog_stats_graphql(sample_repos, token="dummy_token", batch_size=1)

            assert result is not None
            assert len(result) == 3

            # Batch 1 has updated live stats
            assert result["user1/repo1"]["stars"] == 111
            assert result["user1/repo1"]["forks"] == 11

            # Batch 2 has cached fallback stats
            assert result["user2/repo2"]["stars"] == 20
            assert result["user2/repo2"]["forks"] == 2
            assert result["user2/repo2"]["last_updated"] == "2026-08-02"

            # Batch 3 has updated live stats
            assert result["user3/repo3"]["stars"] == 333
            assert result["user3/repo3"]["forks"] == 33

            # Assert retry sleep was invoked for attempt 1 of batch 2
            mock_sleep.assert_called_once_with(2.0)

    @patch("time.sleep", return_value=None)
    def test_transient_failure_recovers_on_retry(self, mock_sleep, sample_repos):
        """Batch fails on attempt 1 with timeout, succeeds on attempt 2."""
        single_repo = {"user1/repo1": sample_repos["user1/repo1"]}
        resp_success = make_graphql_response(
            200,
            {
                "data": {
                    "r0": {
                        "stargazerCount": 999,
                        "forkCount": 99,
                        "pushedAt": "2026-09-10T12:00:00Z",
                        "isArchived": False,
                        "primaryLanguage": {"name": "Python"},
                        "licenseInfo": None,
                        "defaultBranchRef": None,
                        "openIssues": None,
                        "description": "Recovered",
                    }
                }
            },
        )

        with patch("httpx.Client.post") as mock_post:
            mock_post.side_effect = [
                httpx.ReadTimeout("Timeout on attempt 1"),
                resp_success,
            ]

            result = fetch_catalog_stats_graphql(single_repo, token="dummy_token", batch_size=1)

            assert result is not None
            assert result["user1/repo1"]["stars"] == 999
            mock_sleep.assert_called_once_with(2.0)

    @patch("time.sleep", return_value=None)
    def test_rate_limit_429_aborts_and_backfills_remaining(self, mock_sleep, sample_repos):
        """Batch 1 succeeds, Batch 2 hits 429: aborts, backfills cache, returns repo_stats."""
        resp_batch1 = make_graphql_response(
            200,
            {
                "data": {
                    "r0": {
                        "stargazerCount": 50,
                        "forkCount": 5,
                        "pushedAt": "2026-09-10T12:00:00Z",
                        "isArchived": False,
                        "primaryLanguage": None,
                        "licenseInfo": None,
                        "defaultBranchRef": None,
                        "openIssues": None,
                        "description": "Repo 1",
                    }
                }
            },
        )
        resp_batch2_429 = make_graphql_response(429)

        with patch("httpx.Client.post") as mock_post:
            mock_post.side_effect = [resp_batch1, resp_batch2_429]

            result = fetch_catalog_stats_graphql(sample_repos, token="dummy_token", batch_size=1)

            assert result is not None
            assert len(result) == 3
            assert result["user1/repo1"]["stars"] == 50
            # Batches 2 and 3 kept cached
            assert result["user2/repo2"]["stars"] == 20
            assert result["user3/repo3"]["stars"] == 30

    @patch("time.sleep", return_value=None)
    def test_all_batches_fail_returns_none(self, mock_sleep, sample_repos):
        """When 0 batches succeed, returns None to trigger REST fallback."""
        with patch("httpx.Client.post") as mock_post:
            mock_post.side_effect = httpx.ConnectError("Global network failure")

            result = fetch_catalog_stats_graphql(sample_repos, token="dummy_token", batch_size=1)

            assert result is None


class TestOwnerRepoMarkdownRendering:
    def test_generate_markdown_item_uses_owner_repo_install_command(self):
        plugin = {
            "name": "QuickApps HUD",
            "owner": "bjarneo",
            "repo": "omarchy-shell-plugins",
            "repo_url": "https://github.com/bjarneo/omarchy-shell-plugins",
            "description": "HUD launcher",
            "stars": 15,
            "forks": 3,
            "last_updated": "2026-09-08",
            "archived": False,
        }
        item = generate_plugin_markdown_item(plugin)
        assert "- **[QuickApps HUD](https://github.com/bjarneo/omarchy-shell-plugins)**" in item
        assert "`omarchy plugin add bjarneo/omarchy-shell-plugins --enable`" in item
        assert "https://github.com/bjarneo/omarchy-shell-plugins --enable" not in item

    def test_generate_markdown_item_fallback_when_owner_or_repo_missing(self):
        plugin = {
            "name": "Standalone",
            "owner": "",
            "repo": "",
            "repo_url": "https://example.com/standalone",
            "description": "Standalone plugin",
            "stars": 5,
            "forks": 0,
            "last_updated": "2026-09-01",
        }
        item = generate_plugin_markdown_item(plugin)
        assert "`omarchy plugin add https://example.com/standalone --enable`" in item


class TestTopAuthorsLeaderboard:
    def test_generate_top_authors_aggregates_and_sorts(self):
        plugins = [
            {"owner": "alice", "repo": "app1", "stars": 50},
            {"owner": "bob", "repo": "tool1", "stars": 100},
            {"owner": "alice", "repo": "app2", "stars": 80},  # alice total = 130
            {"owner": "carol", "repo": "widget1", "stars": 20},
            {"owner": "", "repo": "unknown", "stars": 999},  # missing owner ignored
        ]

        result = generate_top_authors_list(plugins, limit=2)
        assert "### 🏆 Top Plugin Authors" in result

        assert "| Rank | Contributor | Total Stars | Plugins |" in result
        assert "| :---: | :--- | :---: | :---: |" in result
        lines = result.strip().split("\n")
        # Alice has 130 stars across 2 plugins -> Rank 1 (Gold medal)
        assert "| 🥇 | [@alice](https://github.com/alice) | ⭐ 130 | 2 |" in lines[4]
        # Bob has 100 stars across 1 plugin -> Rank 2 (Silver medal)
        assert "| 🥈 | [@bob](https://github.com/bob) | ⭐ 100 | 1 |" in lines[5]
        # Carol (20 stars) excluded by limit=2
        assert "@carol" not in result
        # Unknown owner ignored
        assert "unknown" not in result

