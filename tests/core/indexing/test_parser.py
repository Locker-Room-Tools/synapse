"""Tests for symbol parsing and relation derivation."""

from pathlib import Path

from synapse.core.indexing.parser import (
    _assign_containers,
    _candidate_symbol_ids,
    _capture_kind_to_symbol_kind,
    _ExtractedSymbol,
    _qualified_name,
    build_reference_relations,
    build_relations,
    extract_references,
    parse_file,
)
from synapse.core.models import ResolutionMethod, SymbolKind


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


def test_parse_file_extracts_bash_symbols(tmp_path: Path) -> None:
    """The parser extracts Bash functions, assignments, and local references."""
    file_path = tmp_path / "sample.sh"
    file_path.write_text(
        'VALUE=1\nname=world\n\nhelper() {\n    echo "$VALUE"\n}\n\nmain() {\n    helper\n}\n',
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "bash", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("function", "helper")].name == "helper"
    assert by_kind_name[("function", "main")].name == "main"
    assert by_kind_name[("constant", "VALUE")].name == "VALUE"
    assert by_kind_name[("variable", "name")].name == "name"

    references = extract_references(file_path, "bash", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names
    assert "VALUE" in reference_names


def test_parse_file_extracts_powershell_symbols(tmp_path: Path) -> None:
    """The parser extracts PowerShell classes, members, functions, and references."""
    file_path = tmp_path / "sample.ps1"
    file_path.write_text(
        "class Greeter {\n"
        "    [string] $Name\n\n"
        "    [string] Greet() {\n"
        "        return $this.Name\n"
        "    }\n"
        "}\n\n"
        "function Invoke-Greeting {\n"
        "    Get-Item .\n"
        "    $g = [Greeter]::new()\n"
        "    $g.Greet()\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "powershell", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    greet = by_kind_name[("method", "Greet")]
    assert by_kind_name[("function", "Invoke-Greeting")].name == "Invoke-Greeting"
    assert ("field", "$Name") in by_kind_name or ("field", "Name") in by_kind_name
    assert greet.container_id == greeter.id

    references = extract_references(file_path, "powershell", symbols)
    reference_names = {reference.name for reference in references}
    assert "Get-Item" in reference_names
    assert "Greet" in reference_names


def test_parse_file_extracts_r_symbols(tmp_path: Path) -> None:
    """The parser extracts R functions, assignments, imports, and references."""
    file_path = tmp_path / "sample.R"
    file_path.write_text(
        "library(stats)\n"
        "VALUE <- 1\n"
        "threshold = 0.5\n\n"
        "fit_model <- function(data) {\n"
        "    model <- lm(y ~ x, data=data)\n"
        "    stats::predict(model)\n"
        "}\n\n"
        "report = function(data) {\n"
        "    fit_model(data)\n"
        "}\n\n"
        "(function(x) x) -> summarize\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "r", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    fit_model = by_kind_name[("function", "fit_model")]
    model = by_kind_name[("variable", "model")]
    assert by_kind_name[("function", "report")].name == "report"
    assert by_kind_name[("function", "summarize")].name == "summarize"
    assert by_kind_name[("constant", "VALUE")].name == "VALUE"
    assert by_kind_name[("variable", "threshold")].name == "threshold"
    assert by_kind_name[("import", "stats")].name == "stats"
    assert model.container_id == fit_model.id

    references = extract_references(file_path, "r", symbols)
    reference_names = {reference.name for reference in references}
    assert "lm" in reference_names
    assert "fit_model" in reference_names
    assert "predict" in reference_names


def test_parse_file_extracts_julia_symbols(tmp_path: Path) -> None:
    """The parser extracts Julia modules, types, functions, imports, and references."""
    file_path = tmp_path / "sample.jl"
    file_path.write_text(
        "module DemoML\n"
        "using Statistics\n"
        "const VALUE = 1\n\n"
        "struct Dataset\n"
        "    values\n"
        "end\n\n"
        "abstract type Model end\n\n"
        "function train(data)\n"
        "    return mean(data)\n"
        "end\n\n"
        "macro trace(expr)\n"
        "    return expr\n"
        "end\n\n"
        "score = train([1, 2, 3])\n"
        "end\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "julia", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    module = by_kind_name[("module", "DemoML")]
    train = by_kind_name[("function", "train")]
    assert by_kind_name[("import", "Statistics")].name == "Statistics"
    assert by_kind_name[("constant", "VALUE")].name == "VALUE"
    assert by_kind_name[("struct", "Dataset")].name == "Dataset"
    assert by_kind_name[("type", "Model")].name == "Model"
    assert by_kind_name[("function", "trace")].name == "trace"
    assert by_kind_name[("variable", "score")].name == "score"
    assert train.container_id == module.id
    assert train.qualified_name is not None
    assert train.qualified_name.endswith("DemoML.train")

    references = extract_references(file_path, "julia", symbols)
    reference_names = {reference.name for reference in references}
    assert "mean" in reference_names
    assert "train" in reference_names


def test_parse_file_extracts_haskell_symbols(tmp_path: Path) -> None:
    """The parser extracts Haskell modules, imports, types, and functions."""
    file_path = tmp_path / "sample.hs"
    file_path.write_text(
        "module Demo where\n"
        "import Data.List\n\n"
        "data Person = Person\n\n"
        "helper x = x\n"
        "main = helper 1\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "haskell", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("module", "Demo")].name == "Demo"
    assert by_kind_name[("import", "Data.List")].name == "Data.List"
    assert by_kind_name[("type", "Person")].name == "Person"
    assert by_kind_name[("function", "helper")].name == "helper"

    references = extract_references(file_path, "haskell", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


def test_parse_file_extracts_elixir_symbols(tmp_path: Path) -> None:
    """The parser extracts Elixir modules, imports, functions, fields, and refs."""
    file_path = tmp_path / "sample.ex"
    file_path.write_text(
        "defmodule Demo do\n"
        "  import Kernel\n"
        "  defstruct [:name]\n"
        "  def greet(name), do: helper(name)\n"
        "  defp helper(name), do: name\n"
        "end\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "elixir", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("module", "Demo")].name == "Demo"
    assert by_kind_name[("import", "Kernel")].name == "Kernel"
    assert by_kind_name[("field", ":name")].name == ":name"
    assert by_kind_name[("function", "greet")].name == "greet"
    assert by_kind_name[("function", "helper")].name == "helper"

    references = extract_references(file_path, "elixir", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


def test_parse_file_extracts_ocaml_symbols(tmp_path: Path) -> None:
    """The parser extracts OCaml modules, types, functions, and refs."""
    file_path = tmp_path / "sample.ml"
    file_path.write_text(
        "module Demo = struct\n"
        "  type person = { name: string }\n"
        "  let helper x = x\n"
        "end\n"
        "let value = Demo.helper 1\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "ocaml", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("module", "Demo")].name == "Demo"
    assert by_kind_name[("type", "person")].name == "person"
    assert by_kind_name[("function", "helper")].name == "helper"

    references = extract_references(file_path, "ocaml", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


def test_parse_file_extracts_fsharp_symbols(tmp_path: Path) -> None:
    """The parser extracts F# modules, imports, records, values, and refs."""
    file_path = tmp_path / "sample.fs"
    file_path.write_text(
        "module Demo\n"
        "open System\n"
        "type Person = { Name: string }\n"
        "let helper x = x\n"
        "let value = helper 1\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "fsharp", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("module", "Demo")].name == "Demo"
    assert by_kind_name[("import", "System")].name == "System"
    assert by_kind_name[("record", "Person")].name == "Person"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert by_kind_name[("variable", "value")].name == "value"

    references = extract_references(file_path, "fsharp", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


def test_parse_file_extracts_clojure_symbols(tmp_path: Path) -> None:
    """The parser extracts Clojure namespaces, vars, records, and refs."""
    file_path = tmp_path / "sample.clj"
    file_path.write_text(
        "(ns demo.core (:require [clojure.string :as str]))\n"
        "(def VALUE 1)\n"
        "(defn helper [x] (str/upper-case x))\n"
        "(defrecord Person [name])\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "clojure", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("module", "demo.core")].name == "demo.core"
    assert by_kind_name[("constant", "VALUE")].name == "VALUE"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert by_kind_name[("record", "Person")].name == "Person"

    references = extract_references(file_path, "clojure", symbols)
    reference_names = {reference.name for reference in references}
    assert "upper-case" in reference_names


def test_parse_file_extracts_erlang_symbols(tmp_path: Path) -> None:
    """The parser extracts Erlang modules, imports, records, functions, and refs."""
    file_path = tmp_path / "sample.erl"
    file_path.write_text(
        "-module(demo).\n"
        "-import(lists, [map/2]).\n"
        "-record(person, {name}).\n"
        "helper(X) -> X.\n"
        "main() -> helper(1).\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "erlang", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("module", "demo")].name == "demo"
    assert by_kind_name[("import", "lists")].name == "lists"
    assert by_kind_name[("record", "person")].name == "person"
    assert by_kind_name[("field", "name")].name == "name"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert by_kind_name[("function", "main")].name == "main"

    references = extract_references(file_path, "erlang", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


def test_parse_file_extracts_sql_symbols(tmp_path: Path) -> None:
    """The parser extracts SQL tables, views, columns, and references."""
    file_path = tmp_path / "sample.sql"
    file_path.write_text(
        "CREATE TABLE users (id INT, name TEXT);\n"
        "CREATE VIEW active_users AS SELECT name FROM users;\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "sql", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    users = by_kind_name[("struct", "users")]
    name = by_kind_name[("field", "name")]
    assert by_kind_name[("field", "id")].name == "id"
    assert by_kind_name[("struct", "active_users")].name == "active_users"
    assert name.container_id == users.id

    references = extract_references(file_path, "sql", symbols)
    reference_names = {reference.name for reference in references}
    assert "users" in reference_names


def test_parse_file_extracts_objc_symbols(tmp_path: Path) -> None:
    """The parser extracts Objective-C imports, classes, properties, and refs."""
    file_path = tmp_path / "sample.mm"
    file_path.write_text(
        "#import <Foundation/Foundation.h>\n"
        "@interface Greeter : NSObject\n"
        "@property NSString *name;\n"
        "- (NSString *)greet;\n"
        "@end\n"
        "@implementation Greeter\n"
        "- (NSString *)greet { return helper(_name); }\n"
        "@end\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "objc", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("import", "<Foundation/Foundation.h>")].name == "<Foundation/Foundation.h>"
    assert by_kind_name[("class", "Greeter")].name == "Greeter"
    assert by_kind_name[("property", "name")].name == "name"
    assert by_kind_name[("method", "greet")].name == "greet"

    references = extract_references(file_path, "objc", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


def test_parse_file_extracts_perl_symbols(tmp_path: Path) -> None:
    """The parser extracts Perl packages, imports, constants, functions, and refs."""
    file_path = tmp_path / "sample.pl"
    file_path.write_text(
        "package Demo;\n"
        "use strict;\n"
        "our $VALUE = 1;\n"
        "sub helper { return $VALUE; }\n"
        "sub main { return helper(); }\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "perl", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("package", "Demo")].name == "Demo"
    assert by_kind_name[("import", "strict")].name == "strict"
    assert by_kind_name[("constant", "VALUE")].name == "VALUE"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert by_kind_name[("function", "main")].name == "main"

    references = extract_references(file_path, "perl", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


def test_parse_file_extracts_zig_symbols(tmp_path: Path) -> None:
    """The parser extracts Zig imports, structs, methods, functions, and refs."""
    file_path = tmp_path / "sample.zig"
    file_path.write_text(
        'const std = @import("std");\n'
        "const Person = struct {\n"
        "    name: []const u8,\n"
        "    pub fn greet(self: Person) []const u8 {\n"
        "        return helper(self.name);\n"
        "    }\n"
        "};\n\n"
        "fn helper(name: []const u8) []const u8 {\n"
        "    return name;\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "zig", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    person = by_kind_name[("struct", "Person")]
    assert by_kind_name[("import", "std")].name == "std"
    assert by_kind_name[("field", "name")].name == "name"
    assert by_kind_name[("method", "greet")].name == "greet"
    assert by_kind_name[("function", "helper")].name == "helper"
    assert by_kind_name[("method", "greet")].container_id == person.id

    references = extract_references(file_path, "zig", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


def test_parse_file_extracts_vue_symbols(tmp_path: Path) -> None:
    """The parser extracts conservative Vue module symbols and template refs."""
    file_path = tmp_path / "sample.vue"
    file_path.write_text(
        "<script>\n"
        "import { helper } from './mod'\n"
        "</script>\n"
        '<template><Widget :value="name" /></template>\n',
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "vue", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("module", "script")].name == "script"
    assert by_kind_name[("module", "template")].name == "template"

    references = extract_references(file_path, "vue", symbols)
    reference_names = {reference.name for reference in references}
    assert "Widget" in reference_names
    assert "name" in reference_names


def test_parse_file_extracts_svelte_symbols(tmp_path: Path) -> None:
    """The parser extracts conservative Svelte module symbols and refs."""
    file_path = tmp_path / "sample.svelte"
    file_path.write_text(
        "<script>\n"
        '  import Widget from "./Widget.svelte";\n'
        "</script>\n"
        "<button on:click={greet}>{name}</button>\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "svelte", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("module", "script")].name == "script"
    assert by_kind_name[("module", "button")].name == "button"

    references = extract_references(file_path, "svelte", symbols)
    reference_names = {reference.name for reference in references}
    assert "greet" in reference_names
    assert "name" in reference_names


def test_parse_file_extracts_angular_template_symbols(tmp_path: Path) -> None:
    """The parser extracts conservative Angular template symbols and refs."""
    file_path = tmp_path / "sample.html"
    file_path.write_text(
        '<app-root><button (click)="save(name)">{{title}}</button><div #panel></div></app-root>\n',
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "angular_template", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("module", "app-root")].name == "app-root"
    assert by_kind_name[("variable", "#panel")].name == "#panel"

    references = extract_references(file_path, "angular_template", symbols)
    reference_names = {reference.name for reference in references}
    assert "button" in reference_names
    assert "save(name)" in reference_names


def test_parse_file_extracts_groovy_symbols(tmp_path: Path) -> None:
    """The parser extracts Groovy packages, imports, types, members, and refs."""
    file_path = tmp_path / "sample.groovy"
    file_path.write_text(
        "package demo\n"
        "import helper.Util\n"
        "class Greeter {\n"
        "  String name\n"
        "  String greet() { helper(name) }\n"
        "}\n"
        "def helper(value) { value }\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "groovy", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    assert by_kind_name[("package", "demo")].name == "demo"
    assert by_kind_name[("import", "helper.Util")].name == "helper.Util"
    assert by_kind_name[("class", "Greeter")].name == "Greeter"
    assert by_kind_name[("field", "name")].name == "name"
    assert by_kind_name[("method", "greet")].name == "greet"
    assert by_kind_name[("function", "helper")].name == "helper"

    references = extract_references(file_path, "groovy", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


def test_parse_file_extracts_matlab_symbols(tmp_path: Path) -> None:
    """The parser extracts MATLAB functions, classes, properties, methods, and refs."""
    file_path = tmp_path / "sample.m"
    file_path.write_text(
        "function out = helper(x)\n"
        "out = x;\n"
        "end\n\n"
        "classdef Greeter\n"
        "  properties\n"
        "    Name\n"
        "  end\n"
        "  methods\n"
        "    function out = greet(obj)\n"
        "      out = helper(obj.Name);\n"
        "    end\n"
        "  end\n"
        "end\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "matlab", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    greeter = by_kind_name[("class", "Greeter")]
    assert by_kind_name[("function", "helper")].name == "helper"
    assert by_kind_name[("property", "Name")].name == "Name"
    assert by_kind_name[("method", "greet")].name == "greet"
    assert by_kind_name[("method", "greet")].container_id == greeter.id

    references = extract_references(file_path, "matlab", symbols)
    reference_names = {reference.name for reference in references}
    assert "helper" in reference_names


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


def test_parse_file_extracts_csharp_file_scoped_namespace_members(tmp_path: Path) -> None:
    """The parser extracts file-scoped namespaces, delegates, events, and enum members."""
    file_path = tmp_path / "modern.cs"
    file_path.write_text(
        "namespace Sample.Modern;\n\n"
        "public delegate void Notify(string message);\n\n"
        "public enum Status\n"
        "{\n"
        "    Active,\n"
        "    Retired,\n"
        "}\n\n"
        "public class Publisher\n"
        "{\n"
        "    public event Notify? Changed;\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    namespace = by_kind_name[("namespace", "Sample.Modern")]
    status = by_kind_name[("enum", "Status")]
    active = by_kind_name[("constant", "Active")]
    assert namespace.native_kind == "file_scoped_namespace_declaration"
    assert by_kind_name[("type", "Notify")].native_kind == "delegate_declaration"
    assert by_kind_name[("field", "Changed")].native_kind == "event_field_declaration"
    assert active.container_id == status.id
    assert by_kind_name[("constant", "Retired")].container_id == status.id
    # A file-scoped namespace declaration ends at its semicolon but scopes the rest of
    # the file, so its symbol range is widened and later declarations nest under it.
    publisher = by_kind_name[("class", "Publisher")]
    assert publisher.container_id == namespace.id
    assert publisher.qualified_name == "Sample.Modern.Publisher"
    assert by_kind_name[("type", "Notify")].qualified_name == "Sample.Modern.Notify"
    # The signature still describes the declaration itself, not the widened range.
    assert namespace.signature == "namespace Sample.Modern;"


def test_extract_references_covers_csharp_member_access_types_and_attributes(
    tmp_path: Path,
) -> None:
    """C# references include member accesses, type usages, and attributes, not only calls."""
    file_path = tmp_path / "service.cs"
    file_path.write_text(
        "namespace Sample.App;\n\n"
        "public class Service\n"
        "{\n"
        "    [Obsolete]\n"
        "    public int Count(Repo repo)\n"
        "    {\n"
        "        DbSet<Item> items = repo.Items;\n"
        "        Helper helper = new Helper();\n"
        "        return items.CountAsync();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols)
    reference_names = {reference.name for reference in references}

    assert "Items" in reference_names  # member access without invocation
    assert "DbSet" in reference_names  # generic type name
    assert "Item" in reference_names  # generic type argument
    assert "Repo" in reference_names  # parameter type
    assert "Helper" in reference_names  # local variable type / object creation
    assert "CountAsync" in reference_names  # invocation via member access
    assert "Obsolete" in reference_names  # attribute


def test_parse_file_extracts_csharp_top_level_local_functions(tmp_path: Path) -> None:
    """A top-level-statements Program.cs yields its local functions, not only imports."""
    file_path = tmp_path / "Program.cs"
    file_path.write_text(
        "using System;\n\n"
        "var greeting = Build();\n"
        "Console.WriteLine(greeting);\n\n"
        "static string Build()\n"
        "{\n"
        '    return "hello";\n'
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name): symbol for symbol in symbols}

    build = by_kind_name[("function", "Build")]
    assert build.native_kind == "local_function_statement"
    assert by_kind_name[("import", "System")].name == "System"


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
    assert greeter.qualified_name == "Sample::Greeter"
    assert greet.qualified_name == "Sample::Greeter::greet"


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
        '  val greeting = "hi"\n\n'
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
    """Reference queries find call sites and the resolver records resolution honestly."""
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
    # A unique global name match stays a heuristic, never high confidence.
    assert str(resolved[0].confidence) == "medium"
    assert resolved[0].resolution is ResolutionMethod.UNIQUE_NAME
    assert resolved[0].start_line == 5
    assert resolved[0].start_byte_col is not None
    unresolved = build_reference_relations(raw_refs, {})
    assert unresolved[0].to_symbol_id is None
    assert str(unresolved[0].confidence) == "low"
    assert unresolved[0].resolution is ResolutionMethod.UNRESOLVED
    ambiguous = build_reference_relations(raw_refs, {"target": ["one", "two"]})
    assert ambiguous[0].to_symbol_id is None
    assert str(ambiguous[0].confidence) == "low"
    assert ambiguous[0].resolution is ResolutionMethod.AMBIGUOUS


def test_uppercase_constant_heuristic_is_language_gated() -> None:
    """ALL-CAPS variables become constants only where the convention applies."""
    promoted = _capture_kind_to_symbol_kind(
        "definition.variable", "MAX_SIZE", uppercase_constants=True
    )
    kept = _capture_kind_to_symbol_kind(
        "definition.variable", "MAX_SIZE", uppercase_constants=False
    )
    assert promoted is SymbolKind.CONSTANT
    assert kept is SymbolKind.VARIABLE


def test_candidate_symbol_ids_match_language_separator() -> None:
    """Qualified-name suffix matching honors the language name separator."""
    name_index = {"Sample::Greeter::greet": ["cpp-id"], "Sample.Greeter.greet": ["dotted-id"]}
    assert _candidate_symbol_ids("greet", name_index, "::") == ["cpp-id"]
    assert _candidate_symbol_ids("greet", name_index, ".") == ["dotted-id"]


def test_fortran_uppercase_variables_stay_variables(tmp_path: Path) -> None:
    """Case-insensitive languages do not promote uppercase names to constants."""
    file_path = tmp_path / "sample.f90"
    file_path.write_text(
        "PROGRAM MAIN\n  INTEGER :: LIMIT\n  LIMIT = 3\nEND PROGRAM MAIN\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "fortran", workspace_root=tmp_path)

    assert all(symbol.kind is not SymbolKind.CONSTANT for symbol in symbols)


def test_extract_references_covers_csharp_type_positions(tmp_path: Path) -> None:
    """C# type and member usages across declaration positions are all extracted."""
    file_path = tmp_path / "coverage.cs"
    file_path.write_text(
        "namespace Sample;\n"
        "public class Store\n"
        "{\n"
        "    public DbSet<Server> Entries { get; set; }\n"
        "    private App.Widget widget;\n"
        "    public Item[] Items;\n"
        "    public Server? Maybe;\n"
        "    public Wid Build(Repo repo)\n"
        "    {\n"
        "        var count = dbContext.Servers;\n"
        "        var made = new Helper();\n"
        "        var kind = typeof(Server);\n"
        "        var cast = (Server)made;\n"
        "        var safe = made as Server;\n"
        "        if (made is Server match) { }\n"
        "        try { } catch (BadError error) { }\n"
        "        return items.CountAsync();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols)
    names = {reference.name for reference in references}

    # Non-invoked member access, independent of the Server type references.
    assert "Servers" in names
    # DbSet<Server> yields the generic type and its argument as separate sites.
    assert "DbSet" in names
    server_sites = {r.start_byte for r in references if r.name == "Server"}
    servers_sites = {r.start_byte for r in references if r.name == "Servers"}
    assert server_sites and servers_sites
    assert server_sites.isdisjoint(servers_sites)
    # Qualified field type, array field type, nullable property type, return type,
    # parameter type, object creation, typeof, cast, as, pattern, catch, invocation.
    assert {
        "Widget",
        "Item",
        "Repo",
        "Wid",
        "Helper",
        "CountAsync",
        "BadError",
    } <= names


def test_csharp_declarations_are_not_captured_as_references(tmp_path: Path) -> None:
    """Declaration identifiers and using directives never surface as usages."""
    file_path = tmp_path / "decls.cs"
    file_path.write_text(
        "using System.Text;\n"
        "namespace Sample.App;\n"
        "public class Catalog\n"
        "{\n"
        "    public int Total { get; set; }\n"
        "    public void Refresh() { }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols)
    names = {reference.name for reference in references}

    assert names.isdisjoint({"System", "Text", "Sample", "App", "Catalog", "Total", "Refresh"})


def test_csharp_reference_sites_are_captured_once(tmp_path: Path) -> None:
    """Overlapping query patterns must not duplicate one syntactic site."""
    file_path = tmp_path / "bases.cs"
    file_path.write_text(
        "namespace Sample;\npublic class Foo : BaseFoo, IHandler<Bar>\n{\n}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols)

    sites = [reference.start_byte for reference in references]
    assert len(sites) == len(set(sites))
    assert {reference.name for reference in references} == {"BaseFoo", "IHandler", "Bar"}


def test_two_usages_of_same_method_have_distinct_locations(tmp_path: Path) -> None:
    """Repeated usages inside one method keep distinct lines/columns and ids."""
    file_path = tmp_path / "twice.cs"
    file_path.write_text(
        "namespace Sample;\n"
        "public class Runner\n"
        "{\n"
        "    public void Go(Repo repo)\n"
        "    {\n"
        "        repo.Save(); repo.Save();\n"
        "        repo.Save();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols)
    saves = [reference for reference in references if reference.name == "Save"]

    assert len(saves) == 3
    locations = {(reference.start_line, reference.start_byte_col) for reference in saves}
    assert len(locations) == 3

    relations = build_reference_relations(saves, {})
    assert len({relation.id for relation in relations}) == 3
    assert {(relation.start_line, relation.start_byte_col) for relation in relations} == locations


def _csharp_reference_names(tmp_path: Path, file_name: str, source: str) -> set[str]:
    """Write one C# source file and return the names its reference query captures."""
    file_path = tmp_path / file_name
    file_path.write_text(source, encoding="utf-8")
    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols, workspace_root=tmp_path)
    return {reference.name for reference in references}


def test_csharp_nameof_captures_the_named_declaration_not_the_operator(
    tmp_path: Path,
) -> None:
    """`nameof(X)` references X; the `nameof` operator itself is never a usage."""
    names = _csharp_reference_names(
        tmp_path,
        "nameof.cs",
        "namespace Sample;\n"
        "public class C\n"
        "{\n"
        "    public string M(Repo repo)\n"
        "    {\n"
        "        var a = nameof(Server);\n"
        "        var b = nameof(repo.Items);\n"
        "        return a + b;\n"
        "    }\n"
        "}\n",
    )

    assert "Server" in names  # nameof argument
    assert "Items" in names  # nameof of a member access
    assert "nameof" not in names  # the operator is not a callable symbol


def test_csharp_nested_generic_nullable_and_array_wrappers_are_captured(
    tmp_path: Path,
) -> None:
    """Type arguments stay visible through generic, nullable, and array nesting."""
    names = _csharp_reference_names(
        tmp_path,
        "wrappers.cs",
        "namespace Sample;\n"
        "public class C\n"
        "{\n"
        "    public Task<List<Server?>> Fetch() { return null; }\n"
        "    public Location?[] Slots { get; set; }\n"
        "    public List<Region?>[] Grid { get; set; }\n"
        "}\n",
    )

    assert {"Task", "List", "Server"} <= names  # Task<List<Server?>>
    assert "Location" in names  # nullable element of an array type
    assert "Region" in names  # nullable type argument inside an array of generics


def test_csharp_lambda_and_tuple_parameter_types_are_captured(tmp_path: Path) -> None:
    """Explicitly typed lambda parameters and tuple elements are real usages."""
    names = _csharp_reference_names(
        tmp_path,
        "lambdas.cs",
        "namespace Sample;\n"
        "public class C\n"
        "{\n"
        "    public (Server first, Location second) Pair() { return default; }\n"
        "    public void Take((Region a, Zone b) pair) { }\n"
        "    public void M()\n"
        "    {\n"
        "        var f = (Widget w) => w.Name;\n"
        "    }\n"
        "}\n",
    )

    assert {"Server", "Location"} <= names  # tuple return elements
    assert {"Region", "Zone"} <= names  # tuple parameter elements
    assert "Widget" in names  # explicitly typed lambda parameter


def test_csharp_foreach_and_default_type_positions_are_captured(tmp_path: Path) -> None:
    """`foreach` element types and `default(T)` name real declarations."""
    names = _csharp_reference_names(
        tmp_path,
        "positions.cs",
        "namespace Sample;\n"
        "public class C\n"
        "{\n"
        "    public void M(object items)\n"
        "    {\n"
        "        foreach (Server s in items) { }\n"
        "        var d = default(Location);\n"
        "    }\n"
        "}\n",
    )

    assert "Server" in names  # foreach element type
    assert "Location" in names  # default(T)


def test_csharp_qualified_and_alias_qualified_names_are_captured(tmp_path: Path) -> None:
    """Dotted and `global::` qualified type names resolve to their trailing segment."""
    names = _csharp_reference_names(
        tmp_path,
        "qualified.cs",
        "namespace Sample;\n"
        "public class C\n"
        "{\n"
        "    public global::System.String Text;\n"
        "    public Overlock.Api.Servers.Server Item;\n"
        "}\n",
    )

    assert "String" in names  # alias-qualified global::System.String
    assert "Server" in names  # dotted qualified name
    # The qualifier segments are context, not usages of their own.
    assert names.isdisjoint({"Overlock", "Api", "System", "global"})


def test_csharp_top_level_statements_produce_file_anchored_references(
    tmp_path: Path,
) -> None:
    """Usages outside any declaration are kept, anchored to the file."""
    file_path = tmp_path / "Program.cs"
    file_path.write_text(
        "var builder = WebApplication.CreateBuilder(args);\n"
        "Server thing = new Server();\n"
        "builder.Run();\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols, workspace_root=tmp_path)
    names = {reference.name for reference in references}

    assert {"CreateBuilder", "Server", "Run"} <= names
    # The receiver of a member access is not captured (the `static-receiver-types`
    # limitation), so the static type name itself is absent by design.
    assert "WebApplication" not in names
    # No enclosing declaration exists, so these anchor to the file instead of being dropped.
    unanchored = [reference for reference in references if reference.from_symbol is None]
    assert unanchored
    assert {reference.file_path for reference in unanchored} == {"Program.cs"}

    relations = build_reference_relations(references, {})
    assert all(relation.from_file_path == "Program.cs" for relation in relations)
    assert any(relation.from_symbol_id is None for relation in relations)
    assert len({relation.id for relation in relations}) == len(relations)


def test_csharp_references_carry_usage_kinds_and_qualified_text(tmp_path: Path) -> None:
    """Every captured usage reports the syntactic position it was found in."""
    file_path = tmp_path / "kinds.cs"
    file_path.write_text(
        "namespace Sample;\n"
        "public class C : BaseC\n"
        "{\n"
        "    public void M(Repo repo)\n"
        "    {\n"
        "        var item = new Widget();\n"
        "        repo.Save();\n"
        "        var total = repo.Total;\n"
        "        var t = typeof(Marker);\n"
        "        var q = Overlock.Api.Servers.Server.Create();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols, workspace_root=tmp_path)
    by_name = {reference.name: reference for reference in references}

    assert by_name["BaseC"].usage_kind == "base-type"
    assert by_name["Repo"].usage_kind == "declared-type"
    assert by_name["Widget"].usage_kind == "object-creation"
    # A call through a receiver is an invocation; only a genuine member read stays
    # a member access. Both match the member-access pattern, so priority decides.
    assert by_name["Save"].usage_kind == "invocation"
    assert by_name["Total"].usage_kind == "member-access"
    assert by_name["Marker"].usage_kind == "type-literal"
    assert by_name["Save"].receiver_text == "repo"
    # The full dotted path survives extraction so the resolver can match it.
    assert by_name["Server"].qualified_text == "Overlock.Api.Servers.Server"


def test_parse_file_indexes_decorated_python_definitions(tmp_path: Path) -> None:
    """Decorated functions, methods, and async defs are indexed as real declarations."""
    file_path = tmp_path / "decorated.py"
    file_path.write_text(
        "import functools\n\n"
        "@functools.cache\n"
        "def decorated_function(x):\n"
        "    return helper(x)\n\n"
        "class Holder:\n"
        "    @property\n"
        "    def decorated_method(self):\n"
        "        return other_call()\n\n"
        "@functools.cache\n"
        "async def decorated_async():\n"
        "    return thing()\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)
    by_name = {symbol.name: symbol for symbol in symbols}

    assert by_name["decorated_function"].kind == "function"
    assert by_name["decorated_function"].start_line == 4
    assert by_name["decorated_function"].signature == "def decorated_function(x):"
    assert by_name["decorated_method"].kind == "method"
    assert by_name["decorated_method"].container_id == by_name["Holder"].id
    assert by_name["decorated_method"].qualified_name == "Holder.decorated_method"
    assert by_name["decorated_async"].kind == "function"
    assert by_name["decorated_async"].start_line == 13


def test_decorated_function_bodies_anchor_references_to_the_function(tmp_path: Path) -> None:
    """Body references of decorated defs anchor to the def, not None or the class."""
    file_path = tmp_path / "decorated_refs.py"
    file_path.write_text(
        "@wrapper\n"
        "def outer():\n"
        "    return helper()\n\n"
        "class Holder:\n"
        "    @wrapper\n"
        "    def inner(self):\n"
        "        return other_call()\n",
        encoding="utf-8",
    )

    symbols = parse_file(file_path, "python", workspace_root=tmp_path)
    references = extract_references(file_path, "python", symbols, workspace_root=tmp_path)
    anchors = {
        reference.name: reference.from_symbol.name if reference.from_symbol else None
        for reference in references
    }

    assert anchors["helper"] == "outer"
    assert anchors["other_call"] == "inner"
