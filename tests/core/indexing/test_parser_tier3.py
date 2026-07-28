"""Parser coverage for Tier 3 language support."""

from pathlib import Path

import pytest

from synapse.core.indexing.parser import extract_references, parse_file

TIER3_SAMPLES = (
    (
        "astro",
        "sample.astro",
        "---\n"
        'import Header from "./Header.astro";\n'
        'const title = "Hi";\n'
        "function greet(name) { return title; }\n"
        "---\n"
        "<Header title={title} />\n"
        '<h1>{greet("Ada")}</h1>\n',
        ("module", "Header"),
        "title",
    ),
    (
        "elm",
        "sample.elm",
        "module Main exposing (main)\n\n"
        "import Html exposing (text)\n\n"
        "type alias Model = { name : String }\n\n"
        "type Msg = NoOp\n\n"
        "helper name =\n"
        "    text name\n\n"
        "main =\n"
        '    helper "Ada"\n',
        ("function", "main"),
        "helper",
    ),
    (
        "purescript",
        "sample.purs",
        "module Main where\n\n"
        "import Prelude\n\n"
        "data Msg = NoOp\n"
        "newtype User = User String\n"
        "type Model = { name :: String }\n\n"
        "helper :: String -> String\n"
        "helper name = name\n\n"
        'main = helper "Ada"\n',
        ("function", "main"),
        "helper",
    ),
    (
        "gleam",
        "sample.gleam",
        "import gleam/io\n\n"
        "pub type Msg {\n"
        "  NoOp\n"
        "}\n\n"
        'const title = "Hi"\n\n'
        "fn helper(name: String) -> String {\n"
        "  name\n"
        "}\n\n"
        "pub fn main() {\n"
        "  io.println(helper(title))\n"
        "}\n",
        ("function", "main"),
        "helper",
    ),
    (
        "rescript",
        "sample.res",
        "module Greeter = {\n"
        "  type user = {name: string}\n"
        "  let helper = name => name\n"
        "}\n\n"
        "type msg = NoOp\n"
        "let helper = value => value\n"
        'let main = helper("Ada")\n',
        ("function", "main"),
        "helper",
    ),
    (
        "scss",
        "sample.scss",
        '@use "sass:color";\n'
        "$primary: #0366d6;\n"
        "@mixin button($color) { color: $color; }\n"
        "@function double($value) { @return $value * 2; }\n"
        ".button { @include button($primary); width: double(2px); }\n",
        ("function", "double"),
        "double",
    ),
    (
        "less",
        "sample.less",
        '@import "theme.less";\n'
        "@primary: #0366d6;\n"
        ".button-mixin(@color) { color: @color; }\n"
        ".card { .button-mixin(@primary); }\n",
        ("function", "button-mixin"),
        "button-mixin",
    ),
)


@pytest.mark.parametrize(
    ("language", "file_name", "source", "expected_symbol", "expected_reference"),
    TIER3_SAMPLES,
)
def test_parse_file_extracts_tier3_symbols_and_references(
    tmp_path: Path,
    language: str,
    file_name: str,
    source: str,
    expected_symbol: tuple[str, str],
    expected_reference: str,
) -> None:
    """Tier 3 query files extract representative symbols and references."""
    file_path = tmp_path / file_name
    file_path.write_text(source, encoding="utf-8")

    symbols = parse_file(file_path, language, workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name) for symbol in symbols}

    assert expected_symbol in by_kind_name

    references = extract_references(file_path, language, symbols)
    reference_names = {reference.name for reference in references}
    assert expected_reference in reference_names
