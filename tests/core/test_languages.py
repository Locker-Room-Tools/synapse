"""Tests for the supported language registry."""

from pathlib import Path

from synapse.core.languages import detect_language, to_treesitter_name


def test_detect_language_by_extension() -> None:
    """Known extensions map to normalized language ids."""
    assert detect_language(Path("main.c")) == "c"
    assert detect_language(Path("header.h")) == "c"
    assert detect_language(Path("main.cpp")) == "cpp"
    assert detect_language(Path("header.hpp")) == "cpp"
    assert detect_language(Path("module.py")) == "python"
    assert detect_language(Path("Program.cs")) == "csharp"
    assert detect_language(Path("main.dart")) == "dart"
    assert detect_language(Path("main.go")) == "go"
    assert detect_language(Path("App.java")) == "java"
    assert detect_language(Path("index.js")) == "javascript"
    assert detect_language(Path("App.kt")) == "kotlin"
    assert detect_language(Path("init.lua")) == "lua"
    assert detect_language(Path("index.php")) == "php"
    assert detect_language(Path("lib.rs")) == "rust"
    assert detect_language(Path("app.rb")) == "ruby"
    assert detect_language(Path("Main.scala")) == "scala"
    assert detect_language(Path("App.swift")) == "swift"
    assert detect_language(Path("ui.jsx")) == "javascript"
    assert detect_language(Path("util.ts")) == "typescript"
    assert detect_language(Path("App.tsx")) == "tsx"


def test_detect_language_returns_none_for_unknown_extensions() -> None:
    """Unknown file extensions are ignored."""
    assert detect_language(Path("README.md")) is None


def test_to_treesitter_name_uses_language_specific_mapping() -> None:
    """Tree-sitter naming stays separate from normalized language ids."""
    assert to_treesitter_name("c") == "c"
    assert to_treesitter_name("cpp") == "cpp"
    assert to_treesitter_name("python") == "python"
    assert to_treesitter_name("csharp") == "csharp"
    assert to_treesitter_name("dart") == "dart"
    assert to_treesitter_name("go") == "go"
    assert to_treesitter_name("java") == "java"
    assert to_treesitter_name("javascript") == "javascript"
    assert to_treesitter_name("kotlin") == "kotlin"
    assert to_treesitter_name("lua") == "lua"
    assert to_treesitter_name("php") == "php"
    assert to_treesitter_name("rust") == "rust"
    assert to_treesitter_name("ruby") == "ruby"
    assert to_treesitter_name("scala") == "scala"
    assert to_treesitter_name("swift") == "swift"
    assert to_treesitter_name("typescript") == "typescript"
    assert to_treesitter_name("tsx") == "tsx"
