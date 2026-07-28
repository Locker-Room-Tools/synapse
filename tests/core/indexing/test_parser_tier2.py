"""Parser coverage for Tier 2 language support."""

from pathlib import Path

import pytest

from synapse.core.indexing.parser import extract_references, parse_file

TIER2_SAMPLES = (
    (
        "ada",
        "sample.adb",
        "with Ada.Text_IO; use Ada.Text_IO;\n"
        "procedure Main is\n"
        "   X : Integer := 1;\n"
        "begin\n"
        '   Put_Line("hi");\n'
        "end Main;\n",
        ("function", "Main"),
        "Put_Line",
    ),
    (
        "assembly",
        "sample.s",
        "_main:\n    call _helper\n_helper:\n    ret\n",
        ("function", "_main"),
        "_helper",
    ),
    (
        "cobol",
        "sample.cbl",
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. HELLO.\n"
        "       DATA DIVISION.\n"
        "       WORKING-STORAGE SECTION.\n"
        "       01 CUSTOMER-NAME PIC X(10).\n"
        "       PROCEDURE DIVISION.\n"
        "       MAIN-PARA.\n"
        "           PERFORM HELPER-PARA.\n"
        "       HELPER-PARA.\n"
        "           STOP RUN.\n",
        ("module", "HELLO"),
        "HELPER-PARA",
    ),
    (
        "common_lisp",
        "sample.lisp",
        "(defpackage :demo)\n"
        "(defun helper (x) x)\n"
        "(defun main () (helper 1))\n"
        "(defclass person () ((name)))\n"
        "(defvar *value* 1)\n",
        ("function", "main"),
        "helper",
    ),
    (
        "crystal",
        "sample.cr",
        'require "json"\n'
        "class Greeter\n"
        "  VALUE = 1\n"
        "  @name : String\n"
        "  def greet\n"
        "    helper\n"
        "  end\n"
        "  def helper\n"
        "  end\n"
        "end\n",
        ("class", "Greeter"),
        "helper",
    ),
    (
        "cuda",
        "sample.cu",
        "#include <cuda_runtime.h>\n"
        "__global__ void kernel(int *data) { data[threadIdx.x] = 1; }\n"
        "struct Item { int value; };\n"
        "void launch() { kernel<<<1,1>>>(nullptr); }\n",
        ("function", "kernel"),
        "kernel",
    ),
    (
        "d",
        "sample.d",
        "module demo;\n"
        "import std.stdio;\n"
        "class Greeter { int value; void greet() { writeln(value); } }\n"
        "void main() { auto g = new Greeter(); g.greet(); }\n",
        ("class", "Greeter"),
        "Greeter",
    ),
    (
        "fortran",
        "sample.f90",
        "program main\n"
        "contains\n"
        "  subroutine helper()\n"
        "  end subroutine helper\n"
        "  subroutine run()\n"
        "    call helper()\n"
        "  end subroutine run\n"
        "end program main\n",
        ("function", "run"),
        "helper",
    ),
    (
        "glsl",
        "sample.frag",
        "#version 450\n"
        "uniform mat4 model;\n"
        "struct Light { vec3 position; };\n"
        "const float PI = 3.14;\n"
        "void helper() {}\n"
        "void main() { helper(); }\n",
        ("struct", "Light"),
        "helper",
    ),
    (
        "hlsl",
        "sample.hlsl",
        "struct VSOut { float4 pos : SV_POSITION; };\n"
        "float4 helper() : SV_Target { return color; }\n"
        "float4 main() : SV_Target { return helper(); }\n",
        ("function", "main"),
        "helper",
    ),
    (
        "nim",
        "sample.nim",
        "import strutils\n"
        "const Value = 1\n"
        "type Person = object\n"
        "  name: string\n"
        "proc helper(x: int): int =\n"
        "  result = x\n"
        "proc main() =\n"
        "  discard helper(Value)\n",
        ("function", "main"),
        "helper",
    ),
    (
        "pascal",
        "sample.pas",
        "program Demo;\n"
        "uses SysUtils;\n"
        "type TPerson = record Name: string; end;\n"
        "var Value: Integer;\n"
        "procedure Helper;\n"
        "begin\n"
        "end;\n"
        "begin\n"
        "  Helper;\n"
        "end.\n",
        ("function", "Helper"),
        "Helper",
    ),
    (
        "smalltalk",
        "sample.st",
        "greet\n    ^self helper\n",
        ("method", "greet"),
        "helper",
    ),
    (
        "verilog",
        "sample.v",
        "module demo(input clk);\n"
        "  parameter WIDTH = 8;\n"
        "  reg value;\n"
        "  function integer helper; input integer x; begin helper = x; end endfunction\n"
        "  initial begin value = helper(WIDTH); end\n"
        "endmodule\n",
        ("module", "demo"),
        "helper",
    ),
    (
        "vhdl",
        "sample.vhd",
        "library ieee;\n"
        "use ieee.std_logic_1164.all;\n"
        "entity demo is end demo;\n"
        "architecture rtl of demo is\n"
        "  signal value : std_logic;\n"
        "begin\n"
        "  process begin null; end process;\n"
        "end rtl;\n",
        ("module", "rtl"),
        "demo",
    ),
    (
        "wgsl",
        "sample.wgsl",
        "struct Light { position: vec3f, }\n"
        "let VALUE: i32 = 1;\n"
        "var<private> state: i32;\n"
        "fn helper(x: i32) -> i32 { return x; }\n"
        "fn main() { let y = helper(VALUE); }\n",
        ("function", "main"),
        "helper",
    ),
)


@pytest.mark.parametrize(
    ("language", "file_name", "source", "expected_symbol", "expected_reference"),
    TIER2_SAMPLES,
)
def test_parse_file_extracts_tier2_symbols_and_references(
    tmp_path: Path,
    language: str,
    file_name: str,
    source: str,
    expected_symbol: tuple[str, str],
    expected_reference: str,
) -> None:
    """Tier 2 query files extract representative symbols and in-scope references."""
    file_path = tmp_path / file_name
    file_path.write_text(source, encoding="utf-8")

    symbols = parse_file(file_path, language, workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name) for symbol in symbols}

    assert expected_symbol in by_kind_name

    references = extract_references(file_path, language, symbols)
    reference_names = {reference.name for reference in references}
    assert expected_reference in reference_names
