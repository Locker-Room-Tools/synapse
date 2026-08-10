"""Tests for the `synapse ignore` command."""

import json
from pathlib import Path

import pytest

from synapse.cli import main as cli_main
from synapse.core.config import (
    project_config_path,
    synapseignore_path,
    write_project_ignored_directories,
)
from synapse.core.config.ignore_presets import PRESETS


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return an isolated workspace root with an isolated global config directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


def test_init_writes_a_flat_file_for_a_named_preset(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A named preset produces a plain file: header, a section comment, then patterns."""
    exit_code = cli_main.main(["ignore", "init", "--node", "--path", str(workspace)])

    text = synapseignore_path(workspace).read_text(encoding="utf-8")
    assert exit_code == 0
    assert "Created" in capsys.readouterr().out
    assert text.startswith("# .synapseignore")
    assert "# Presets: node." in text
    assert "# Node.js" in text
    assert "*.min.js" in text
    # No managed markers: the file belongs to the repository from here on.
    assert "BEGIN" not in text


def test_init_detects_the_ecosystem_when_no_preset_is_named(workspace: Path) -> None:
    """With no flags, init detects presets from marker files near the root."""
    (workspace / "package.json").write_text("{}\n", encoding="utf-8")
    (workspace / "go.mod").write_text("module x\n", encoding="utf-8")

    exit_code = cli_main.main(["ignore", "init", "--path", str(workspace)])

    text = synapseignore_path(workspace).read_text(encoding="utf-8")
    assert exit_code == 0
    assert "# Presets: node, go." in text


def test_init_detects_markers_one_level_down(workspace: Path) -> None:
    """A monorepo keeps its manifests in subdirectories, so one level is scanned."""
    (workspace / "packages").mkdir()
    (workspace / "packages" / "package.json").write_text("{}\n", encoding="utf-8")

    cli_main.main(["ignore", "init", "--path", str(workspace)])

    assert "# Presets: node." in synapseignore_path(workspace).read_text(encoding="utf-8")


def test_init_fails_clearly_when_nothing_is_detected(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An undetectable workspace gets an actionable error, not an empty file."""
    exit_code = cli_main.main(["ignore", "init", "--path", str(workspace)])

    assert exit_code == 2
    assert "No ecosystem detected" in capsys.readouterr().err
    assert not synapseignore_path(workspace).exists()


def test_init_refuses_to_overwrite_without_force(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The file is version-controlled, so init never silently replaces it."""
    synapseignore_path(workspace).write_text("# mine\ncustom/\n", encoding="utf-8")

    exit_code = cli_main.main(["ignore", "init", "--node", "--path", str(workspace)])

    assert exit_code == 2
    assert "already exists" in capsys.readouterr().err
    assert synapseignore_path(workspace).read_text(encoding="utf-8") == "# mine\ncustom/\n"


def test_init_force_rewrites_the_whole_file(workspace: Path) -> None:
    """--force is the explicit opt-in to losing the previous contents."""
    synapseignore_path(workspace).write_text("# mine\ncustom/\n", encoding="utf-8")

    exit_code = cli_main.main(["ignore", "init", "--node", "--force", "--path", str(workspace)])

    text = synapseignore_path(workspace).read_text(encoding="utf-8")
    assert exit_code == 0
    assert "custom/" not in text
    assert "*.min.js" in text


def test_init_dry_run_writes_nothing(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dry run prints the file it would write and leaves the workspace untouched."""
    exit_code = cli_main.main(["ignore", "init", "--node", "--dry-run", "--path", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Would write" in captured.out
    assert "*.min.js" in captured.out
    assert not synapseignore_path(workspace).exists()


def test_add_preset_appends_without_reordering(workspace: Path) -> None:
    """--preset extends an existing file instead of rewriting it."""
    synapseignore_path(workspace).write_text("# mine\ncustom/\n", encoding="utf-8")

    exit_code = cli_main.main(["ignore", "add", "--preset", "go", "--path", str(workspace)])

    text = synapseignore_path(workspace).read_text(encoding="utf-8")
    assert exit_code == 0
    assert text.startswith("# mine\ncustom/\n")
    assert "*.pb.go" in text


def test_add_requires_something_to_add(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An add with neither a pattern nor a preset is an error, not a silent no-op."""
    exit_code = cli_main.main(["ignore", "add", "--path", str(workspace)])

    assert exit_code == 2
    assert "at least one pattern" in capsys.readouterr().err


def test_add_glob_creates_the_file_and_migrates_legacy_entries(workspace: Path) -> None:
    """Adopting the ignore file moves the legacy JSON entries in and drops the key."""
    write_project_ignored_directories(workspace, {"legacy"})

    exit_code = cli_main.main(["ignore", "add", "*.min.js", "--path", str(workspace)])

    payload = json.loads(project_config_path(workspace).read_text(encoding="utf-8"))
    assert exit_code == 0
    assert "legacy/" in synapseignore_path(workspace).read_text(encoding="utf-8")
    assert "ignored_directories" not in payload


def test_remove_negates_a_builtin_and_exits_zero(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Removing a built-in used to exit 2; it now appends a negation and succeeds."""
    exit_code = cli_main.main(["ignore", "remove", "node_modules/", "--path", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "negated: !node_modules/" in captured.out
    assert "!node_modules/" in synapseignore_path(workspace).read_text(encoding="utf-8")


def test_list_shows_order_provenance_and_problems(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Listing is the honest view: order, layer, line, negations, and skipped lines."""
    write_project_ignored_directories(workspace, {"legacy"})
    synapseignore_path(workspace).write_text(
        "*.min.js\n!node_modules/\n[unterminated\n", encoding="utf-8"
    )

    with pytest.warns(UserWarning, match="supersedes ignored_directories"):
        exit_code = cli_main.main(["ignore", "list", "--path", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "built-in  node_modules/" in captured.out
    assert "project   *.min.js" in captured.out
    assert "project   !node_modules/" in captured.out
    assert "Last matching rule wins" in captured.out
    assert "Skipped:" in captured.out
    assert "synapse ignore migrate" in captured.out


def test_migrate_moves_legacy_entries(workspace: Path) -> None:
    """Migration is the explicit way out of the legacy JSON list."""
    write_project_ignored_directories(workspace, {"legacy", "src/generated"})

    exit_code = cli_main.main(["ignore", "migrate", "--path", str(workspace)])

    text = synapseignore_path(workspace).read_text(encoding="utf-8")
    assert exit_code == 0
    assert "legacy/" in text
    assert "/src/generated/" in text


def test_presets_lists_every_template(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every shipped preset is discoverable, with the markers that would select it."""
    (workspace / "package.json").write_text("{}\n", encoding="utf-8")

    exit_code = cli_main.main(["ignore", "presets", "--path", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 0
    for preset_id in PRESETS:
        assert preset_id in captured.out
    assert "* node" in captured.out
    assert "opt-in only" in captured.out


def test_every_preset_template_compiles(workspace: Path) -> None:
    """A shipped template with an unusable line would silently do nothing; catch it here."""
    from synapse.core.config import ConfigScope, parse_ignore_text
    from synapse.core.config.ignore_presets import preset_patterns

    for preset_id in PRESETS:
        patterns = preset_patterns(preset_id)
        rules, problems = parse_ignore_text(
            "\n".join(patterns), scope=ConfigScope.PROJECT, origin=preset_id
        )
        assert problems == ()
        assert len(rules) == len(patterns)
