"""Tests for creating and editing ignore files."""

import json
from pathlib import Path

import pytest

from synapse.core.config import (
    ConfigScope,
    active_ignore_matcher,
    add_ignore_patterns,
    global_ignore_path,
    migrate_ignores,
    project_config_path,
    remove_ignore_patterns,
    synapseignore_path,
    write_global_ignored_directories,
    write_ignore_file,
    write_project_ignored_directories,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return an isolated workspace root with an isolated global config directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def test_add_creates_the_file_with_a_header(workspace: Path) -> None:
    """The first add creates .synapseignore, header included."""
    result = add_ignore_patterns(workspace, ["*.min.js"])

    text = synapseignore_path(workspace).read_text(encoding="utf-8")
    assert result.created is True
    assert result.added == ("*.min.js",)
    assert text.startswith("# .synapseignore")
    assert text.endswith("*.min.js\n")


def test_add_appends_and_preserves_everything_already_there(workspace: Path) -> None:
    """Comments, blank lines, and order survive; exactly one line is appended, at the end."""
    original = "# keep me\n\ndist/\n\n# trailing note\n"
    synapseignore_path(workspace).write_text(original, encoding="utf-8")

    add_ignore_patterns(workspace, ["*.min.js"])

    assert synapseignore_path(workspace).read_text(encoding="utf-8") == f"{original}*.min.js\n"


def test_add_is_idempotent(workspace: Path) -> None:
    """Re-adding a pattern reports it instead of duplicating the line."""
    add_ignore_patterns(workspace, ["*.min.js"])
    result = add_ignore_patterns(workspace, ["*.min.js"])

    assert result.added == ()
    assert result.already_present == ("*.min.js",)
    assert synapseignore_path(workspace).read_text(encoding="utf-8").count("*.min.js") == 1


def test_creating_the_file_migrates_legacy_json_entries(workspace: Path) -> None:
    """Adopting .synapseignore moves the JSON entries in and drops the key it replaced."""
    project_config_path(workspace).parent.mkdir(parents=True)
    project_config_path(workspace).write_text(
        json.dumps(
            {"ignored_directories": ["legacy", "src/generated"], "watch": {"batch_size": 8}}
        ),
        encoding="utf-8",
    )

    result = add_ignore_patterns(workspace, ["*.min.js"])

    text = synapseignore_path(workspace).read_text(encoding="utf-8")
    assert result.migrated_from_json == ("legacy", "src/generated")
    assert "legacy/" in text
    assert "/src/generated/" in text

    payload = json.loads(project_config_path(workspace).read_text(encoding="utf-8"))
    assert "ignored_directories" not in payload
    assert payload["watch"] == {"batch_size": 8}


def test_remove_deletes_an_owned_line_and_leaves_the_rest(workspace: Path) -> None:
    """A pattern this file owns is deleted outright; comments are untouched."""
    synapseignore_path(workspace).write_text("# note\ndist/\n*.min.js\n", encoding="utf-8")

    result = remove_ignore_patterns(workspace, ["dist/"])

    assert result.removed == ("dist/",)
    assert synapseignore_path(workspace).read_text(encoding="utf-8") == "# note\n*.min.js\n"


def test_remove_negates_a_builtin_instead_of_failing(workspace: Path) -> None:
    """A built-in cannot be deleted, so it is negated — and the matcher then re-includes it."""
    result = remove_ignore_patterns(workspace, ["node_modules/"])

    assert result.removed == ()
    assert result.negated == ("!node_modules/",)
    assert not active_ignore_matcher(workspace).ignores_child((), "node_modules")


def test_remove_negates_a_global_entry_instead_of_failing(workspace: Path) -> None:
    """An entry inherited from the global config is negated locally, not an error."""
    write_global_ignored_directories({"cache"})

    result = remove_ignore_patterns(workspace, ["cache/"])

    assert result.negated == ("!cache/",)
    assert not active_ignore_matcher(workspace).ignores_child(("pkg",), "cache")


def test_remove_reports_an_unknown_pattern_without_writing_a_negation(workspace: Path) -> None:
    """Something that was never ignored is reported, not negated and not an error."""
    result = remove_ignore_patterns(workspace, ["never-ignored/"])

    assert result.not_present == ("never-ignored/",)
    assert result.negated == ()
    assert "!never-ignored/" not in synapseignore_path(workspace).read_text(encoding="utf-8")


def test_crlf_files_keep_their_line_endings(workspace: Path) -> None:
    """A file written on Windows is not silently rewritten to LF."""
    path = synapseignore_path(workspace)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("dist/\r\n")

    add_ignore_patterns(workspace, ["*.min.js"])

    with path.open(encoding="utf-8", newline="") as handle:
        assert handle.read() == "dist/\r\n*.min.js\r\n"


def test_writes_leave_no_temporary_file_behind(workspace: Path) -> None:
    """The atomic write cleans up its pid-suffixed temporary file."""
    add_ignore_patterns(workspace, ["*.min.js"])

    assert list(workspace.glob(".synapseignore.*.tmp")) == []


def test_migrate_moves_json_entries_and_refuses_to_clobber(workspace: Path) -> None:
    """Migration is explicit, and will not overwrite an existing file without force."""
    write_project_ignored_directories(workspace, {"legacy"})

    result = migrate_ignores(workspace)
    assert result.migrated_from_json == ("legacy",)
    assert "legacy/" in synapseignore_path(workspace).read_text(encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        migrate_ignores(workspace)

    migrate_ignores(workspace, force=True)


def test_global_scope_writes_the_global_ignore_file(workspace: Path) -> None:
    """The global scope has its own ignore file, parallel to the project one."""
    result = add_ignore_patterns(workspace, ["scratch/"], scope=ConfigScope.GLOBAL)

    assert result.path == global_ignore_path()
    assert "scratch/" in global_ignore_path().read_text(encoding="utf-8")
    assert not synapseignore_path(workspace).exists()


def test_write_ignore_file_replaces_the_whole_file(workspace: Path) -> None:
    """A forced rewrite replaces the previous contents rather than merging into them."""
    synapseignore_path(workspace).write_text("# old\nold/\n", encoding="utf-8")

    write_ignore_file(workspace, ["new/"], header="# fresh\n")

    assert synapseignore_path(workspace).read_text(encoding="utf-8") == "# fresh\n\nnew/\n"


def test_writing_to_the_builtin_scope_is_refused(workspace: Path) -> None:
    """Built-in rules ship with Synapse and are not a writable layer."""
    with pytest.raises(ValueError, match="cannot be written to"):
        add_ignore_patterns(workspace, ["x/"], scope=ConfigScope.BUILT_IN)
