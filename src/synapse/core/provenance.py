"""Which Synapse is actually running, and from where.

A developer can edit a checkout, run its tests green, and still be served by an MCP
host holding a older globally-installed build. Nothing in the responses distinguishes
the two, which makes validation quietly misleading. This module reports the identity of
the loaded code so that mismatch is visible rather than inferred.

Only installation identity is exposed — never environment variables, credentials, or
anything else about the host.
"""

import json
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path

import synapse
from synapse.core.index import INDEX_WRITER_CONTRACT_VERSION, SCHEMA_VERSION
from synapse.core.indexing import reference_extraction_fingerprint
from synapse.core.indexing.parser import REFERENCE_EXTRACTOR_VERSION

DISTRIBUTION_NAME = "locker-room-tools-synapse-mcp"


@dataclass(frozen=True, slots=True)
class RuntimeProvenance:
    """Identity of the running Synapse installation."""

    package_version: str
    package_location: str
    schema_version: int
    # Persistence invariants this build implements; a daemon recording a different
    # value cannot be reused, whatever its package version says.
    writer_contract_version: int
    extractor_version: int
    reference_fingerprint: str
    # None when the installation records no PEP 610 origin (e.g. a plain source tree).
    editable: bool | None
    source_url: str | None

    def to_payload(self) -> dict[str, object]:
        """Return the additive diagnostic block."""
        return asdict(self)


def _direct_url() -> dict[str, object] | None:
    """Return the installed distribution's PEP 610 origin record, if it has one."""
    try:
        raw = metadata.distribution(DISTRIBUTION_NAME).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    except OSError:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def runtime_provenance() -> RuntimeProvenance:
    """Return the identity of the Synapse package serving this process.

    `package_location` is the directory the code was imported from, which is what
    separates "my repository checkout" from "a frozen copy installed as a tool".
    """
    direct_url = _direct_url()
    editable: bool | None = None
    source_url: str | None = None
    if direct_url is not None:
        source_url = str(direct_url.get("url")) if direct_url.get("url") else None
        dir_info = direct_url.get("dir_info")
        # A directory install records editability; anything else (sdist, wheel, VCS)
        # is by definition not editable.
        if isinstance(dir_info, dict):
            editable = bool(dir_info.get("editable", False))
        elif "vcs_info" in direct_url or "archive_info" in direct_url:
            editable = False

    return RuntimeProvenance(
        package_version=synapse.__version__,
        package_location=str(Path(synapse.__file__).resolve().parent),
        schema_version=SCHEMA_VERSION,
        writer_contract_version=INDEX_WRITER_CONTRACT_VERSION,
        extractor_version=REFERENCE_EXTRACTOR_VERSION,
        reference_fingerprint=reference_extraction_fingerprint(),
        editable=editable,
        source_url=source_url,
    )
