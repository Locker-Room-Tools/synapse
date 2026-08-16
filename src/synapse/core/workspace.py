"""Workspace identity, storage paths, and metadata helpers."""

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

DATA_ROOT_ENV_VAR = "SYNAPSE_DATA_DIR"
DEFAULT_DB_NAME = "index.sqlite"


@dataclass(frozen=True, slots=True)
class WorkspaceMetadata:
    """Persisted metadata for one indexed workspace."""

    name: str
    path: str
    workspace_id: str
    created_at: str
    last_indexed_at: str | None
    languages: list[str]


def normalize_workspace_path(path: Path | str) -> Path:
    """Return a normalized absolute workspace path."""
    return Path(path).expanduser().resolve()


def require_workspace_path(path: Path | str) -> Path:
    """Return a normalized workspace path or reject a missing/non-directory path."""
    normalized_path = normalize_workspace_path(path)
    if not normalized_path.is_dir():
        msg = f"Workspace is not a directory: {normalized_path}"
        raise NotADirectoryError(msg)
    return normalized_path


def detect_workspace_root(path: Path | str = ".") -> Path:
    """Return the nearest Git root, falling back to the given directory."""
    candidate = require_workspace_path(path)
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists():
            return parent
    return candidate


def workspace_id(path: Path | str) -> str:
    """Return the deterministic workspace identifier for a path."""
    normalized_path = str(normalize_workspace_path(path))
    return hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()


def _data_root() -> Path:
    override = os.environ.get(DATA_ROOT_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data).expanduser().resolve() / "synapse"
    return Path.home() / ".local" / "share" / "synapse"


def data_dir_path(path: Path | str) -> Path:
    """Return the per-workspace data directory path without creating it."""
    return _data_root() / "workspaces" / workspace_id(path)


def data_dir(path: Path | str) -> Path:
    """Return the per-workspace data directory, creating it when needed."""
    directory = data_dir_path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def db_path(path: Path | str) -> Path:
    """Return the SQLite index path for a workspace."""
    return data_dir(path) / DEFAULT_DB_NAME


def db_file_path(path: Path | str) -> Path:
    """Return the SQLite index path without creating its parent directory."""
    return data_dir_path(path) / DEFAULT_DB_NAME


def metadata_path(path: Path | str) -> Path:
    """Return the metadata JSON path for a workspace."""
    return data_dir(path) / "metadata.json"


def metadata_file_path(path: Path | str) -> Path:
    """Return the metadata JSON path without creating its parent directory."""
    return data_dir_path(path) / "metadata.json"


def logs_dir(path: Path | str) -> Path:
    """Return the logs directory for a workspace, creating it when needed."""
    directory = data_dir(path) / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def watch_state_path(path: Path | str) -> Path:
    """Return the persisted watch status path for a workspace."""
    return data_dir(path) / "watch.json"


def watch_state_file_path(path: Path | str) -> Path:
    """Return the watch status path without creating its parent directory."""
    return data_dir_path(path) / "watch.json"


def watch_lock_path(path: Path | str) -> Path:
    """Return the singleton watch lock path for a workspace."""
    return data_dir(path) / "watch.lock"


def watch_journal_path(path: Path | str) -> Path:
    """Return the watch batch journal path for a workspace."""
    return data_dir(path) / "watch.journal"


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def atomic_write_text(path: Path, text: str) -> Path:
    """Write text through a pid-suffixed temporary file and os.replace.

    Newlines are written verbatim so a file's existing line endings survive a rewrite.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def read_metadata(path: Path | str) -> WorkspaceMetadata | None:
    """Load workspace metadata, treating missing or unreadable metadata as absent.

    Metadata is regenerable state: a corrupt or truncated file must read as "not
    initialized" so the ensure path rewrites it instead of crashing every status,
    ensure, and watch probe that asks whether the workspace is initialized.
    """
    file_path = metadata_file_path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return WorkspaceMetadata(
            name=str(payload["name"]),
            path=str(payload["path"]),
            workspace_id=str(payload["workspace_id"]),
            created_at=str(payload["created_at"]),
            last_indexed_at=payload.get("last_indexed_at"),
            languages=sorted(str(language) for language in payload.get("languages", [])),
        )
    except (KeyError, TypeError):
        return None


def write_metadata(
    path: Path | str,
    *,
    last_indexed_at: str | None,
    languages: list[str],
) -> WorkspaceMetadata:
    """Persist workspace metadata and return the stored representation."""
    normalized_path = normalize_workspace_path(path)
    existing = read_metadata(normalized_path)
    metadata = WorkspaceMetadata(
        name=normalized_path.name,
        path=str(normalized_path),
        workspace_id=workspace_id(normalized_path),
        created_at=existing.created_at if existing else _utc_now(),
        last_indexed_at=last_indexed_at,
        languages=sorted(set(languages)),
    )
    atomic_write_text(
        metadata_path(normalized_path),
        json.dumps(asdict(metadata), indent=2, sort_keys=True),
    )
    return metadata
