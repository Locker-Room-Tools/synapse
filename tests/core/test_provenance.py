"""Tests for runtime provenance reporting."""

import json
from pathlib import Path

import pytest

import synapse
from synapse.core import provenance as provenance_module
from synapse.core.index import INDEX_WRITER_CONTRACT_VERSION, SCHEMA_VERSION
from synapse.core.indexing import reference_extraction_fingerprint
from synapse.core.indexing.parser import REFERENCE_EXTRACTOR_VERSION
from synapse.core.provenance import runtime_provenance


def test_runtime_provenance_identifies_the_loaded_installation() -> None:
    """The block names the version, the code location, and the index contract."""
    payload = runtime_provenance().to_payload()

    assert payload["package_version"] == synapse.__version__
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["writer_contract_version"] == INDEX_WRITER_CONTRACT_VERSION
    assert payload["extractor_version"] == REFERENCE_EXTRACTOR_VERSION
    assert payload["reference_fingerprint"] == reference_extraction_fingerprint()
    # The location is what distinguishes a checkout from a frozen installed copy.
    location = Path(str(payload["package_location"]))
    assert location.is_absolute()
    assert (location / "__init__.py").exists()


def test_runtime_provenance_never_leaks_environment_details() -> None:
    """Only installation identity is reported; nothing about the host."""
    payload = runtime_provenance().to_payload()

    assert set(payload) == {
        "package_version",
        "package_location",
        "schema_version",
        "writer_contract_version",
        "extractor_version",
        "reference_fingerprint",
        "editable",
        "source_url",
    }
    # The payload must survive JSON serialization for MCP and `--json` output.
    assert json.loads(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    ("direct_url", "expected_editable", "expected_source"),
    [
        ({"url": "file:///repo", "dir_info": {"editable": True}}, True, "file:///repo"),
        ({"url": "file:///repo", "dir_info": {}}, False, "file:///repo"),
        ({"url": "https://pypi/x.whl", "archive_info": {}}, False, "https://pypi/x.whl"),
        (None, None, None),
    ],
)
def test_editable_is_read_from_the_pep610_origin_record(
    monkeypatch: pytest.MonkeyPatch,
    direct_url: dict[str, object] | None,
    expected_editable: bool | None,
    expected_source: str | None,
) -> None:
    """A non-editable install built from a repo path is reported as non-editable."""
    monkeypatch.setattr(provenance_module, "_direct_url", lambda: direct_url)

    payload = runtime_provenance().to_payload()

    assert payload["editable"] is expected_editable
    assert payload["source_url"] == expected_source


def test_unreadable_origin_record_reports_unknown_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed direct_url.json degrades to `unknown`, never an exception."""

    class _Distribution:
        @staticmethod
        def read_text(name: str) -> str:
            return "{not json"

    monkeypatch.setattr(
        "synapse.core.provenance.metadata.distribution",
        lambda name: _Distribution(),
    )

    payload = runtime_provenance().to_payload()

    assert payload["editable"] is None
    assert payload["source_url"] is None
