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


def test_languages_without_reference_syntax_fall_back_to_unique_name(
    tmp_path: Path,
) -> None:
    """A language with no grammar metadata still resolves by unique name only."""
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
    assert relation.usage_kind is None
