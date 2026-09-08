"""Unit tests for registry synchronization logic."""

from scripts.sync_registry import build_skeleton


class TestSyncRegistry:
    def test_build_skeleton_preserves_unique_plugin_names(self):
        mock_registry = {
            "sources": [
                {
                    "repo": "https://github.com/bjarneo/omarchy-shell-plugins",
                    "type": "plugin-source",
                    "addedAt": "2026-07-28",
                    "plugins": {
                        "omni": {
                            "category": "Productivity",
                            "tags": ["launcher"],
                        },
                        "quickapps-hud": {
                            "name": "QuickApps HUD",
                            "category": "Desktop",
                            "tags": ["launcher"],
                        },
                    },
                }
            ]
        }

        skeleton = build_skeleton(mock_registry)
        assert len(skeleton) == 2
        entry_omni = next(e for e in skeleton if e["plugin_id"] == "omni")
        entry_hud = next(e for e in skeleton if e["plugin_id"] == "quickapps-hud")

        assert entry_omni["name"] == "omni"
        assert entry_hud["name"] == "QuickApps HUD"
        assert entry_omni["owner"] == "bjarneo"
        assert entry_omni["repo"] == "omarchy-shell-plugins"

    def test_zero_star_metadata_preservation(self, monkeypatch):
        from scripts import sync_registry

        # Simulated upstream skeleton
        mock_skeleton = [
            {
                "plugin_id": "zero-star-plugin",
                "owner": "user",
                "repo": "repo",
                "name": "zero-star-plugin",
                "description": "",
                "category": "Other",
                "stars": 0,
                "forks": 0,
                "last_updated": "N/A",
            }
        ]
        # Simulated existing catalog with enriched metadata and 0 stars
        mock_existing = [
            {
                "plugin_id": "zero-star-plugin",
                "owner": "user",
                "repo": "repo",
                "name": "Custom Name",
                "description": "Custom Description",
                "category": "Widgets",
                "stars": 0,
                "forks": 0,
                "last_updated": "2026-08-15",
                "license": "MIT",
                "language": "QML",
            }
        ]

        saved = []
        monkeypatch.setattr(sync_registry, "fetch_registry", lambda url: {"sources": []})
        monkeypatch.setattr(sync_registry, "build_skeleton", lambda reg: mock_skeleton)
        monkeypatch.setattr(sync_registry, "load_plugins", lambda: mock_existing)
        monkeypatch.setattr(sync_registry, "save_plugins_atomic", lambda data: saved.extend(data))

        # Run main logic
        import argparse

        args = argparse.Namespace(registry_url="http://mock", dry_run=False)
        monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: args)

        sync_registry.main()

        assert len(saved) == 1
        entry = saved[0]
        # Enriched metadata must be preserved despite 0 stars
        assert entry["last_updated"] == "2026-08-15"
        assert entry["license"] == "MIT"
        assert entry["language"] == "QML"
        assert entry["name"] == "Custom Name"
        assert entry["description"] == "Custom Description"
