"""Config file codecs and shape-aware access to the Synapse MCP entry.

JSON and YAML documents share one merge algorithm: ``dict`` and ruamel's
``CommentedMap`` are both mappings, ``list`` and ``CommentedSeq`` are both
sequences, so entry access only needs the declarative shape metadata. TOML is
handled as a marker-delimited text block because Codex config has no
structure-preserving writer in the standard library.
"""

import io
import json
from collections.abc import MutableMapping, MutableSequence
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from synapse.cli.adapters.model import ConfigFormat, ContainerShape, JsonObject, McpTarget

SERVER_NAME = "synapse"

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def loads(fmt: ConfigFormat, text: str, origin: str) -> JsonObject:
    """Parse a structured config document, rejecting non-mapping roots."""
    if not text.strip():
        return {}
    if fmt is ConfigFormat.JSON:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"{origin} contains invalid JSON."
            raise ValueError(msg) from exc
    elif fmt is ConfigFormat.YAML:
        try:
            data = _yaml.load(text)
        except YAMLError as exc:
            msg = f"{origin} contains invalid YAML."
            raise ValueError(msg) from exc
        if data is None:
            return {}
    else:
        msg = f"Unsupported structured config format: {fmt}"
        raise ValueError(msg)
    if not isinstance(data, MutableMapping):
        msg = f"{origin} must contain a mapping at the top level."
        raise ValueError(msg)
    return cast(JsonObject, data)


def dumps(fmt: ConfigFormat, data: JsonObject) -> str:
    """Serialize a structured config document."""
    if fmt is ConfigFormat.JSON:
        return json.dumps(data, indent=2) + "\n"
    if fmt is ConfigFormat.YAML:
        stream = io.StringIO()
        _yaml.dump(data, stream)
        return stream.getvalue()
    msg = f"Unsupported structured config format: {fmt}"
    raise ValueError(msg)


def apply_document_defaults(data: JsonObject, target: McpTarget) -> None:
    """Seed required top-level keys that the agent's format demands."""
    for key, value in target.document_defaults:
        if key not in data:
            data[key] = value


def document_is_empty(data: JsonObject, target: McpTarget) -> bool:
    """Return whether only Synapse-seeded scaffolding remains in the document."""
    if not data:
        return True
    default_keys = {key for key, _ in target.document_defaults}
    return bool(default_keys) and set(data) <= default_keys


def read_entry(data: JsonObject, target: McpTarget) -> Any:
    """Return the stored Synapse server entry, or None when absent."""
    container = _lookup(data, target.key_path)
    if container is None:
        return None
    if target.shape is ContainerShape.MAPPING:
        if not isinstance(container, MutableMapping):
            msg = f"Config key {'.'.join(target.key_path)} must be a mapping."
            raise ValueError(msg)
        return container.get(SERVER_NAME)
    if not isinstance(container, MutableSequence):
        msg = f"Config key {'.'.join(target.key_path)} must be a list."
        raise ValueError(msg)
    return _find_list_entry(container, target)


def write_entry(data: JsonObject, target: McpTarget, entry: JsonObject) -> None:
    """Insert or replace the Synapse server entry, preserving neighbours."""
    if target.shape is ContainerShape.MAPPING:
        parent = _ensure_mapping(data, target.key_path)
        parent[SERVER_NAME] = entry
        return
    container = _ensure_list(data, target.key_path)
    for index, item in enumerate(container):
        if _is_named_entry(item, target):
            container[index] = entry
            return
    container.append(entry)


def delete_entry(data: JsonObject, target: McpTarget) -> bool:
    """Remove the Synapse server entry and prune containers it emptied."""
    container = _lookup(data, target.key_path)
    if container is None:
        return False
    if target.shape is ContainerShape.MAPPING:
        if not isinstance(container, MutableMapping) or SERVER_NAME not in container:
            return False
        del container[SERVER_NAME]
    else:
        if not isinstance(container, MutableSequence):
            return False
        remaining = [item for item in container if not _is_named_entry(item, target)]
        if len(remaining) == len(container):
            return False
        del container[:]
        container.extend(remaining)
    _drop_empty_path(data, target.key_path)
    return True


def _find_list_entry(container: MutableSequence[Any], target: McpTarget) -> Any:
    for item in container:
        if _is_named_entry(item, target):
            return item
    return None


def _is_named_entry(item: Any, target: McpTarget) -> bool:
    if not target.name_field or not isinstance(item, MutableMapping):
        return False
    return bool(item.get(target.name_field) == SERVER_NAME)


def _lookup(data: JsonObject, key_path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in key_path:
        if not isinstance(current, MutableMapping):
            msg = f"Config key {key} must be a mapping."
            raise ValueError(msg)
        current = current.get(key)
        if current is None:
            return None
    return current


def _ensure_mapping(data: JsonObject, key_path: tuple[str, ...]) -> JsonObject:
    current: JsonObject = data
    for key in key_path:
        child = current.get(key)
        if child is None:
            child = {}
            current[key] = child
        if not isinstance(child, MutableMapping):
            msg = f"Config key {key} must be a mapping."
            raise ValueError(msg)
        current = cast(JsonObject, child)
    return current


def _ensure_list(data: JsonObject, key_path: tuple[str, ...]) -> MutableSequence[Any]:
    parent = _ensure_mapping(data, key_path[:-1])
    key = key_path[-1]
    child = parent.get(key)
    if child is None:
        child = []
        parent[key] = child
    if not isinstance(child, MutableSequence):
        msg = f"Config key {key} must be a list."
        raise ValueError(msg)
    return child


def _drop_empty_path(data: JsonObject, key_path: tuple[str, ...]) -> None:
    stack: list[tuple[JsonObject, str]] = []
    current: Any = data
    for key in key_path:
        child = current.get(key)
        if child is None:
            return
        stack.append((cast(JsonObject, current), key))
        current = child
    while stack and not current:
        parent, key = stack.pop()
        del parent[key]
        current = parent


def render_toml_entry(target: McpTarget, entry: JsonObject) -> str:
    """Render one TOML table for the Synapse entry, e.g. [mcp_servers.synapse]."""
    header = ".".join((*target.key_path, SERVER_NAME))
    lines = [f"[{header}]"]
    for key, value in entry.items():
        lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(value)
