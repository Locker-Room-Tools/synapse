"""Tests for the supported language registry."""

from collections import Counter
from pathlib import Path

import pytest

from synapse.core.languages import (
    LANGUAGES,
    LanguageSpec,
    _build_extension_map,
    detect_language,
    to_treesitter_name,
)


def test_detect_language_by_extension() -> None:
    """Known extensions map to normalized language ids."""
    assert detect_language(Path("script.sh")) == "bash"
    assert detect_language(Path("script.bash")) == "bash"
    assert detect_language(Path("test.bats")) == "bash"
    assert detect_language(Path("main.c")) == "c"
    assert detect_language(Path("header.h")) == "c"
    assert detect_language(Path("main.cpp")) == "cpp"
    assert detect_language(Path("header.hpp")) == "cpp"
    assert detect_language(Path("module.py")) == "python"
    assert detect_language(Path("Program.cs")) == "csharp"
    assert detect_language(Path("main.dart")) == "dart"
    assert detect_language(Path("lib.ex")) == "elixir"
    assert detect_language(Path("script.exs")) == "elixir"
    assert detect_language(Path("server.erl")) == "erlang"
    assert detect_language(Path("types.fsi")) == "fsharp"
    assert detect_language(Path("main.go")) == "go"
    assert detect_language(Path("build.gradle")) == "groovy"
    assert detect_language(Path("Main.hs")) == "haskell"
    assert detect_language(Path("App.java")) == "java"
    assert detect_language(Path("index.js")) == "javascript"
    assert detect_language(Path("notebook.jl")) == "julia"
    assert detect_language(Path("App.kt")) == "kotlin"
    assert detect_language(Path("init.lua")) == "lua"
    assert detect_language(Path("main.m")) == "matlab"
    assert detect_language(Path("Widget.mm")) == "objc"
    assert detect_language(Path("demo.ml")) == "ocaml"
    assert detect_language(Path("core.clj")) == "clojure"
    assert detect_language(Path("script.pl")) == "perl"
    assert detect_language(Path("index.php")) == "php"
    assert detect_language(Path("script.ps1")) == "powershell"
    assert detect_language(Path("Module.psm1")) == "powershell"
    assert detect_language(Path("Data.psd1")) == "powershell"
    assert detect_language(Path("analysis.r")) == "r"
    assert detect_language(Path("analysis.R")) == "r"
    assert detect_language(Path("lib.rs")) == "rust"
    assert detect_language(Path("app.rb")) == "ruby"
    assert detect_language(Path("Main.scala")) == "scala"
    assert detect_language(Path("schema.sql")) == "sql"
    assert detect_language(Path("App.svelte")) == "svelte"
    assert detect_language(Path("App.swift")) == "swift"
    assert detect_language(Path("ui.jsx")) == "javascript"
    assert detect_language(Path("util.ts")) == "typescript"
    assert detect_language(Path("App.tsx")) == "tsx"
    assert detect_language(Path("App.vue")) == "vue"
    assert detect_language(Path("main.zig")) == "zig"
    assert detect_language(Path("main.adb")) == "ada"
    assert detect_language(Path("spec.ads")) == "ada"
    assert detect_language(Path("kernel.asm")) == "assembly"
    assert detect_language(Path("boot.s")) == "assembly"
    assert detect_language(Path("Component.astro")) == "astro"
    assert detect_language(Path("payroll.cbl")) == "cobol"
    assert detect_language(Path("system.cob")) == "cobol"
    assert detect_language(Path("package.lisp")) == "common_lisp"
    assert detect_language(Path("system.asd")) == "common_lisp"
    assert detect_language(Path("app.cr")) == "crystal"
    assert detect_language(Path("kernel.cu")) == "cuda"
    assert detect_language(Path("header.cuh")) == "cuda"
    assert detect_language(Path("module.d")) == "d"
    assert detect_language(Path("program.f90")) == "fortran"
    assert detect_language(Path("legacy.for")) == "fortran"
    assert detect_language(Path("Main.elm")) == "elm"
    assert detect_language(Path("shader.frag")) == "glsl"
    assert detect_language(Path("shader.glsl")) == "glsl"
    assert detect_language(Path("main.gleam")) == "gleam"
    assert detect_language(Path("effect.hlsl")) == "hlsl"
    assert detect_language(Path("theme.less")) == "less"
    assert detect_language(Path("app.nim")) == "nim"
    assert detect_language(Path("unit.pas")) == "pascal"
    assert detect_language(Path("unit.pp")) == "pascal"
    assert detect_language(Path("App.purs")) == "purescript"
    assert detect_language(Path("Button.res")) == "rescript"
    assert detect_language(Path("Button.resi")) == "rescript"
    assert detect_language(Path("style.scss")) == "scss"
    assert detect_language(Path("method.st")) == "smalltalk"
    assert detect_language(Path("design.v")) == "verilog"
    assert detect_language(Path("defs.vh")) == "verilog"
    assert detect_language(Path("entity.vhd")) == "vhdl"
    assert detect_language(Path("entity.vhdl")) == "vhdl"
    assert detect_language(Path("shader.wgsl")) == "wgsl"
    assert detect_language(Path("config.fish")) == "fish"
    assert detect_language(Path("plugin.zsh")) == "zsh"
    assert detect_language(Path("pipeline.nu")) == "nushell"
    assert detect_language(Path("report.awk")) == "awk"
    assert detect_language(Path("init.vim")) == "vimscript"
    assert detect_language(Path("init.el")) == "emacs_lisp"
    assert detect_language(Path("player.gd")) == "gdscript"
    assert detect_language(Path("game.luau")) == "luau"
    assert detect_language(Path("Main.hx")) == "haxe"


def test_detect_language_returns_none_for_unknown_extensions() -> None:
    """Unknown file extensions are ignored."""
    assert detect_language(Path("README.md")) is None
    assert detect_language(Path("module.sv")) is None
    assert detect_language(Path("module.svh")) is None


def test_detect_language_uses_documented_collision_policy() -> None:
    """Extension collisions stay deterministic until content-aware detection exists."""
    assert detect_language(Path("main.m")) == "matlab"
    assert detect_language(Path("main.mm")) == "objc"
    assert detect_language(Path("header.h")) == "c"
    assert detect_language(Path("design.v")) == "verilog"


def test_detect_language_matches_angular_component_templates() -> None:
    """Angular's compound suffix convention routes templates to angular_template."""
    assert detect_language(Path("app.component.html")) == "angular_template"
    assert detect_language(Path("APP.COMPONENT.HTML")) == "angular_template"
    assert detect_language(Path("index.html")) is None
    assert detect_language(Path(".component.html")) is None


def test_extension_map_rejects_duplicate_claims() -> None:
    """Two languages claiming one extension is a registry bug, not a silent override."""
    specs = [
        LanguageSpec(id="a", tree_sitter_name="a", extensions=(".x",), query_dir="a"),
        LanguageSpec(id="b", tree_sitter_name="b", extensions=(".x",), query_dir="b"),
    ]
    with pytest.raises(ValueError, match="claimed by both"):
        _build_extension_map(specs)


def test_to_treesitter_name_uses_language_specific_mapping() -> None:
    """Tree-sitter naming stays separate from normalized language ids."""
    assert to_treesitter_name("bash") == "bash"
    assert to_treesitter_name("c") == "c"
    assert to_treesitter_name("cpp") == "cpp"
    assert to_treesitter_name("python") == "python"
    assert to_treesitter_name("csharp") == "csharp"
    assert to_treesitter_name("dart") == "dart"
    assert to_treesitter_name("elixir") == "elixir"
    assert to_treesitter_name("erlang") == "erlang"
    assert to_treesitter_name("fsharp") == "fsharp"
    assert to_treesitter_name("go") == "go"
    assert to_treesitter_name("groovy") == "groovy"
    assert to_treesitter_name("haskell") == "haskell"
    assert to_treesitter_name("java") == "java"
    assert to_treesitter_name("javascript") == "javascript"
    assert to_treesitter_name("julia") == "julia"
    assert to_treesitter_name("kotlin") == "kotlin"
    assert to_treesitter_name("lua") == "lua"
    assert to_treesitter_name("matlab") == "matlab"
    assert to_treesitter_name("objc") == "objc"
    assert to_treesitter_name("ocaml") == "ocaml"
    assert to_treesitter_name("angular_template") == "html"
    assert to_treesitter_name("clojure") == "clojure"
    assert to_treesitter_name("perl") == "perl"
    assert to_treesitter_name("php") == "php"
    assert to_treesitter_name("powershell") == "powershell"
    assert to_treesitter_name("r") == "r"
    assert to_treesitter_name("rust") == "rust"
    assert to_treesitter_name("ruby") == "ruby"
    assert to_treesitter_name("scala") == "scala"
    assert to_treesitter_name("sql") == "sql"
    assert to_treesitter_name("svelte") == "svelte"
    assert to_treesitter_name("swift") == "swift"
    assert to_treesitter_name("typescript") == "typescript"
    assert to_treesitter_name("tsx") == "tsx"
    assert to_treesitter_name("vue") == "html"
    assert to_treesitter_name("zig") == "zig"
    assert to_treesitter_name("ada") == "ada"
    assert to_treesitter_name("assembly") == "asm"
    assert to_treesitter_name("astro") == "astro"
    assert to_treesitter_name("cobol") == "cobol"
    assert to_treesitter_name("common_lisp") == "commonlisp"
    assert to_treesitter_name("crystal") == "crystal"
    assert to_treesitter_name("cuda") == "cuda"
    assert to_treesitter_name("d") == "d"
    assert to_treesitter_name("elm") == "elm"
    assert to_treesitter_name("fortran") == "fortran"
    assert to_treesitter_name("gleam") == "gleam"
    assert to_treesitter_name("glsl") == "glsl"
    assert to_treesitter_name("hlsl") == "hlsl"
    assert to_treesitter_name("less") == "less"
    assert to_treesitter_name("nim") == "nim"
    assert to_treesitter_name("pascal") == "pascal"
    assert to_treesitter_name("purescript") == "purescript"
    assert to_treesitter_name("rescript") == "rescript"
    assert to_treesitter_name("scss") == "scss"
    assert to_treesitter_name("smalltalk") == "smalltalk"
    assert to_treesitter_name("verilog") == "verilog"
    assert to_treesitter_name("vhdl") == "vhdl"
    assert to_treesitter_name("wgsl") == "wgsl"
    assert to_treesitter_name("fish") == "fish"
    assert to_treesitter_name("zsh") == "zsh"
    assert to_treesitter_name("nushell") == "nushell"
    assert to_treesitter_name("awk") == "awk"
    assert to_treesitter_name("vimscript") == "vim"
    assert to_treesitter_name("emacs_lisp") == "elisp"
    assert to_treesitter_name("gdscript") == "gdscript"
    assert to_treesitter_name("luau") == "luau"
    assert to_treesitter_name("haxe") == "haxe"


def test_language_registry_has_no_duplicate_extensions() -> None:
    """Each auto-detected extension maps to one language id."""
    counts = Counter(
        extension for language in LANGUAGES.values() for extension in language.extensions
    )
    assert counts.most_common(1)[0][1] == 1
