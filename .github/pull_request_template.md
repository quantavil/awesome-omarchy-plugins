### Type of Change

Please check the option that best describes your PR:

- [ ] **Add a new plugin** (GitHub repository URL)
- [ ] **Edit / Update existing plugin details** (Description, Category, Tags)
- [ ] **Remove a plugin** (Repository deleted, retired upstream, broken, author request)

---

### Plugin Details (if adding or editing)

- **Plugin Name**:
- **Plugin ID**: `...`
- **Repository URL**: `https://...`
- **Category**:
- **Tags**: (e.g. bar, quickshell, system)
- **License**: (e.g. MIT, GPL-3.0, Apache-2.0)

---

### Removal Reason (if requesting removal)

- **Reason**:

---

### Contribution Checklist

- [ ] Repository is open-source under a recognized license.
- [ ] Plugin is an Omarchy shell plugin and is functional.
- [ ] `plugins.json` and `README.md` are in sync (`uv run python src/update_stats.py`).
- [ ] Ran automated tests and they pass: `uv run pytest`.
- [ ] Ran linter and it passes: `uv run ruff check .`.
