# Contributing to Awesome Omarchy Plugins

Thank you for your interest in contributing to **Awesome Omarchy Plugins**! 🎉

We welcome submissions, metadata improvements, corrections, and removals to keep this catalog high-quality, comprehensive, and up-to-date.

---

## Table of Contents

- [Submission & Quality Guidelines](#submission--quality-guidelines)
- [Data Sources](#data-sources)
- [How to Contribute](#how-to-contribute)
  - [Pathway A: GitHub Issues (Easiest)](#pathway-a-github-issues-easiest)
  - [Pathway B: Pull Requests (Fast-Tracked)](#pathway-b-pull-requests-fast-tracked)
- [CLI Tools & Commands](#cli-tools--commands)
  - [Adding / Updating Plugins](#adding--updating-plugins)
  - [Removing Plugins](#removing-plugins)
  - [Re-syncing From Upstream](#re-syncing-from-upstream)
- [Maintainer IssueOps Automation](#maintainer-issueops-automation)
- [Pull Request Process](#pull-request-process)
- [Automated Validation & CI](#automated-validation--ci)

---

## Submission & Quality Guidelines

Before submitting a plugin, please verify that it meets the following criteria:

1. **Open-Source**: The repository must contain source code under an OSI-approved or recognized open-source license (e.g. MIT, GPL, Apache, BSD, MPL).
2. **Omarchy Plugin**: The repository must be an Omarchy shell plugin, widget, bar, or suite (quickshell-based).
3. **Functional**: The plugin should work and not be an empty boilerplate or placeholder.
4. **Marketplace-listed (preferred)**: Plugins listed in the [omarchy-plugin-marketplace registry](https://github.com/omacom/omarchy-plugin-marketplace) are merged fastest, since this catalog syncs from it.

---

## Data Sources

- **Upstream registry**: [`omacom/omarchy-plugin-marketplace`](https://github.com/omacom/omarchy-plugin-marketplace/blob/main/registry.json) — authoritative plugin IDs, categories, and tags. Re-sync via `scripts/sync_registry.py`.
- **Live enrichment**: GitHub API (⭐ stars, 🍴 forks, last push date, license, language, archived status) refreshed by `src/update_stats.py` and the weekly CI workflow.

---

## How to Contribute

### Pathway A: GitHub Issues (Easiest)

No local setup or programming required! Use our structured issue templates:

- **Suggest a New Plugin**: Provide the repository URL, category, and plugin details.
- **Update Plugin Metadata**: Fix descriptions, recategorize, or update URLs.
- **Request Plugin Removal**: Report defunct, closed-source, or broken entries.

### Pathway B: Pull Requests (Fast-Tracked)

If you'd like your changes merged directly:

1. Fork this repository and clone locally.
2. Use the CLI tool or edit [`plugins.json`](plugins.json).
3. Run test and lint checks (`uv run pytest && uv run ruff check .`).
4. Submit a Pull Request.

---

## CLI Tools & Commands

### Adding / Updating Plugins

```bash
# Auto-fetch metadata and add/update plugin
uv run python src/update_stats.py --add "https://github.com/owner/repo"

# Optional overrides
uv run python src/update_stats.py --add "https://github.com/owner/repo" \
  --plugin-id "my.widget" \
  --name "My Widget" \
  --category "Widgets" \
  --tags "bar,quickshell" \
  --desc "A minimal bar widget."
```

### Removing Plugins

```bash
# Remove by repository URL, owner/repo, or plugin ID
uv run python src/update_stats.py --remove "https://github.com/owner/repo"
uv run python src/update_stats.py --remove "my.widget"
```

### Re-syncing From Upstream

```bash
# Pull the latest registry snapshot (preserves enriched stats), then enrich
uv run python scripts/sync_registry.py
uv run python src/update_stats.py
```

---

## Maintainer IssueOps Automation

Maintainers can automatically process community issues using GitHub Actions IssueOps:

### Comment Commands

Comment directly on any submission issue:

- `/add` — Adds the plugin using the issue form's repository URL and metadata.
- `/add <url> [--category <category>] [--tags <tags>]` — Adds a plugin with explicit parameters.
- `/remove` — Removes the plugin cited in the removal request issue.
- `/remove <url>` — Removes a specific plugin URL.

### Label Triggers

Applying the following labels will automatically execute the action, sync `README.md`, run tests, commit to `main`, and close the issue:

- `approved-add`
- `approved-remove`

---

## Pull Request Process

1. Fork this repository and clone your fork locally.
2. Create a feature branch:
   ```bash
   git checkout -b add-my-plugin
   ```
3. Make your changes in `plugins.json` or use `src/update_stats.py`.
4. Validate your changes:
   ```bash
   # Run automated test suite
   uv run pytest

   # Run linter
   uv run ruff check .
   ```
5. Commit your changes:
   ```bash
   git commit -m "Add/Update/Remove <Plugin Name>"
   ```
6. Push to your fork and open a Pull Request.

---

## Automated Validation & CI

Every Pull Request and IssueOps run automatically executes CI checks:

- JSON schema validity and plugin-ID deduplication in `plugins.json`.
- Markdown link and Table of Contents synchronization with `README.md`.
- Repository URL parsing across supported formats.
- Code style and lint passing with zero errors (`ruff check`).
- Full pytest test suite execution.
- Weekly scheduled refresh of live stats (stars, forks, last updated) plus upstream registry re-sync.
