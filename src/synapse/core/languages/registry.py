"""Supported language registry and tree-sitter name helpers."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ReferenceExtraction(StrEnum):
    """How completely a language's reference query covers real usage syntax."""

    NONE = "none"
    PARTIAL = "partial"
    # Reserved: requires demonstrated near-complete usage coverage.
    BROAD = "broad"


@dataclass(frozen=True, slots=True)
class ReferenceSyntax:
    """Grammar node and field names a structural reference resolver needs.

    The resolver itself is language-agnostic; every syntax-specific fact it relies on
    is named here. A language without this block resolves by unique name only.
    """

    # Dotted names: `(qualified_name qualifier: ... name: ...)`.
    qualified_types: tuple[str, ...] = ()
    # Alias-qualified names such as C# `global::System.String`.
    alias_qualified_types: tuple[str, ...] = ()
    qualifier_field: str = "qualifier"
    name_field: str = "name"
    # Member access: `(member_access_expression expression: <receiver> name: <member>)`.
    member_access_types: tuple[str, ...] = ()
    receiver_field: str = "expression"
    # Member-name field on member-access nodes when it differs from name_field
    # (Python's `attribute` node uses `attribute`, not `name`).
    member_name_field: str = ""
    # Declarations that establish the enclosing namespace for a byte range.
    namespace_types: tuple[str, ...] = ()
    # Import/using directives contributing in-scope namespaces and aliases.
    import_types: tuple[str, ...] = ()
    # Nodes binding one name to a syntactically declared type via `type`/`name` fields.
    binder_types: tuple[str, ...] = ()
    # Declarator style: `(variable_declaration type: T (variable_declarator name: N))`.
    declarator_parent_types: tuple[str, ...] = ()
    declarator_type: str = ""
    type_field: str = "type"
    # Tried in order; `foreach` binds its variable under `left`, most nodes under `name`.
    binder_name_fields: tuple[str, ...] = ("name",)
    # Fallback when a binder's name is an unnamed child (Python `typed_parameter`):
    # the first child of one of these literal node types names the binding.
    binder_name_child_types: tuple[str, ...] = ()
    # Value fields inspected when a binder carries no type annotation: a direct
    # constructor-style call (`x = Foo(...)`) binds x to Foo, subject to the
    # resolver's unique-type gate.
    binder_value_fields: tuple[str, ...] = ()
    call_types: tuple[str, ...] = ()
    call_function_field: str = "function"
    # Callables whose explicit return annotation types a factory-call receiver.
    callable_types: tuple[str, ...] = ()
    return_type_field: str = "return_type"
    # Dotted type expressions readable as one type name (Python `attribute`).
    dotted_type_types: tuple[str, ...] = ()
    # Receiver spellings that denote the enclosing type instance (`self`, `cls`).
    self_receivers: tuple[str, ...] = ()
    # Type wrappers to unwrap when reading a declared type (nullable, array, generic).
    type_wrapper_types: tuple[str, ...] = ()
    # Generic type applications whose first child names the constructed type.
    generic_types: tuple[str, ...] = ()
    # Type nodes that name no indexable declaration (`var`, `int`, ...).
    opaque_type_types: tuple[str, ...] = ()
    # Ancestors that delimit a binder's scope; the nearest one wins.
    scope_types: tuple[str, ...] = ()
    # Ancestors that delimit a real variable frame (Python: functions and modules,
    # not `if` blocks). Empty means the whole file is one frame. Rebinding a name in
    # a nested scope of the SAME frame voids type proof; a nested frame does not.
    frame_types: tuple[str, ...] = ()
    # Parameter-list nodes whose untyped children still bind names locally.
    parameter_list_types: tuple[str, ...] = ()
    # Import nodes that bind a local alias (`import x as y`, `from m import x as y`);
    # the alias shadows type names like any untyped binding. References are never
    # resolved *through* the alias (documented as import-scope-narrowing).
    alias_import_types: tuple[str, ...] = ()
    alias_field: str = "alias"
    # Binders that introduce names with no type evidence (loop targets, `as`
    # targets, comprehension targets, walrus): recorded as untyped bindings that
    # shadow type names but never prove a type.
    untyped_binder_types: tuple[str, ...] = ()
    # Wrapper holding a decorated definition together with its decorators.
    decorator_wrapper_types: tuple[str, ...] = ()
    decorator_types: tuple[str, ...] = ()
    # Decorator names that make a callable static (its first parameter is not an
    # instance receiver regardless of spelling).
    static_decorators: tuple[str, ...] = ()
    # Whether a callable definition binds its own name in the enclosing scope.
    callable_defs_bind_names: bool = False


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
    # Reference-extraction coverage advertised to consumers of find_references.
    reference_extraction: ReferenceExtraction = ReferenceExtraction.PARTIAL
    # Usage-kind ids the reference query is known to capture.
    reference_usage_kinds: tuple[str, ...] = ()
    # Usage-kind ids whose syntax proves control transfers into the target: a strict
    # subset of reference_usage_kinds. Empty means the language yields no call evidence,
    # so a consumer must report its references neutrally rather than guessing a call
    # from either endpoint's declaration kind.
    call_usage_kinds: tuple[str, ...] = ()
    # Known extraction gaps, as short machine-readable ids.
    reference_limitations: tuple[str, ...] = ()
    # Grammar facts enabling structural resolution; absent means unique-name only.
    reference_syntax: ReferenceSyntax | None = None
    # Declarations that scope everything after them rather than a braced body, so
    # their symbol range must be widened to their parent's (C# file-scoped namespaces).
    file_scoped_container_types: tuple[str, ...] = ()


CSHARP_REFERENCE_SYNTAX = ReferenceSyntax(
    qualified_types=("qualified_name",),
    alias_qualified_types=("alias_qualified_name",),
    member_access_types=("member_access_expression",),
    namespace_types=("namespace_declaration", "file_scoped_namespace_declaration"),
    import_types=("using_directive",),
    binder_types=(
        "parameter",
        "property_declaration",
        "catch_declaration",
        "foreach_statement",
        "declaration_pattern",
        "tuple_element",
    ),
    declarator_parent_types=("variable_declaration",),
    declarator_type="variable_declarator",
    binder_name_fields=("name", "left"),
    type_wrapper_types=("nullable_type", "array_type"),
    generic_types=("generic_name",),
    opaque_type_types=("implicit_type", "predefined_type"),
    scope_types=(
        "block",
        "lambda_expression",
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "property_declaration",
        "class_declaration",
        "struct_declaration",
        "record_declaration",
        "interface_declaration",
        "enum_declaration",
        "compilation_unit",
    ),
    frame_types=(
        "lambda_expression",
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "property_declaration",
        "class_declaration",
        "struct_declaration",
        "record_declaration",
        "interface_declaration",
        "enum_declaration",
        "compilation_unit",
    ),
)

PYTHON_REFERENCE_SYNTAX = ReferenceSyntax(
    member_access_types=("attribute",),
    receiver_field="object",
    member_name_field="attribute",
    binder_types=("assignment", "typed_parameter", "typed_default_parameter"),
    binder_name_fields=("name", "left"),
    binder_name_child_types=("identifier",),
    binder_value_fields=("right",),
    call_types=("call",),
    callable_types=("function_definition",),
    dotted_type_types=("attribute", "dotted_name"),
    type_field="type",
    type_wrapper_types=("type",),
    generic_types=("generic_type",),
    self_receivers=("self", "cls"),
    scope_types=("module", "block", "function_definition", "class_definition", "lambda"),
    frame_types=("module", "function_definition", "class_definition", "lambda"),
    parameter_list_types=("parameters", "lambda_parameters"),
    alias_import_types=("aliased_import",),
    untyped_binder_types=(
        "for_statement",
        "as_pattern_target",
        "named_expression",
        "for_in_clause",
    ),
    decorator_wrapper_types=("decorated_definition",),
    decorator_types=("decorator",),
    static_decorators=("staticmethod", "abstractstaticmethod"),
    callable_defs_bind_names=True,
)


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
        reference_usage_kinds=(
            "member-access",
            "invocation",
            "nameof",
            "object-creation",
            "generic-type",
            "type-argument",
            "declared-type",
            "return-type",
            "type-literal",
            "cast-and-pattern",
            "attribute",
            "base-type",
        ),
        # `new T(...)` transfers control into a constructor exactly as an invocation
        # does; every other advertised kind is a type position, a metadata position,
        # or a member read that proves nothing about control flow.
        call_usage_kinds=("invocation", "object-creation"),
        reference_limitations=(
            # A member-access receiver is not itself captured, so a static access such
            # as `EndpointTags.Servers` records the member but not the type.
            "static-receiver-types",
            "extension-methods",
            "inherited-members",
            "partial-classes",
        ),
        reference_syntax=CSHARP_REFERENCE_SYNTAX,
        file_scoped_container_types=("file_scoped_namespace_declaration",),
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
        # The query captures only call syntax, so both advertised kinds prove a call;
        # every other reference stays an advertised limitation, not a neutral kind.
        reference_usage_kinds=("invocation", "object-creation"),
        call_usage_kinds=("invocation", "object-creation"),
        reference_limitations=(
            "dynamic-dispatch",
            "member-call-receiver-types",
            "import-alias-relations",
            "local-variables",
            "configuration-strings",
            "non-call-references",
        ),
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
        reference_usage_kinds=("invocation", "base-type", "decorator"),
        # A base list is a declaration position, and a bare decorator name sits in one
        # too; both are reported neutrally with their usage kind rather than as calls.
        call_usage_kinds=("invocation",),
        reference_limitations=(
            "inherited-members",
            "union-return-types",
            "cross-file-factory-returns",
            "dynamic-receivers",
            "import-scope-narrowing",
            "unindexed-import-shadows",
        ),
        reference_syntax=PYTHON_REFERENCE_SYNTAX,
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
        # The query captures only call syntax, so both advertised kinds prove a call;
        # every other reference stays an advertised limitation, not a neutral kind.
        reference_usage_kinds=("invocation", "object-creation"),
        call_usage_kinds=("invocation", "object-creation"),
        reference_limitations=(
            "dynamic-dispatch",
            "member-call-receiver-types",
            "import-alias-relations",
            "local-variables",
            "configuration-strings",
            "non-call-references",
        ),
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
        # TSX shares the TypeScript queries, so it advertises the same coverage.
        reference_usage_kinds=("invocation", "object-creation"),
        call_usage_kinds=("invocation", "object-creation"),
        reference_limitations=(
            "dynamic-dispatch",
            "member-call-receiver-types",
            "import-alias-relations",
            "local-variables",
            "configuration-strings",
            "non-call-references",
        ),
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


def reference_extraction(language: str) -> ReferenceExtraction:
    """Return the advertised reference-extraction coverage for a language id."""
    try:
        return LANGUAGES[language].reference_extraction
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc


def reference_usage_kinds(language: str) -> tuple[str, ...]:
    """Return the usage-kind ids covered by a language's reference query."""
    try:
        return LANGUAGES[language].reference_usage_kinds
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc


def call_usage_kinds(language: str) -> tuple[str, ...]:
    """Return the usage-kind ids that prove a call site in a language."""
    try:
        return LANGUAGES[language].call_usage_kinds
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc


def is_call_usage(language: str | None, usage_kind: str | None) -> bool:
    """Report whether a stored usage kind proves a call at a site in this language.

    The single place where usage-kind vocabulary becomes call semantics. An unknown
    language, an unlabelled site, or a language advertising no call kinds is never a
    call: absence of evidence is not evidence of a call.
    """
    if language is None or usage_kind is None:
        return False
    spec = LANGUAGES.get(language)
    return spec is not None and usage_kind in spec.call_usage_kinds


def reference_limitations(language: str) -> tuple[str, ...]:
    """Return the known reference-extraction gaps for a language id."""
    try:
        return LANGUAGES[language].reference_limitations
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc


def reference_syntax(language: str) -> ReferenceSyntax | None:
    """Return the structural-resolution grammar facts for a language id, if any."""
    try:
        return LANGUAGES[language].reference_syntax
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc


def file_scoped_container_types(language: str) -> tuple[str, ...]:
    """Return declaration node types that scope the remainder of their parent."""
    try:
        return LANGUAGES[language].file_scoped_container_types
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc
