"""Tests for workspace crawling and hashing."""

import hashlib
from pathlib import Path

from synapse.core.crawler import hash_file, iter_source_files


def test_iter_source_files_skips_ignored_directories(tmp_path: Path) -> None:
    """The crawler yields only supported files outside ignored directories."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "pkg" / "lib.cs").write_text("class Program {}\n", encoding="utf-8")
    (tmp_path / "pkg" / "notes.txt").write_text("ignored\n", encoding="utf-8")
    (tmp_path / ".git" / "hidden.py").write_text("print('nope')\n", encoding="utf-8")
    (tmp_path / "node_modules" / "vendor.py").write_text("print('nope')\n", encoding="utf-8")

    files = list(iter_source_files(tmp_path))

    assert files == [tmp_path / "pkg" / "app.py", tmp_path / "pkg" / "lib.cs"]


def test_hash_file_returns_stable_sha256_digest(tmp_path: Path) -> None:
    """File hashing uses deterministic SHA-256 digests."""
    file_path = tmp_path / "sample.py"
    file_path.write_text("print('synapse')\n", encoding="utf-8")

    assert hash_file(file_path) == hashlib.sha256(file_path.read_bytes()).hexdigest()