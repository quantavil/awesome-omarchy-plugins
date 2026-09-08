"""Unit tests for the IssueOps handler."""

from issueops_handler import parse_command_args, parse_issue_markdown_form


class TestParseCommandArgs:
    def test_add_with_url(self):
        result = parse_command_args("/add https://github.com/owner/repo")
        assert result["action"] == "add"
        assert result["url"] == "https://github.com/owner/repo"

    def test_add_with_flags(self):
        result = parse_command_args("/add https://github.com/o/r --category Widgets")
        assert result["action"] == "add"
        assert result["category"] == "Widgets"

    def test_remove(self):
        result = parse_command_args("/remove https://github.com/o/r")
        assert result["action"] == "remove"

    def test_empty(self):
        assert parse_command_args("") == {}


class TestParseIssueMarkdownForm:
    def test_extracts_repo_url(self):
        body = "### Repository URL\n\nhttps://github.com/owner/repo\n\n### Plugin Name\n\nMy Plugin"
        data = parse_issue_markdown_form(body)
        assert data["repo_url"] == "https://github.com/owner/repo"
        assert data["name"] == "My Plugin"

    def test_extracts_category(self):
        body = "### Repository URL\n\nhttps://github.com/o/r\n\n### Category\n\nWidgets"
        data = parse_issue_markdown_form(body)
        assert data["category"] == "Widgets"

    def test_empty_body(self):
        assert parse_issue_markdown_form("") == {}
