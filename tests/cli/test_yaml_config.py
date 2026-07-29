"""YAML config merge behaviour for the mapping and list container shapes.

Hermes stores MCP servers as a mapping under ``mcp_servers``; Continue stores
them as a list under ``mcpServers`` keyed by an inner ``name``. Both files are
shared with unrelated user configuration, so these tests pin down what survives
a Synapse install and uninstall.
"""

from pathlib import Path

import pytest

from synapse.cli.adapters import get_adapter
from synapse.cli.config_codecs import dumps, loads
from synapse.cli.installer import (
    config_has_mcp_server,
    install_mcp_server,
    resolve_config_path,
    uninstall_mcp_server,
)

HERMES_CONFIG = """\
# Hermes configuration, hand written.
model: hermes-4.1
terminal:
  theme: dark          # inline comment
compression:
  enabled: true
memory:
  max_entries: 500
skills:
  external_dirs:
    - ~/work/skills
mcp_servers:
  github:
    command: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-github"
  filesystem:
    command: fs-mcp
    enabled: false
tool_output:
  max_chars: 20000
"""

CONTINUE_CONFIG = """\
name: My Assistant
version: 1.0.0
schema: v1
models:
  - name: GPT-4
    provider: openai
    model: gpt-4
rules:
  - Give concise responses
mcpServers:
  - name: DevServer
    command: npm
    args:
      - run
      - dev
  - name: Browser search
    command: npx
    args:
      - "@playwright/mcp@latest"
"""


def _hermes_config(isolated_home: Path, tmp_path: Path) -> Path:
    target = resolve_config_path(get_adapter("hermes"), tmp_path, "user")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(HERMES_CONFIG, encoding="utf-8")
    return target


def _continue_config(isolated_home: Path, tmp_path: Path) -> Path:
    target = resolve_config_path(get_adapter("continue"), tmp_path, "user")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(CONTINUE_CONFIG, encoding="utf-8")
    return target


def test_hermes_mapping_merge_preserves_every_sibling_key(
    isolated_home: Path, tmp_path: Path
) -> None:
    """Installing into config.yaml touches only mcp_servers.synapse."""
    target = _hermes_config(isolated_home, tmp_path)

    install_mcp_server("hermes", tmp_path, scope="user", portable=True)

    data = loads(get_adapter("hermes").mcp.fmt, target.read_text(encoding="utf-8"), str(target))
    assert data["model"] == "hermes-4.1"
    assert data["terminal"]["theme"] == "dark"
    assert data["compression"]["enabled"] is True
    assert data["memory"]["max_entries"] == 500
    assert data["skills"]["external_dirs"] == ["~/work/skills"]
    assert data["tool_output"]["max_chars"] == 20000
    assert data["mcp_servers"]["synapse"] == {"command": "synapse", "args": ["serve"]}


def test_hermes_merge_preserves_multiple_unrelated_servers(
    isolated_home: Path, tmp_path: Path
) -> None:
    """Neighbouring MCP servers keep their own fields and order."""
    target = _hermes_config(isolated_home, tmp_path)

    install_mcp_server("hermes", tmp_path, scope="user", portable=True)

    data = loads(get_adapter("hermes").mcp.fmt, target.read_text(encoding="utf-8"), str(target))
    servers = data["mcp_servers"]
    assert list(servers) == ["github", "filesystem", "synapse"]
    assert servers["github"]["args"] == ["-y", "@modelcontextprotocol/server-github"]
    assert servers["filesystem"] == {"command": "fs-mcp", "enabled": False}


def test_hermes_comments_survive_install_and_uninstall(isolated_home: Path, tmp_path: Path) -> None:
    """Round-trip YAML keeps user comments that a plain dump would discard."""
    target = _hermes_config(isolated_home, tmp_path)

    install_mcp_server("hermes", tmp_path, scope="user", portable=True)
    after_install = target.read_text(encoding="utf-8")
    uninstall_mcp_server("hermes", tmp_path, scope="user")

    assert "# Hermes configuration, hand written." in after_install
    assert "# inline comment" in after_install
    assert target.read_text(encoding="utf-8") == HERMES_CONFIG


def test_yaml_anchors_and_aliases_survive_a_round_trip(isolated_home: Path, tmp_path: Path) -> None:
    """Anchor/alias pairs and nested unrelated data are preserved verbatim."""
    target = resolve_config_path(get_adapter("hermes"), tmp_path, "user")
    target.parent.mkdir(parents=True, exist_ok=True)
    original = (
        "defaults: &defaults\n"
        "  timeout: 30\n"
        "auxiliary:\n"
        "  nested:\n"
        "    deep:\n"
        "      value: keep\n"
        "mcp_servers:\n"
        "  github:\n"
        "    command: gh-mcp\n"
        "    timeout: *defaults\n"
    )
    target.write_text(original, encoding="utf-8")

    install_mcp_server("hermes", tmp_path, scope="user", portable=True)
    text = target.read_text(encoding="utf-8")
    uninstall_mcp_server("hermes", tmp_path, scope="user")

    assert "&defaults" in text
    assert "*defaults" in text
    assert "value: keep" in text
    assert target.read_text(encoding="utf-8") == original


def test_continue_list_entry_is_matched_on_name(isolated_home: Path, tmp_path: Path) -> None:
    """The list shape adds one entry keyed by its inner name field."""
    target = _continue_config(isolated_home, tmp_path)

    install_mcp_server("continue", tmp_path, scope="user", portable=True)

    data = loads(get_adapter("continue").mcp.fmt, target.read_text(encoding="utf-8"), str(target))
    servers = data["mcpServers"]
    assert [entry["name"] for entry in servers] == ["DevServer", "Browser search", "synapse"]
    assert servers[-1] == {"name": "synapse", "command": "synapse", "args": ["serve"]}


def test_continue_reinstall_replaces_in_place_without_duplicating(
    isolated_home: Path, tmp_path: Path
) -> None:
    """A second install updates the existing list entry rather than appending."""
    target = _continue_config(isolated_home, tmp_path)
    install_mcp_server("continue", tmp_path, scope="user", portable=True)

    second = install_mcp_server("continue", tmp_path, scope="user", portable=True)
    pinned = install_mcp_server("continue", tmp_path, scope="user", force=True)

    data = loads(get_adapter("continue").mcp.fmt, target.read_text(encoding="utf-8"), str(target))
    names = [entry["name"] for entry in data["mcpServers"]]
    assert second.action == "unchanged"
    assert pinned.action == "updated"
    assert names.count("synapse") == 1
    assert len(names) == 3


def test_continue_uninstall_preserves_list_order_and_unknown_fields(
    isolated_home: Path, tmp_path: Path
) -> None:
    """Removing the Synapse entry leaves every other list entry untouched."""
    target = _continue_config(isolated_home, tmp_path)
    install_mcp_server("continue", tmp_path, scope="user", portable=True)

    uninstall_mcp_server("continue", tmp_path, scope="user")

    assert target.read_text(encoding="utf-8") == CONTINUE_CONFIG


def test_continue_project_block_file_is_self_contained(isolated_home: Path, tmp_path: Path) -> None:
    """The project block file carries Continue's mandatory metadata."""
    target = resolve_config_path(get_adapter("continue"), tmp_path, "project")

    install_mcp_server("continue", tmp_path, scope="project", portable=True)

    data = loads(get_adapter("continue").mcp.fmt, target.read_text(encoding="utf-8"), str(target))
    assert target.name == "synapse.yaml"
    assert data["name"] == "Synapse"
    assert data["version"] == "0.0.1"
    assert data["schema"] == "v1"
    assert data["mcpServers"] == [{"name": "synapse", "command": "synapse", "args": ["serve"]}]


@pytest.mark.parametrize("agent", ["hermes", "continue"])
def test_malformed_yaml_is_rejected_without_writing(
    agent: str, isolated_home: Path, tmp_path: Path
) -> None:
    """Invalid YAML raises a clear error and the file is left byte-identical."""
    target = resolve_config_path(get_adapter(agent), tmp_path, "user")
    target.parent.mkdir(parents=True, exist_ok=True)
    broken = "mcp_servers:\n  github:\n   command: [unclosed\n  bad: : :\n"
    target.write_text(broken, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid YAML"):
        install_mcp_server(agent, tmp_path, scope="user", portable=True)

    assert target.read_text(encoding="utf-8") == broken
    assert not config_has_mcp_server(agent, tmp_path, scope="user")


def test_existing_synapse_entry_requires_force_to_change(
    isolated_home: Path, tmp_path: Path
) -> None:
    """A differing Synapse entry is protected until --force is passed."""
    target = _hermes_config(isolated_home, tmp_path)
    install_mcp_server("hermes", tmp_path, scope="user", portable=True)

    with pytest.raises(FileExistsError, match="use --force"):
        install_mcp_server("hermes", tmp_path, scope="user", python_executable="/py")
    replaced = install_mcp_server(
        "hermes", tmp_path, scope="user", python_executable="/py", force=True
    )

    data = loads(get_adapter("hermes").mcp.fmt, target.read_text(encoding="utf-8"), str(target))
    assert replaced.action == "updated"
    assert data["mcp_servers"]["synapse"]["command"] == "/py"


def test_non_mapping_yaml_root_is_rejected(isolated_home: Path, tmp_path: Path) -> None:
    """A YAML document that is not a mapping cannot be merged into."""
    target = resolve_config_path(get_adapter("hermes"), tmp_path, "user")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a mapping"):
        install_mcp_server("hermes", tmp_path, scope="user", portable=True)


def test_empty_yaml_file_is_treated_as_an_empty_document(
    isolated_home: Path, tmp_path: Path
) -> None:
    """A blank config file is a valid starting point, not a parse error."""
    target = resolve_config_path(get_adapter("hermes"), tmp_path, "user")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n", encoding="utf-8")

    install_mcp_server("hermes", tmp_path, scope="user", portable=True)

    assert config_has_mcp_server("hermes", tmp_path, scope="user")


def test_unused_yaml_anchors_are_not_preserved(isolated_home: Path, tmp_path: Path) -> None:
    """Known ruamel limitation: an anchor with no alias is dropped on rewrite.

    Semantic content is unaffected, which is what the merge guarantees.
    """
    target = resolve_config_path(get_adapter("hermes"), tmp_path, "user")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("mcp_servers:\n  github: &unused\n    command: gh-mcp\n", encoding="utf-8")

    install_mcp_server("hermes", tmp_path, scope="user", portable=True)

    text = target.read_text(encoding="utf-8")
    assert "&unused" not in text
    assert "command: gh-mcp" in text


def test_yaml_dump_round_trips_through_loads(isolated_home: Path) -> None:
    """The codec is symmetric for the documents these adapters produce."""
    fmt = get_adapter("hermes").mcp.fmt

    data = loads(fmt, HERMES_CONFIG, "test")

    assert dumps(fmt, data) == HERMES_CONFIG
