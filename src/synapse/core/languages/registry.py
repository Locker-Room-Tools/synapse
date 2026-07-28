"""Supported language registry and tree-sitter name helpers."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    """Static metadata for one supported language."""

    id: str
    tree_sitter_name: str
    extensions: tuple[str, ...]
    query_dir: str
    # Separator used when joining nested symbol names into qualified names.
    name_separator: str = "."
    # Whether ALL-CAPS fields/variables idiomatically denote constants.
    uppercase_constants: bool = True


LANGUAGES: dict[str, LanguageSpec] = {
    "ada": LanguageSpec(
        id="ada",
        tree_sitter_name="ada",
        extensions=(".adb", ".ads", ".ada"),
        query_dir="ada",
        uppercase_constants=False,
    ),
    "angular_template": LanguageSpec(
        id="angular_template",
        tree_sitter_name="html",
        extensions=(),
        query_dir="angular_template",
    ),
    "assembly": LanguageSpec(
        id="assembly",
        tree_sitter_name="asm",
        extensions=(".asm", ".s"),
        query_dir="assembly",
        uppercase_constants=False,
    ),
    "astro": LanguageSpec(
        id="astro",
        tree_sitter_name="astro",
        extensions=(".astro",),
        query_dir="astro",
    ),
    "awk": LanguageSpec(
        id="awk",
        tree_sitter_name="awk",
        extensions=(".awk",),
        query_dir="awk",
    ),
    "bash": LanguageSpec(
        id="bash",
        tree_sitter_name="bash",
        extensions=(".sh", ".bash", ".bats"),
        query_dir="bash",
    ),
    "c": LanguageSpec(
        id="c",
        tree_sitter_name="c",
        extensions=(".c", ".h"),
        query_dir="c",
    ),
    "clojure": LanguageSpec(
        id="clojure",
        tree_sitter_name="clojure",
        extensions=(".clj", ".cljs", ".cljc", ".bb"),
        query_dir="clojure",
    ),
    "cobol": LanguageSpec(
        id="cobol",
        tree_sitter_name="cobol",
        extensions=(".cob", ".cbl", ".cobol", ".cpy"),
        query_dir="cobol",
        uppercase_constants=False,
    ),
    "common_lisp": LanguageSpec(
        id="common_lisp",
        tree_sitter_name="commonlisp",
        extensions=(".lisp", ".lsp", ".cl", ".asd"),
        query_dir="common_lisp",
    ),
    "cpp": LanguageSpec(
        id="cpp",
        tree_sitter_name="cpp",
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
        query_dir="cpp",
        name_separator="::",
    ),
    "crystal": LanguageSpec(
        id="crystal",
        tree_sitter_name="crystal",
        extensions=(".cr",),
        query_dir="crystal",
        name_separator="::",
    ),
    "csharp": LanguageSpec(
        id="csharp",
        tree_sitter_name="csharp",
        extensions=(".cs",),
        query_dir="c_sharp",
    ),
    "cuda": LanguageSpec(
        id="cuda",
        tree_sitter_name="cuda",
        extensions=(".cu", ".cuh", ".cuda"),
        query_dir="cuda",
        name_separator="::",
    ),
    "d": LanguageSpec(
        id="d",
        tree_sitter_name="d",
        extensions=(".d", ".di"),
        query_dir="d",
    ),
    "dart": LanguageSpec(
        id="dart",
        tree_sitter_name="dart",
        extensions=(".dart",),
        query_dir="dart",
    ),
    "elixir": LanguageSpec(
        id="elixir",
        tree_sitter_name="elixir",
        extensions=(".ex", ".exs"),
        query_dir="elixir",
    ),
    "elm": LanguageSpec(
        id="elm",
        tree_sitter_name="elm",
        extensions=(".elm",),
        query_dir="elm",
    ),
    "emacs_lisp": LanguageSpec(
        id="emacs_lisp",
        tree_sitter_name="elisp",
        extensions=(".el",),
        query_dir="emacs_lisp",
    ),
    "erlang": LanguageSpec(
        id="erlang",
        tree_sitter_name="erlang",
        extensions=(".erl", ".hrl"),
        query_dir="erlang",
        uppercase_constants=False,
    ),
    "fish": LanguageSpec(
        id="fish",
        tree_sitter_name="fish",
        extensions=(".fish",),
        query_dir="fish",
    ),
    "fsharp": LanguageSpec(
        id="fsharp",
        tree_sitter_name="fsharp",
        extensions=(".fs", ".fsi", ".fsx"),
        query_dir="fsharp",
    ),
    "gdscript": LanguageSpec(
        id="gdscript",
        tree_sitter_name="gdscript",
        extensions=(".gd",),
        query_dir="gdscript",
    ),
    "fortran": LanguageSpec(
        id="fortran",
        tree_sitter_name="fortran",
        extensions=(".f", ".for", ".f90", ".f95", ".f03", ".f08"),
        query_dir="fortran",
        uppercase_constants=False,
    ),
    "glsl": LanguageSpec(
        id="glsl",
        tree_sitter_name="glsl",
        extensions=(".glsl", ".vert", ".frag", ".geom", ".tesc", ".tese", ".comp"),
        query_dir="glsl",
    ),
    "gleam": LanguageSpec(
        id="gleam",
        tree_sitter_name="gleam",
        extensions=(".gleam",),
        query_dir="gleam",
    ),
    "go": LanguageSpec(
        id="go",
        tree_sitter_name="go",
        extensions=(".go",),
        query_dir="go",
    ),
    "groovy": LanguageSpec(
        id="groovy",
        tree_sitter_name="groovy",
        extensions=(".groovy", ".gradle", ".gvy", ".gy"),
        query_dir="groovy",
    ),
    "haxe": LanguageSpec(
        id="haxe",
        tree_sitter_name="haxe",
        extensions=(".hx",),
        query_dir="haxe",
    ),
    "haskell": LanguageSpec(
        id="haskell",
        tree_sitter_name="haskell",
        extensions=(".hs", ".lhs"),
        query_dir="haskell",
    ),
    "hlsl": LanguageSpec(
        id="hlsl",
        tree_sitter_name="hlsl",
        extensions=(".hlsl", ".fx", ".fxh"),
        query_dir="hlsl",
    ),
    "java": LanguageSpec(
        id="java",
        tree_sitter_name="java",
        extensions=(".java",),
        query_dir="java",
    ),
    "javascript": LanguageSpec(
        id="javascript",
        tree_sitter_name="javascript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        query_dir="javascript",
    ),
    "julia": LanguageSpec(
        id="julia",
        tree_sitter_name="julia",
        extensions=(".jl",),
        query_dir="julia",
    ),
    "kotlin": LanguageSpec(
        id="kotlin",
        tree_sitter_name="kotlin",
        extensions=(".kt", ".kts"),
        query_dir="kotlin",
    ),
    "less": LanguageSpec(
        id="less",
        tree_sitter_name="less",
        extensions=(".less",),
        query_dir="less",
    ),
    "lua": LanguageSpec(
        id="lua",
        tree_sitter_name="lua",
        extensions=(".lua",),
        query_dir="lua",
    ),
    "luau": LanguageSpec(
        id="luau",
        tree_sitter_name="luau",
        extensions=(".luau",),
        query_dir="luau",
    ),
    "matlab": LanguageSpec(
        id="matlab",
        tree_sitter_name="matlab",
        extensions=(".m",),
        query_dir="matlab",
    ),
    "nim": LanguageSpec(
        id="nim",
        tree_sitter_name="nim",
        extensions=(".nim", ".nims"),
        query_dir="nim",
    ),
    "nushell": LanguageSpec(
        id="nushell",
        tree_sitter_name="nushell",
        extensions=(".nu",),
        query_dir="nushell",
    ),
    "objc": LanguageSpec(
        id="objc",
        tree_sitter_name="objc",
        extensions=(".mm",),
        query_dir="objc",
    ),
    "ocaml": LanguageSpec(
        id="ocaml",
        tree_sitter_name="ocaml",
        extensions=(".ml", ".mli"),
        query_dir="ocaml",
    ),
    "perl": LanguageSpec(
        id="perl",
        tree_sitter_name="perl",
        extensions=(".pl", ".pm", ".t"),
        query_dir="perl",
        name_separator="::",
    ),
    "pascal": LanguageSpec(
        id="pascal",
        tree_sitter_name="pascal",
        extensions=(".pas", ".pp"),
        query_dir="pascal",
        uppercase_constants=False,
    ),
    "php": LanguageSpec(
        id="php",
        tree_sitter_name="php",
        extensions=(".php",),
        query_dir="php",
        name_separator="\\",
    ),
    "powershell": LanguageSpec(
        id="powershell",
        tree_sitter_name="powershell",
        extensions=(".ps1", ".psm1", ".psd1"),
        query_dir="powershell",
    ),
    "purescript": LanguageSpec(
        id="purescript",
        tree_sitter_name="purescript",
        extensions=(".purs",),
        query_dir="purescript",
    ),
    "python": LanguageSpec(
        id="python",
        tree_sitter_name="python",
        extensions=(".py",),
        query_dir="python",
    ),
    "r": LanguageSpec(
        id="r",
        tree_sitter_name="r",
        extensions=(".r",),
        query_dir="r",
    ),
    "rescript": LanguageSpec(
        id="rescript",
        tree_sitter_name="rescript",
        extensions=(".res", ".resi"),
        query_dir="rescript",
    ),
    "ruby": LanguageSpec(
        id="ruby",
        tree_sitter_name="ruby",
        extensions=(".rb",),
        query_dir="ruby",
        name_separator="::",
    ),
    "rust": LanguageSpec(
        id="rust",
        tree_sitter_name="rust",
        extensions=(".rs",),
        query_dir="rust",
        name_separator="::",
    ),
    "scala": LanguageSpec(
        id="scala",
        tree_sitter_name="scala",
        extensions=(".scala", ".sc"),
        query_dir="scala",
    ),
    "scss": LanguageSpec(
        id="scss",
        tree_sitter_name="scss",
        extensions=(".scss",),
        query_dir="scss",
    ),
    "smalltalk": LanguageSpec(
        id="smalltalk",
        tree_sitter_name="smalltalk",
        extensions=(".st",),
        query_dir="smalltalk",
    ),
    "sql": LanguageSpec(
        id="sql",
        tree_sitter_name="sql",
        extensions=(".sql",),
        query_dir="sql",
        uppercase_constants=False,
    ),
    "svelte": LanguageSpec(
        id="svelte",
        tree_sitter_name="svelte",
        extensions=(".svelte",),
        query_dir="svelte",
    ),
    "swift": LanguageSpec(
        id="swift",
        tree_sitter_name="swift",
        extensions=(".swift",),
        query_dir="swift",
    ),
    "typescript": LanguageSpec(
        id="typescript",
        tree_sitter_name="typescript",
        extensions=(".ts", ".mts", ".cts"),
        query_dir="typescript",
    ),
    "verilog": LanguageSpec(
        id="verilog",
        tree_sitter_name="verilog",
        extensions=(".v", ".vh", ".verilog"),
        query_dir="verilog",
        uppercase_constants=False,
    ),
    "vhdl": LanguageSpec(
        id="vhdl",
        tree_sitter_name="vhdl",
        extensions=(".vhd", ".vhdl"),
        query_dir="vhdl",
        uppercase_constants=False,
    ),
    "vimscript": LanguageSpec(
        id="vimscript",
        tree_sitter_name="vim",
        extensions=(".vim",),
        query_dir="vimscript",
    ),
    "tsx": LanguageSpec(
        id="tsx",
        tree_sitter_name="tsx",
        extensions=(".tsx",),
        query_dir="typescript",
    ),
    "vue": LanguageSpec(
        id="vue",
        tree_sitter_name="vue",
        extensions=(".vue",),
        query_dir="vue",
    ),
    "wgsl": LanguageSpec(
        id="wgsl",
        tree_sitter_name="wgsl",
        extensions=(".wgsl",),
        query_dir="wgsl",
    ),
    "zig": LanguageSpec(
        id="zig",
        tree_sitter_name="zig",
        extensions=(".zig",),
        query_dir="zig",
    ),
    "zsh": LanguageSpec(
        id="zsh",
        tree_sitter_name="zsh",
        extensions=(".zsh",),
        query_dir="zsh",
    ),
}


# Contested extensions are assigned to exactly one language until content-aware
# detection exists: .m -> matlab (objc claims only .mm), .h -> c (not cpp),
# .v -> verilog (not coq). Losing languages must not list the extension.
def _build_extension_map(languages: Iterable[LanguageSpec]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for language in languages:
        for extension in language.extensions:
            claimed_by = mapping.get(extension)
            if claimed_by is not None:
                msg = f"Extension {extension!r} claimed by both {claimed_by!r} and {language.id!r}"
                raise ValueError(msg)
            mapping[extension] = language.id
    return mapping


_EXTENSION_TO_LANGUAGE = _build_extension_map(LANGUAGES.values())

# Compound-suffix conventions checked before plain extension lookup.
_FILENAME_SUFFIX_TO_LANGUAGE = {
    ".component.html": "angular_template",
}


def detect_language(path: Path) -> str | None:
    """Return the normalized language id for a file path when supported."""
    name = path.name.lower()
    for suffix, language_id in _FILENAME_SUFFIX_TO_LANGUAGE.items():
        if name.endswith(suffix) and len(name) > len(suffix):
            return language_id
    return _EXTENSION_TO_LANGUAGE.get(path.suffix.lower())


def to_treesitter_name(language: str) -> str:
    """Return the tree-sitter language name for a normalized language id."""
    try:
        return LANGUAGES[language].tree_sitter_name
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc


def tree_sitter_language_names() -> tuple[str, ...]:
    """Return the deduplicated parser names required by the language registry."""
    return tuple(sorted({spec.tree_sitter_name for spec in LANGUAGES.values()}))


def name_separator(language: str) -> str:
    """Return the qualified-name separator for a normalized language id."""
    try:
        return LANGUAGES[language].name_separator
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc


def uses_uppercase_constants(language: str) -> bool:
    """Return whether ALL-CAPS variables denote constants in this language."""
    try:
        return LANGUAGES[language].uppercase_constants
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc


def query_dir(language: str) -> str:
    """Return the query directory name for a normalized language id."""
    try:
        return LANGUAGES[language].query_dir
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc
