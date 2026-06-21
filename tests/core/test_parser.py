"""Tests for symbol parsing and relation derivation."""

from pathlib import Path

from synapse.core.models import SymbolKind
from synapse.core.parser import (
    _assign_containers,
    _ExtractedSymbol,
    _qualified_name,
    build_reference_relations,
    build_relations,
    extract_references,
    parse_file,
)


def test_parse_file_extracts_python_symbols_with_nesting(tmp_path: Path) -> None:
    """The parser extracts classes, methods, variables, constants, and imports."""
    file_path = tmp_path / "sample.py"
    file_path.write_text(
        "import os\n"
        "from pkg import thing\n\n"
        "VALUE = 1\n"
        "name = 2\n\n"
        "class Example:\n"
        "    FIELD = 3\n"
        "    attr = 4\n\n"
        "    def method(self):\n"
        "        return self.attr\n\n"
        "def helper():\n"
        "    return VALUE\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)
    by_name = {symbol.name: symbol for symbol in symbols}

    assert by_name["Example"].kind == "class"
    assert by_name["method"].kind == "method"
    assert by_name["helper"].kind == "function"
    assert by_name["VALUE"].kind == "constant"
    assert by_name["FIELD"].kind == "constant"
    assert by_name["attr"].kind == "field"
    assert by_name["name"].kind == "variable"
    assert by_name["method"].container_id == by_name["Example"].id
    assert by_name["method"].qualified_name == "Example.method"
    assert by_name["helper"].id == "python:sample.py:function:helper:14"


def test_equal_span_symbols_do_not_become_each_others_containers() -> None:
    """Equal Tree-sitter match spans do not create recursive parent cycles."""
    items = [
        _ExtractedSymbol(
            language="python",
            kind=SymbolKind.IMPORT,
            native_kind="import_statement",
            name="first",
            file_path="sample.py",
            start_line=1,
            end_line=1,
            start_byte=0,
            end_byte=10,
            signature="import first",
        ),
        _ExtractedSymbol(
            language="python",
            kind=SymbolKind.IMPORT,
            native_kind="import_statement",
            name="second",
            file_path="sample.py",
            start_line=1,
            end_line=1,
            start_byte=0,
            end_byte=10,
            signature="import second",
        ),
    ]

    _assign_containers(items)
    for index, _ in enumerate(items):
        _qualified_name(items, index)

    assert [item.container_index for item in items] == [None, None]
    assert [item.qualified_name for item in items] == ["first", "second"]


def test_parse_file_extracts_csharp_symbols_with_nesting(tmp_path: Path) -> None:
    """The parser extracts C# classes, members, and using directives with nesting."""
    file_path = tmp_path / "sample.cs"
    file_path.write_text(
        "using System;\n"
        "using System.Text;\n\n"
        "namespace Sample.App\n"
        "{\n"
        "    public class Greeter\n"
        "    {\n"
        "        private int _count;\n\n"
        "        public string Name { get; set; }\n\n"
        "        public Greeter(string name)\n"
        "        {\n"
        "            Name = name;\n"
        "        }\n\n"
        "        public string Greet()\n"
        "        {\n"
        "            _count++;\n"
        "            return Name;\n"
        "        }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    greet = by_kind_name[("method", "Greet")]
    constructor = by_kind_name[("constructor", "Greeter")]
    assert greeter.native_kind == "class_declaration"
    assert constructor.name == "Greeter"
    assert by_kind_name[("property", "Name")].name == "Name"
    assert by_kind_name[("field", "_count")].name == "_count"
    assert by_kind_name[("namespace", "Sample.App")].name == "Sample.App"
    assert {symbol.name for symbol in symbols if symbol.kind == "import"} == {
        "System",
        "System.Text",
    }
    assert greet.container_id == greeter.id
    assert greet.qualified_name is not None
    assert greet.qualified_name.endswith("Greeter.Greet")


def test_parse_file_extracts_dart_symbols(tmp_path: Path) -> None:
    """The parser extracts Dart classes, mixins, enums, methods, and functions."""
    file_path = tmp_path / "sample.dart"
    file_path.write_text(
        "class Greeter {\n"
        '    final String name = "hi";\n\n'
        "    String greet() {\n"
        "        return name;\n"
        "    }\n"
        "}\n\n"
        "mixin Speaker {\n"
        "    void speak() {}\n"
        "}\n\n"
        "enum Color { red, green }\n\n"
        "int helper() {\n"
        "    return 1;\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "dart", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    greet = by_kind_name[("method", "greet")]
    assert by_kind_name[("class", "Speaker")].name == "Speaker"
    assert by_kind_name[("enum", "Color")].name == "Color"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert greet.container_id == greeter.id


def test_parse_file_extracts_c_symbols(tmp_path: Path) -> None:
    """The parser extracts C imports, types, fields, functions, and variables."""
    file_path = tmp_path / "sample.c"
    file_path.write_text(
        "#include <stdio.h>\n"
        "#define MAX 10\n\n"
        "typedef struct Point {\n"
        "    int x;\n"
        "} Point;\n\n"
        "enum Color { RED, GREEN };\n\n"
        "int count = 0;\n\n"
        "int add(int left, int right) {\n"
        "    return left + right;\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "c", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    point = by_kind_name[("struct", "Point")]
    x = by_kind_name[("field", "x")]
    assert by_kind_name[("import", "<stdio.h>")].name == "<stdio.h>"
    assert by_kind_name[("constant", "MAX")].name == "MAX"
    assert by_kind_name[("type", "Point")].name == "Point"
    assert by_kind_name[("enum", "Color")].name == "Color"
    assert by_kind_name[("variable", "count")].name == "count"
    assert by_kind_name[("function", "add")].name == "add"
    assert x.container_id == point.id


def test_parse_file_extracts_cpp_symbols(tmp_path: Path) -> None:
    """The parser extracts C++ namespaces, types, methods, and fields."""
    file_path = tmp_path / "sample.cpp"
    file_path.write_text(
        "namespace Sample {\n"
        "class Greeter {\n"
        "public:\n"
        "    int count;\n"
        "    int greet() {\n"
        "        return count;\n"
        "    }\n"
        "};\n\n"
        "struct Point {\n"
        "    int x;\n"
        "};\n\n"
        "enum Color { Red, Green };\n\n"
        "int helper() {\n"
        "    return 1;\n"
        "}\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "cpp", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    greet = by_kind_name[("method", "greet")]
    assert by_kind_name[("namespace", "Sample")].name == "Sample"
    assert by_kind_name[("field", "count")].name == "count"
    assert by_kind_name[("struct", "Point")].name == "Point"
    assert by_kind_name[("enum", "Color")].name == "Color"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert greet.container_id == greeter.id


def test_parse_file_extracts_go_symbols(tmp_path: Path) -> None:
    """The parser extracts Go packages, declarations, and struct members."""
    file_path = tmp_path / "sample.go"
    file_path.write_text(
        "package main\n"
        'import "fmt"\n\n'
        "const Pi = 3\n"
        'var greeting = "hi"\n\n'
        "type Person struct {\n"
        "    Name string\n"
        "}\n\n"
        "type Greeter interface {\n"
        "    Greet() string\n"
        "}\n\n"
        "func main() {\n"
        "    fmt.Println(greeting)\n"
        "}\n\n"
        "func (p Person) Greet() string {\n"
        "    return p.Name\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "go", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    person = by_kind_name[("struct", "Person")]
    name = by_kind_name[("field", "Name")]
    assert by_kind_name[("package", "main")].name == "main"
    assert by_kind_name[("import", '"fmt"')].name == '"fmt"'
    assert by_kind_name[("constant", "Pi")].name == "Pi"
    assert by_kind_name[("variable", "greeting")].name == "greeting"
    assert by_kind_name[("interface", "Greeter")].name == "Greeter"
    assert by_kind_name[("function", "main")].name == "main"
    assert by_kind_name[("method", "Greet")].name == "Greet"
    assert name.container_id == person.id


def test_parse_file_extracts_java_symbols(tmp_path: Path) -> None:
    """The parser extracts Java packages, types, members, and enum constants."""
    file_path = tmp_path / "Sample.java"
    file_path.write_text(
        "package com.example;\n"
        "import java.util.List;\n\n"
        "class Greeter {\n"
        "    private int count;\n"
        '    public static final String NAME = "x";\n'
        "    Greeter() {}\n"
        "    String greet() { return NAME; }\n"
        "}\n\n"
        "interface Speaker { String speak(); }\n"
        "enum Color { RED, GREEN }\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "java", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    greet = by_kind_name[("method", "greet")]
    assert by_kind_name[("package", "com.example")].name == "com.example"
    assert by_kind_name[("import", "java.util.List")].name == "java.util.List"
    assert by_kind_name[("field", "count")].name == "count"
    assert by_kind_name[("constant", "NAME")].name == "NAME"
    assert by_kind_name[("constructor", "Greeter")].name == "Greeter"
    assert by_kind_name[("interface", "Speaker")].name == "Speaker"
    assert by_kind_name[("enum", "Color")].name == "Color"
    assert by_kind_name[("constant", "RED")].name == "RED"
    assert by_kind_name[("constant", "GREEN")].name == "GREEN"
    assert greet.container_id == greeter.id


def test_parse_file_extracts_javascript_symbols(tmp_path: Path) -> None:
    """The parser extracts JavaScript imports, functions, classes, and members."""
    file_path = tmp_path / "sample.js"
    file_path.write_text(
        'import { thing } from "./mod";\n'
        "export const Widget = () => thing();\n"
        "const VALUE = 1;\n"
        "let name = 2;\n\n"
        "class Example {\n"
        "    field = 3;\n"
        "    method() { return thing(); }\n"
        "}\n\n"
        "function helper() { return new Example(); }\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "javascript", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    example = by_kind_name[("class", "Example")]
    method = by_kind_name[("method", "method")]
    assert by_kind_name[("import", '"./mod"')].name == '"./mod"'
    assert by_kind_name[("variable", "Widget")].name == "Widget"
    assert by_kind_name[("constant", "VALUE")].name == "VALUE"
    assert by_kind_name[("variable", "name")].name == "name"
    assert by_kind_name[("field", "field")].name == "field"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert method.container_id == example.id


def test_parse_file_extracts_kotlin_symbols(tmp_path: Path) -> None:
    """The parser extracts Kotlin packages, imports, types, members, and constants."""
    file_path = tmp_path / "sample.kt"
    file_path.write_text(
        "package sample\n\n"
        "import helper\n\n"
        "const val MAX = 10\n\n"
        "class Greeter {\n"
        '    val name = "hi"\n\n'
        "    fun greet(): String {\n"
        "        return name\n"
        "    }\n"
        "}\n\n"
        "interface Speaker\n\n"
        "enum class Color { RED, GREEN }\n\n"
        "fun helper() = MAX\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "kotlin", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    greet = by_kind_name[("method", "greet")]
    assert by_kind_name[("package", "sample")].name == "sample"
    assert by_kind_name[("import", "helper")].name == "helper"
    assert by_kind_name[("constant", "MAX")].name == "MAX"
    assert by_kind_name[("field", "name")].name == "name"
    assert by_kind_name[("interface", "Speaker")].name == "Speaker"
    assert by_kind_name[("enum", "Color")].name == "Color"
    assert by_kind_name[("constant", "RED")].name == "RED"
    assert by_kind_name[("constant", "GREEN")].name == "GREEN"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert greet.container_id == greeter.id


def test_parse_file_extracts_lua_symbols(tmp_path: Path) -> None:
    """The parser extracts Lua functions and table-style methods."""
    file_path = tmp_path / "sample.lua"
    file_path.write_text(
        "local M = {}\n\n"
        "function helper()\n"
        "    return 1\n"
        "end\n\n"
        "function M.greet()\n"
        "    return helper()\n"
        "end\n\n"
        "return M\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "lua", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("function", "helper")].name == "helper"
    assert by_kind_name[("method", "greet")].name == "greet"


def test_parse_file_extracts_php_symbols(tmp_path: Path) -> None:
    """The parser extracts PHP namespaces, imports, types, members, and functions."""
    file_path = tmp_path / "sample.php"
    file_path.write_text(
        "<?php\n"
        "namespace App\\Demo;\n\n"
        "use DateTime;\n\n"
        "const LIMIT = 3;\n\n"
        "interface Speaker {\n"
        "    public function speak();\n"
        "}\n\n"
        "enum Color {\n"
        "    case Red;\n"
        "}\n\n"
        "class Greeter implements Speaker {\n"
        "    private string $name;\n\n"
        "    public function __construct(string $name) {\n"
        "        $this->name = $name;\n"
        "    }\n\n"
        "    public function greet(): string {\n"
        "        return $this->name;\n"
        "    }\n"
        "}\n\n"
        "function helper(): int {\n"
        "    return LIMIT;\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "php", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    greet = by_kind_name[("method", "greet")]
    assert by_kind_name[("namespace", "App\\Demo")].name == "App\\Demo"
    assert by_kind_name[("import", "DateTime")].name == "DateTime"
    assert by_kind_name[("constant", "LIMIT")].name == "LIMIT"
    assert by_kind_name[("interface", "Speaker")].name == "Speaker"
    assert by_kind_name[("enum", "Color")].name == "Color"
    assert by_kind_name[("field", "name")].name == "name"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert greet.container_id == greeter.id


def test_parse_file_extracts_rust_symbols(tmp_path: Path) -> None:
    """The parser extracts Rust imports, modules, types, and members."""
    file_path = tmp_path / "sample.rs"
    file_path.write_text(
        "use std::fmt;\n\n"
        "const MAX: i32 = 10;\n"
        "type Alias = i32;\n\n"
        "mod nested {\n"
        "    fn helper() {}\n"
        "}\n\n"
        "struct Point {\n"
        "    x: i32,\n"
        "}\n\n"
        "enum Color { Red, Green }\n\n"
        "trait Speaker {}\n\n"
        "impl Point {\n"
        "    fn area(&self) -> i32 {\n"
        "        self.x\n"
        "    }\n"
        "}\n\n"
        "fn main() {}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "rust", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    point = by_kind_name[("struct", "Point")]
    x = by_kind_name[("field", "x")]
    assert by_kind_name[("import", "std::fmt")].name == "std::fmt"
    assert by_kind_name[("constant", "MAX")].name == "MAX"
    assert by_kind_name[("type", "Alias")].name == "Alias"
    assert by_kind_name[("module", "nested")].name == "nested"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert by_kind_name[("enum", "Color")].name == "Color"
    assert by_kind_name[("interface", "Speaker")].name == "Speaker"
    assert by_kind_name[("method", "area")].name == "area"
    assert by_kind_name[("function", "main")].name == "main"
    assert x.container_id == point.id


def test_parse_file_extracts_ruby_symbols(tmp_path: Path) -> None:
    """The parser extracts Ruby modules, classes, constants, and methods."""
    file_path = tmp_path / "sample.rb"
    file_path.write_text(
        "module Sample\n"
        "  VALUE = 1\n\n"
        "  class Greeter\n"
        "    def greet\n"
        "      VALUE\n"
        "    end\n\n"
        "    def self.build\n"
        "      new\n"
        "    end\n"
        "  end\n"
        "end\n\n"
        "def helper\n"
        "  Sample::VALUE\n"
        "end\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "ruby", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    greet = by_kind_name[("method", "greet")]
    assert by_kind_name[("module", "Sample")].name == "Sample"
    assert by_kind_name[("constant", "VALUE")].name == "VALUE"
    assert by_kind_name[("method", "build")].name == "build"
    assert by_kind_name[("method", "helper")].name == "helper"
    assert greet.container_id == greeter.id


def test_parse_file_extracts_scala_symbols(tmp_path: Path) -> None:
    """The parser extracts Scala packages, objects, types, values, and functions."""
    file_path = tmp_path / "sample.scala"
    file_path.write_text(
        "package sample.app\n\n"
        "import scala.util.Try\n\n"
        "object Main {\n"
        "  val greeting = \"hi\"\n\n"
        "  def helper(): String = greeting\n"
        "}\n\n"
        "class Greeter {\n"
        "  def greet(): String = Main.helper()\n"
        "}\n\n"
        "trait Speaker {\n"
        "  def speak(): String\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "scala", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    main = by_kind_name[("module", "Main")]
    helper = by_kind_name[("function", "helper")]
    assert by_kind_name[("package", "sample.app")].name == "sample.app"
    assert by_kind_name[("class", "Greeter")].name == "Greeter"
    assert by_kind_name[("interface", "Speaker")].name == "Speaker"
    assert by_kind_name[("field", "greeting")].name == "greeting"
    assert by_kind_name[("function", "greet")].name == "greet"
    assert by_kind_name[("function", "speak")].name == "speak"
    assert helper.container_id == main.id


def test_parse_file_extracts_swift_symbols(tmp_path: Path) -> None:
    """The parser extracts Swift imports, types, members, and free functions."""
    file_path = tmp_path / "sample.swift"
    file_path.write_text(
        "import Foundation\n\n"
        "class Greeter {\n"
        "    let name: String\n\n"
        "    func greet() -> String {\n"
        "        return name\n"
        "    }\n"
        "}\n\n"
        "struct Point {\n"
        "    let x: Int\n"
        "}\n\n"
        "enum Color {\n"
        "    case red\n"
        "    case green\n"
        "}\n\n"
        "protocol Speaker {}\n\n"
        "func helper() -> Int {\n"
        "    return 1\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "swift", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    greet = by_kind_name[("method", "greet")]
    assert by_kind_name[("import", "Foundation")].name == "Foundation"
    assert by_kind_name[("field", "name")].name == "name"
    assert by_kind_name[("struct", "Point")].name == "Point"
    assert by_kind_name[("enum", "Color")].name == "Color"
    assert by_kind_name[("interface", "Speaker")].name == "Speaker"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert greet.container_id == greeter.id


def test_parse_file_extracts_typescript_symbols(tmp_path: Path) -> None:
    """The parser extracts TypeScript declarations and class members."""
    file_path = tmp_path / "sample.ts"
    file_path.write_text(
        "interface Shape { area(): number; }\n"
        "type ID = string;\n"
        "enum Status { Active, Inactive }\n\n"
        "class Box {\n"
        "    size: number = 0;\n"
        "    area(): number { return this.size; }\n"
        "}\n\n"
        "function make(): Box { return new Box(); }\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "typescript", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    box = by_kind_name[("class", "Box")]
    area = by_kind_name[("method", "area")]
    assert by_kind_name[("interface", "Shape")].name == "Shape"
    assert by_kind_name[("type", "ID")].name == "ID"
    assert by_kind_name[("enum", "Status")].name == "Status"
    assert by_kind_name[("field", "size")].name == "size"
    assert by_kind_name[("function", "make")].name == "make"
    assert area.container_id == box.id


def test_parse_file_extracts_tsx_symbols(tmp_path: Path) -> None:
    """TSX reuses TypeScript queries while retaining the TSX language id."""
    file_path = tmp_path / "App.tsx"
    file_path.write_text(
        'import React from "react";\n'
        "interface Props { title: string }\n"
        "export const Shell = () => <main />;\n"
        "function App(props: Props) { return <div>{props.title}</div>; }\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "tsx", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("interface", "Props")].name == "Props"
    assert by_kind_name[("variable", "Shell")].name == "Shell"
    assert by_kind_name[("function", "App")].name == "App"
    assert all(symbol.id.startswith("tsx:") for symbol in symbols)


def test_build_relations_emits_contains_and_imports(tmp_path: Path) -> None:
    """Relations mirror container links (CONTAINS) and imports (IMPORTS)."""
    file_path = tmp_path / "sample.py"
    file_path.write_text(
        "import os\n\nclass Example:\n    def method(self):\n        return 1\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)
    by_name = {symbol.name: symbol for symbol in symbols}
    relations = build_relations(symbols)

    contains = [relation for relation in relations if relation.kind == "contains"]
    imports = [relation for relation in relations if relation.kind == "imports"]
    assert any(
        relation.from_symbol_id == by_name["Example"].id
        and relation.to_symbol_id == by_name["method"].id
        for relation in contains
    )
    assert len(imports) == 1
    assert imports[0].to_name == "os"
    assert imports[0].to_symbol_id is None
    assert str(imports[0].confidence) == "high"


def test_extract_references_and_resolve_candidates(tmp_path: Path) -> None:
    """Reference queries find call sites and the resolver records confidence."""
    file_path = tmp_path / "sample.py"
    file_path.write_text(
        "def target():\n    return 1\n\ndef caller():\n    return target()\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)
    target = next(symbol for symbol in symbols if symbol.name == "target")
    raw_refs = extract_references(file_path, "python", symbols)

    assert [raw_ref.name for raw_ref in raw_refs] == ["target"]
    resolved = build_reference_relations(raw_refs, {"target": [target.id]})
    assert resolved[0].to_symbol_id == target.id
    assert str(resolved[0].confidence) == "high"
    low = build_reference_relations(raw_refs, {})
    assert low[0].to_symbol_id is None
    assert str(low[0].confidence) == "low"
    medium = build_reference_relations(raw_refs, {"target": ["one", "two"]})
    assert medium[0].to_symbol_id is None
    assert str(medium[0].confidence) == "medium"
