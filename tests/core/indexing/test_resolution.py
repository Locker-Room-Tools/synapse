"""Tests for conservative structural reference resolution."""

from pathlib import Path

from synapse.core.indexing.parser import (
    build_reference_relations,
    extract_references,
    parse_source,
)
from synapse.core.indexing.resolution import build_resolution_facts
from synapse.core.models import Confidence, Relation, ResolutionMethod, Symbol

# Three unrelated types each declaring `Servers`, mirroring the shape that makes
# name-only resolution useless.
_DECLARATIONS = (
    (
        "Data/AppDbContext.cs",
        "namespace Sample.Data;\n"
        "public class AppDbContext\n"
        "{\n"
        "    public DbSet<Server> Servers { get; set; }\n"
        "}\n",
    ),
    (
        "Locations/Location.cs",
        "namespace Sample.Locations;\n"
        "public class Location\n"
        "{\n"
        "    public ICollection<Server> Servers { get; set; }\n"
        "}\n",
    ),
    (
        "Shared/EndpointTags.cs",
        "namespace Sample.Shared;\n"
        "public static class EndpointTags\n"
        "{\n"
        '    public const string Servers = "Servers";\n'
        "}\n",
    ),
    (
        "Servers/Server.cs",
        "namespace Sample.Servers;\npublic class Server\n{\n    public int Id { get; set; }\n}\n",
    ),
)


def _resolve_workspace(
    tmp_path: Path,
    usage_file: str,
    usage_source: str,
) -> tuple[list[Relation], list[Symbol]]:
    """Index the fixture declarations plus one usage file, then resolve its references."""
    symbols: list[Symbol] = []
    for relative_path, source in _DECLARATIONS:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        symbols.extend(
            parse_source(path, "csharp", source.encode(), workspace_root=tmp_path).symbols
        )

    usage_path = tmp_path / usage_file
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(usage_source, encoding="utf-8")
    parsed = parse_source(usage_path, "csharp", usage_source.encode(), workspace_root=tmp_path)
    symbols.extend(parsed.symbols)

    facts = build_resolution_facts(
        kinds={symbol.id: str(symbol.kind) for symbol in symbols},
        qualified_names={symbol.id: symbol.qualified_name or symbol.name for symbol in symbols},
    )
    name_index: dict[str, list[str]] = {}
    for symbol in symbols:
        for key in {symbol.name, symbol.qualified_name or symbol.name}:
            name_index.setdefault(key, []).append(symbol.id)

    relations = build_reference_relations(
        parsed.references,
        name_index,
        facts=facts,
        scope=parsed.scope,
    )
    return relations, symbols


def _relation_named(relations: list[Relation], name: str) -> Relation:
    return next(relation for relation in relations if relation.to_name == name)


def test_receiver_with_a_declared_type_resolves_the_member_exactly(tmp_path: Path) -> None:
    """`dbContext.Servers` binds to the member of the receiver's declared type."""
    relations, symbols = _resolve_workspace(
        tmp_path,
        "Usage/Endpoint.cs",
        "namespace Sample.Usage;\n"
        "using Sample.Data;\n"
        "public class Endpoint\n"
        "{\n"
        "    public static void Handle(AppDbContext dbContext)\n"
        "    {\n"
        "        var all = dbContext.Servers;\n"
        "    }\n"
        "}\n",
    )

    target = next(
        symbol for symbol in symbols if symbol.qualified_name == "Sample.Data.AppDbContext.Servers"
    )
    relation = _relation_named(relations, "Servers")
    assert relation.resolution is ResolutionMethod.EXACT
    assert relation.confidence is Confidence.HIGH
    assert relation.to_symbol_id == target.id


def test_static_type_receiver_resolves_the_member_exactly(tmp_path: Path) -> None:
    """A receiver that is itself a type name determines the member just as well."""
    relations, symbols = _resolve_workspace(
        tmp_path,
        "Usage/Tags.cs",
        "namespace Sample.Usage;\n"
        "using Sample.Shared;\n"
        "public class Tags\n"
        "{\n"
        "    public static string Name() { return EndpointTags.Servers; }\n"
        "}\n",
    )

    target = next(
        symbol
        for symbol in symbols
        if symbol.qualified_name == "Sample.Shared.EndpointTags.Servers"
    )
    relation = _relation_named(relations, "Servers")
    assert relation.resolution is ResolutionMethod.EXACT
    assert relation.to_symbol_id == target.id


def test_unknown_receiver_stays_ambiguous(tmp_path: Path) -> None:
    """An implicitly typed lambda parameter proves nothing, so the member stays ambiguous."""
    relations, _ = _resolve_workspace(
        tmp_path,
        "Usage/Config.cs",
        "namespace Sample.Usage;\n"
        "public class Config\n"
        "{\n"
        "    public void Apply(object builder)\n"
        "    {\n"
        "        Map(location => location.Servers);\n"
        "    }\n"
        "}\n",
    )

    relation = _relation_named(relations, "Servers")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.confidence is Confidence.LOW
    assert relation.to_symbol_id is None


def test_var_receiver_proves_nothing_and_stays_ambiguous(tmp_path: Path) -> None:
    """`var` contributes no binding, so its member access is never promoted."""
    relations, _ = _resolve_workspace(
        tmp_path,
        "Usage/Implicit.cs",
        "namespace Sample.Usage;\n"
        "public class Implicit\n"
        "{\n"
        "    public void Run()\n"
        "    {\n"
        "        var thing = Build();\n"
        "        var all = thing.Servers;\n"
        "    }\n"
        "}\n",
    )

    relation = _relation_named(relations, "Servers")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_fully_qualified_type_usage_resolves_exactly(tmp_path: Path) -> None:
    """A fully-qualified type name names exactly one declaration."""
    relations, symbols = _resolve_workspace(
        tmp_path,
        "Usage/Holder.cs",
        "namespace Sample.Usage;\n"
        "public class Holder\n"
        "{\n"
        "    public Sample.Servers.Server Item { get; set; }\n"
        "}\n",
    )

    target = next(symbol for symbol in symbols if symbol.qualified_name == "Sample.Servers.Server")
    relation = _relation_named(relations, "Server")
    assert relation.resolution is ResolutionMethod.EXACT
    assert relation.to_symbol_id == target.id
    assert relation.to_qualified_name == "Sample.Servers.Server"


def test_same_name_declarations_receive_no_false_confirmed_references(
    tmp_path: Path,
) -> None:
    """Nothing binds to a same-name declaration the syntax did not select."""
    relations, symbols = _resolve_workspace(
        tmp_path,
        "Usage/Endpoint.cs",
        "namespace Sample.Usage;\n"
        "using Sample.Data;\n"
        "public class Endpoint\n"
        "{\n"
        "    public static void Handle(AppDbContext dbContext)\n"
        "    {\n"
        "        var all = dbContext.Servers;\n"
        "    }\n"
        "}\n",
    )

    decoys = [
        symbol.id
        for symbol in symbols
        if symbol.qualified_name
        in {"Sample.Locations.Location.Servers", "Sample.Shared.EndpointTags.Servers"}
    ]
    assert len(decoys) == 2
    bound = {relation.to_symbol_id for relation in relations}
    assert bound.isdisjoint(decoys)


def test_resolution_facts_never_target_namespaces_or_imports(tmp_path: Path) -> None:
    """Structural boilerplate is excluded from the candidate space entirely."""
    source = "namespace Sample.Only;\nusing Sample.Other;\npublic class Thing { }\n"
    path = tmp_path / "only.cs"
    path.write_text(source, encoding="utf-8")
    symbols = parse_source(path, "csharp", source.encode(), workspace_root=tmp_path).symbols

    facts = build_resolution_facts(
        kinds={symbol.id: str(symbol.kind) for symbol in symbols},
        qualified_names={symbol.id: symbol.qualified_name or symbol.name for symbol in symbols},
    )
    excluded = {symbol.id for symbol in symbols if str(symbol.kind) in {"namespace", "import"}}
    assert excluded
    reachable = {symbol_id for ids in facts.suffix_index.values() for symbol_id in ids}
    assert reachable.isdisjoint(excluded)


def test_resolution_without_facts_falls_back_to_unique_name(
    tmp_path: Path,
) -> None:
    """Without workspace resolution facts, references resolve by unique name only."""
    path = tmp_path / "sample.py"
    path.write_text("def target():\n    return 1\n\ndef caller():\n    return target()\n", "utf-8")
    from synapse.core.indexing.parser import parse_file

    symbols = parse_file(path, "python", workspace_root=tmp_path)
    references = extract_references(path, "python", symbols, workspace_root=tmp_path)
    target = next(symbol for symbol in symbols if symbol.name == "target")

    relations = build_reference_relations(references, {"target": [target.id]})
    relation = _relation_named(relations, "target")
    assert relation.resolution is ResolutionMethod.UNIQUE_NAME
    assert relation.confidence is Confidence.MEDIUM
    # Resolution strength and usage kind are independent: a heuristically bound target
    # is still a syntactically proven call site.
    assert relation.usage_kind == "invocation"


_PYTHON_STORE = """class Repo:
    def save(self, item):
        return item

    def load(self):
        return None

class Cache:
    def save(self, item):
        return item
"""


def _resolve_python_usage(
    tmp_path: Path, usage_source: str, extra_sources: dict[str, str] | None = None
) -> list[Relation]:
    from synapse.core.indexing.parser import parse_source

    sources = {"store.py": _PYTHON_STORE, **(extra_sources or {}), "usage.py": usage_source}
    symbols = []
    parsed_usage = None
    for file_name, source in sources.items():
        parsed = parse_source(tmp_path / file_name, "python", source.encode(), tmp_path)
        symbols.extend(parsed.symbols)
        if file_name == "usage.py":
            parsed_usage = parsed
    assert parsed_usage is not None
    facts = build_resolution_facts(
        kinds={symbol.id: str(symbol.kind) for symbol in symbols},
        qualified_names={symbol.id: symbol.qualified_name or symbol.name for symbol in symbols},
    )
    name_index: dict[str, list[str]] = {}
    for symbol in symbols:
        name_index.setdefault(symbol.name, []).append(symbol.id)
        if symbol.qualified_name:
            name_index.setdefault(symbol.qualified_name, []).append(symbol.id)
    return build_reference_relations(
        parsed_usage.references, name_index, facts=facts, scope=parsed_usage.scope
    )


def test_python_typed_parameter_receiver_resolves_member_exactly(tmp_path: Path) -> None:
    relations = _resolve_python_usage(tmp_path, "def use(repo: Repo):\n    return repo.save(1)\n")
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.EXACT
    assert relation.to_symbol_id is not None
    assert "Repo.save" in relation.to_symbol_id


def test_python_annotated_local_and_constructor_receivers_resolve(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def annotated():\n    r: Repo = anything()\n    return r.load()\n\n"
        "def constructed():\n    c = Repo()\n    return c.save(2)\n",
    )
    load = _relation_named(relations, "load")
    save = _relation_named(relations, "save")
    assert load.resolution is ResolutionMethod.EXACT
    assert "Repo.load" in (load.to_symbol_id or "")
    assert save.resolution is ResolutionMethod.EXACT
    assert "Repo.save" in (save.to_symbol_id or "")


def test_python_factory_return_annotation_types_the_receiver(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def make_repo() -> Repo:\n    return Repo()\n\n"
        "def use():\n    return make_repo().save(3)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Repo.save" in (relation.to_symbol_id or "")


def test_python_self_receiver_resolves_within_the_enclosing_class(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "class Holder:\n"
        "    def helper(self):\n"
        "        return 1\n\n"
        "    def caller(self):\n"
        "        return self.helper()\n",
    )
    relation = _relation_named(relations, "helper")
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Holder.helper" in (relation.to_symbol_id or "")


def test_python_static_type_receiver_resolves_member(tmp_path: Path) -> None:
    relations = _resolve_python_usage(tmp_path, "def use():\n    return Repo.load(None)\n")
    relation = _relation_named(relations, "load")
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Repo.load" in (relation.to_symbol_id or "")


def test_python_unannotated_receiver_stays_ambiguous(tmp_path: Path) -> None:
    """`save` exists on Repo and Cache; without receiver evidence it must stay open."""
    relations = _resolve_python_usage(tmp_path, "def use(x):\n    return x.save(4)\n")
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_union_return_annotation_proves_nothing(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def maybe_repo() -> Repo | None:\n    return None\n\n"
        "def use():\n    return maybe_repo().save(5)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_generic_member_names_never_bind_without_receiver_evidence(
    tmp_path: Path,
) -> None:
    """Generic names on unknown receivers must not chain to unrelated declarations."""
    usage = (
        "def use(thing):\n    thing.add(1)\n    thing.get(2)\n    thing.run()\n    thing.find(3)\n"
    )
    extra = (
        "class A:\n    def add(self):\n        return 1\n\n"
        "class B:\n    def add(self):\n        return 2\n\n"
        "class OnlyRun:\n    def run(self):\n        return 3\n"
    )
    from synapse.core.indexing.parser import parse_source

    symbols = []
    for file_name, source in {
        "classes.py": extra,
        "usage.py": usage,
    }.items():
        parsed = parse_source(tmp_path / file_name, "python", source.encode(), tmp_path)
        symbols.extend(parsed.symbols)
        if file_name == "usage.py":
            parsed_usage = parsed
    facts = build_resolution_facts(
        kinds={symbol.id: str(symbol.kind) for symbol in symbols},
        qualified_names={symbol.id: symbol.qualified_name or symbol.name for symbol in symbols},
    )
    name_index: dict[str, list[str]] = {}
    for symbol in symbols:
        name_index.setdefault(symbol.name, []).append(symbol.id)
    relations = build_reference_relations(
        parsed_usage.references, name_index, facts=facts, scope=parsed_usage.scope
    )
    add = _relation_named(relations, "add")
    assert add.resolution is ResolutionMethod.AMBIGUOUS
    assert add.to_symbol_id is None
    # Even a workspace-unique method name is only heuristic without receiver evidence.
    run = _relation_named(relations, "run")
    assert run.resolution is not ResolutionMethod.EXACT


def test_python_conflicting_rebinding_stays_ambiguous(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use():\n    r = Repo()\n    r = Cache()\n    return r.save(6)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_constructor_call_receiver_types_itself(tmp_path: Path) -> None:
    """`Repo(...).save()` is a constructor receiver; the call names the type."""
    relations = _resolve_python_usage(tmp_path, "def use(c):\n    return Repo(c).save(7)\n")
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Repo.save" in (relation.to_symbol_id or "")


def test_python_factory_call_to_unknown_function_stays_ambiguous(tmp_path: Path) -> None:
    relations = _resolve_python_usage(tmp_path, "def use():\n    return mystery().save(8)\n")
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_function_shadowing_a_type_name_blocks_the_constructor_proof(
    tmp_path: Path,
) -> None:
    """A call target contested by a same-name function is never proven a constructor."""
    relations = _resolve_python_usage(
        tmp_path,
        "def Repo():\n    return None\n\ndef use():\n    r = Repo()\n    return r.save(1)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_imported_callable_shadowing_a_class_blocks_the_proof(tmp_path: Path) -> None:
    from synapse.core.indexing.parser import parse_source

    sources = {
        "store.py": _PYTHON_STORE,
        "funcs.py": "def Repo():\n    return None\n",
        "usage.py": "from funcs import Repo\n\ndef use():\n    r = Repo()\n    return r.save(1)\n",
    }
    symbols = []
    parsed_usage = None
    for file_name, source in sources.items():
        parsed = parse_source(tmp_path / file_name, "python", source.encode(), tmp_path)
        symbols.extend(parsed.symbols)
        if file_name == "usage.py":
            parsed_usage = parsed
    assert parsed_usage is not None
    facts = build_resolution_facts(
        kinds={symbol.id: str(symbol.kind) for symbol in symbols},
        qualified_names={symbol.id: symbol.qualified_name or symbol.name for symbol in symbols},
    )
    name_index: dict[str, list[str]] = {}
    for symbol in symbols:
        name_index.setdefault(symbol.name, []).append(symbol.id)
    relations = build_reference_relations(
        parsed_usage.references, name_index, facts=facts, scope=parsed_usage.scope
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_variable_named_like_a_type_is_not_static_access(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use():\n    Repo = 5\n    return Repo.save(1)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_untyped_parameter_shadowing_a_type_name_blocks_static_access(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use(Repo):\n    return Repo.save(1)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_conditional_rebinding_blocks_the_earlier_constructor_proof(
    tmp_path: Path,
) -> None:
    """A rebinding in a conditional block may have executed; the earlier type is unproven."""
    relations = _resolve_python_usage(
        tmp_path,
        "def use(flag):\n    r = Repo()\n    if flag:\n        r = Cache()\n    return r.save(2)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_conditional_untyped_rebinding_blocks_the_proof(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use(flag):\n    r = Repo()\n    if flag:\n        r = flag\n    return r.save(3)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_staticmethod_parameter_named_self_proves_no_instance(tmp_path: Path) -> None:
    """`self` is a convention, not proof: a static method's `self` is a plain value."""
    relations = _resolve_python_usage(
        tmp_path,
        "class Holder:\n"
        "    def helper(self):\n"
        "        return 1\n\n"
        "    @staticmethod\n"
        "    def caller(self):\n"
        "        return self.helper()\n",
    )
    relation = _relation_named(relations, "helper")
    assert relation.resolution is not ResolutionMethod.EXACT
    assert relation.confidence is not Confidence.HIGH


def test_python_reassigned_self_resolves_by_its_binding_not_the_class(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "class Holder:\n"
        "    def save(self, item):\n"
        "        return item\n\n"
        "    def caller(self):\n"
        "        self = Cache()\n"
        "        return self.save(9)\n",
    )
    relation = next(rel for rel in relations if rel.to_name == "save" and rel.start_line == 7)
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Cache.save" in (relation.to_symbol_id or "")


def test_python_untyped_reassigned_self_proves_nothing(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "class Holder:\n"
        "    def helper(self):\n"
        "        return 1\n\n"
        "    def caller(self, other):\n"
        "        self = other\n"
        "        return self.helper()\n",
    )
    relation = _relation_named(relations, "helper")
    assert relation.resolution is not ResolutionMethod.EXACT
    assert relation.confidence is not Confidence.HIGH


def test_python_rebinding_inside_a_nested_function_does_not_poison_the_outer_use(
    tmp_path: Path,
) -> None:
    """A nested `def` is a separate frame; its locals never rebind the outer name."""
    relations = _resolve_python_usage(
        tmp_path,
        "def use():\n"
        "    r = Repo()\n\n"
        "    def inner():\n"
        "        r = Cache()\n"
        "        return r\n\n"
        "    return r.save(4)\n",
    )
    relation = next(rel for rel in relations if rel.to_name == "save" and rel.start_line == 8)
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Repo.save" in (relation.to_symbol_id or "")


def test_python_conditional_rebinding_with_the_same_type_keeps_the_proof(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use(flag):\n    r = Repo()\n    if flag:\n        r = Repo()\n    return r.save(5)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Repo.save" in (relation.to_symbol_id or "")


def test_python_self_annotated_with_another_type_follows_the_annotation(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "class Elsewhere:\n    def caller(self: Repo):\n        return self.load()\n",
    )
    relation = _relation_named(relations, "load")
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Repo.load" in (relation.to_symbol_id or "")


def test_python_lambda_parameter_shadowing_a_type_name_blocks_static_access(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(tmp_path, "f = lambda Repo: Repo.save(1)\n")
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_aliased_import_shadowing_a_class_blocks_the_constructor_proof(
    tmp_path: Path,
) -> None:
    """`from funcs import build as Repo` rebinds Repo; the call is not a constructor."""
    relations = _resolve_python_usage(
        tmp_path,
        "from funcs import build as Repo\n\ndef use():\n    r = Repo()\n    return r.save(1)\n",
        extra_sources={"funcs.py": "def build():\n    return None\n"},
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_module_import_alias_shadowing_blocks_static_type_access(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "import funcs as Repo\n\ndef use():\n    return Repo.save(1)\n",
        extra_sources={"funcs.py": "def build():\n    return None\n"},
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is not ResolutionMethod.EXACT
    assert relation.confidence is not Confidence.HIGH


def test_python_qualified_staticmethod_decorator_still_proves_no_instance(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "class Holder:\n"
        "    def helper(self):\n"
        "        return 1\n\n"
        "    @builtins.staticmethod\n"
        "    def caller(self):\n"
        "        return self.helper()\n",
    )
    relation = _relation_named(relations, "helper")
    assert relation.resolution is not ResolutionMethod.EXACT
    assert relation.confidence is not Confidence.HIGH


def test_python_call_form_staticmethod_decorator_still_proves_no_instance(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "class Holder:\n"
        "    def helper(self):\n"
        "        return 1\n\n"
        "    @staticmethod()\n"
        "    def caller(self):\n"
        "        return self.helper()\n",
    )
    relation = _relation_named(relations, "helper")
    assert relation.resolution is not ResolutionMethod.EXACT
    assert relation.confidence is not Confidence.HIGH


def test_python_abstractstaticmethod_decorator_proves_no_instance(tmp_path: Path) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "class Holder:\n"
        "    def helper(self):\n"
        "        return 1\n\n"
        "    @abc.abstractstaticmethod\n"
        "    def caller(self):\n"
        "        return self.helper()\n",
    )
    relation = _relation_named(relations, "helper")
    assert relation.resolution is not ResolutionMethod.EXACT
    assert relation.confidence is not Confidence.HIGH


def test_python_for_loop_target_shadowing_a_type_name_blocks_static_access(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use(items):\n    for Repo in items:\n        return Repo.save(2)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_tuple_for_loop_target_shadowing_a_type_name_blocks_static_access(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use(items):\n    for Repo, x in items:\n        return Repo.save(2)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_with_as_target_shadowing_a_type_name_blocks_static_access(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use():\n    with open('x') as Repo:\n        return Repo.save(3)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_except_as_target_shadowing_a_type_name_blocks_static_access(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use():\n"
        "    try:\n"
        "        return None\n"
        "    except Exception as Repo:\n"
        "        return Repo.save(4)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_comprehension_target_shadowing_a_type_name_blocks_static_access(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use(items):\n    return [Repo.save(5) for Repo in items]\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_walrus_target_shadowing_a_type_name_blocks_static_access(
    tmp_path: Path,
) -> None:
    relations = _resolve_python_usage(
        tmp_path,
        "def use(get):\n    if (Repo := get()):\n        return Repo.save(6)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.AMBIGUOUS
    assert relation.to_symbol_id is None


def test_python_plain_import_of_a_class_does_not_shadow_the_constructor(
    tmp_path: Path,
) -> None:
    """Non-aliased imports are how types enter scope; they must not block proofs."""
    relations = _resolve_python_usage(
        tmp_path,
        "from store import Repo\n\ndef use():\n    r = Repo()\n    return r.save(1)\n",
    )
    relation = _relation_named(relations, "save")
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Repo.save" in (relation.to_symbol_id or "")


def test_python_functools_wraps_decorated_method_still_resolves_self(tmp_path: Path) -> None:
    """A non-static decorator must not withdraw the structural self proof."""
    relations = _resolve_python_usage(
        tmp_path,
        "class Holder:\n"
        "    def helper(self):\n"
        "        return 1\n\n"
        "    @functools.wraps(f)\n"
        "    def caller(self):\n"
        "        return self.helper()\n",
    )
    relation = _relation_named(relations, "helper")
    assert relation.resolution is ResolutionMethod.EXACT
    assert "Holder.helper" in (relation.to_symbol_id or "")
