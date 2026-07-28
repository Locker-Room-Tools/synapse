"""Adversarial-input tests for the parser."""

from pathlib import Path

from synapse.core.indexing.parser import extract_references, parse_file


def test_parse_file_handles_empty_file(tmp_path: Path) -> None:
    """A zero-byte source file yields no symbols and no references."""
    file_path = tmp_path / "empty.py"
    file_path.write_bytes(b"")

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)

    assert symbols == []
    assert extract_references(file_path, "python", symbols) == []


def test_parse_file_survives_syntax_errors(tmp_path: Path) -> None:
    """Broken source parses via tree-sitter error recovery instead of raising."""
    file_path = tmp_path / "broken.py"
    file_path.write_text(
        "def broken(:\n    return\n\nclass Intact:\n    def method(self):\n        return 1\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)

    assert {symbol.name for symbol in symbols} >= {"Intact", "method"}


def test_parse_file_survives_binary_content(tmp_path: Path) -> None:
    """Binary bytes behind a source extension do not crash the parser."""
    file_path = tmp_path / "binary.py"
    file_path.write_bytes(bytes(range(256)) * 16)

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)

    assert isinstance(symbols, list)


def test_parse_file_survives_non_utf8_encoding(tmp_path: Path) -> None:
    """Latin-1 content decodes with replacement characters instead of raising."""
    file_path = tmp_path / "latin.py"
    file_path.write_bytes('CAFÉ = "café"\ndef helper():\n    return CAFÉ\n'.encode("latin-1"))

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)

    assert any(symbol.name == "helper" for symbol in symbols)


def test_parse_file_extracts_unicode_identifiers(tmp_path: Path) -> None:
    """Non-ASCII identifiers keep their exact names."""
    file_path = tmp_path / "unicode.py"
    file_path.write_text(
        "class Класс:\n    def метод(self):\n        return 1\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)
    by_name = {symbol.name: symbol for symbol in symbols}

    assert by_name["Класс"].kind == "class"
    assert by_name["метод"].qualified_name == "Класс.метод"
