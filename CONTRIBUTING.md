# Contributing to Awesome Omarchy Plugins

**Awesome Omarchy Plugins** is an automated visual catalog and showcase for plugins in the Omarchy ecosystem.

This repository focuses purely on presenting rich data: generating and formatting [`README.md`](README.md) (sorted by stars) and [`BY_UPDATED.md`](BY_UPDATED.md) (sorted by recency).

---

## Where to Submit New Plugins

Plugin submissions are maintained upstream in the **Omarchy Plugin Marketplace**:
👉 [omacom/omarchy-plugin-marketplace](https://github.com/omacom/omarchy-plugin-marketplace)

Once merged upstream, new plugins are automatically imported into this catalog during the weekly synchronization run.

---

## Local Development & Rendering

To run the catalog generator locally:

```bash
# Fast local rendering (regenerates README.md and BY_UPDATED.md without network calls)
uv run python src/update_stats.py --render-only

# Full enrichment (fetches live stars/forks from GitHub via GraphQL batching)
uv run python src/update_stats.py

# Sync skeleton with upstream registry
uv run python scripts/sync_registry.py
```

### Running Validation

Before opening a pull request, ensure tests and linters pass:

```bash
uv run ruff check .
uv run pytest -v
```
