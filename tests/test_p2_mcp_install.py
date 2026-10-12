"""`composerbench mcp install`: merge, back up, stay idempotent."""
import json

import pytest

from composerbench.mcp.install import client_config_path, install, merge_config, server_entry

ENTRY = {"command": "/venv/bin/python", "args": ["-m", "composerbench.mcp.server"],
         "env": {"COMPOSERBENCH_ADAPTER": "magda_serve"}}


def test_merge_keeps_other_servers_and_backs_up(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    result = merge_config(cfg, ENTRY)
    data = json.loads(cfg.read_text())
    assert result["status"] == "updated" and data["theme"] == "dark"
    assert data["mcpServers"] == {"other": {"command": "x"}, "composerbench": ENTRY}
    assert json.loads(open(result["backup"]).read())["mcpServers"] == {"other": {"command": "x"}}
    again = merge_config(cfg, ENTRY)
    assert again["status"] == "unchanged" and "backup" not in again


def test_merge_creates_missing_file_and_refuses_bad_json_shapes(tmp_path):
    fresh = tmp_path / "deep" / "mcp.json"
    assert merge_config(fresh, ENTRY)["status"] == "updated"
    assert json.loads(fresh.read_text())["mcpServers"]["composerbench"] == ENTRY
    odd = tmp_path / "odd.json"
    odd.write_text("[1, 2]")
    with pytest.raises(ValueError):
        merge_config(odd, ENTRY)


def test_dry_run_writes_nothing(tmp_path):
    results = install(["cursor", "claude-desktop"], ENTRY, dry_run=True, home=tmp_path)
    assert [r["status"] for r in results] == ["would-update", "would-update"]
    assert not client_config_path("cursor", tmp_path).exists()


def test_server_entry_is_self_contained():
    entry = server_entry(python="/venv/bin/python")
    assert entry["args"] == ["-m", "composerbench.mcp.server"]
    assert entry["env"]["PYTHONPATH"] and entry["env"]["PATH"] and entry["env"]["COMPOSERBENCH_WORKSPACE"]
