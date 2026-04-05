from pathlib import Path
import yaml


def test_yaml_inventory_plugin_load(tmp_path, monkeypatch):
    # Put repo root on cwd so package imports resolve when tests run from anywhere
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    cfg_file = tmp_path / "inventory.yaml"
    data = [
        "10.0.0.1",
        {"host": "10.0.0.2", "port": 9443, "labels": {"role": "idrac"}},
        {"targets": ["10.0.0.3:443", "http://example.com:8080"], "labels": {"env": "prod"}},
        {"target": ["10.0.0.4", "10.0.0.5"]},
    ]

    cfg_file.write_text(yaml.safe_dump(data))

    monkeypatch.setenv("INVENTORY_CONFIG_PATH", str(cfg_file))

    from redfishagent.plugins.inventory.yaml.main import YamlInventoryPlugin

    plugin = YamlInventoryPlugin()
    results = plugin.load()

    assert isinstance(results, list)
    # Expect at least the normalized entries to be produced
    assert len(results) >= 3

    # Flatten targets for easier assertions
    all_targets = []
    for e in results:
        if getattr(e, "targets", None):
            all_targets.extend(e.targets)

    # The HTTP URL should be preserved as-is
    assert any(t.startswith("http://example.com") for t in all_targets)

    # Non-http addresses should be shortened to host portion (port removed)
    assert "10.0.0.1" in all_targets
    assert "10.0.0.3" in all_targets
