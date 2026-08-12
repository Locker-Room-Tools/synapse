"""Deterministic two-call navigation over the symbol index.

The agent supplies literal repository vocabulary and investigation planning;
this package supplies compact, budgeted structural evidence: ranked orientation
(`orient_workspace`) and one-snapshot batch inspection (`inspect_symbols`).
"""

from synapse.core.navigation.budget import (
    INSPECT_DEFAULT_TOKEN_BUDGET,
    ORIENT_DEFAULT_TOKEN_BUDGET,
    PUBLIC_MAX_TOKEN_BUDGET,
    estimate_tokens,
)
from synapse.core.navigation.inspection import InspectRequest, inspect_symbols
from synapse.core.navigation.orient import OrientRequest, orient_workspace

__all__ = [
    "INSPECT_DEFAULT_TOKEN_BUDGET",
    "ORIENT_DEFAULT_TOKEN_BUDGET",
    "PUBLIC_MAX_TOKEN_BUDGET",
    "InspectRequest",
    "OrientRequest",
    "estimate_tokens",
    "inspect_symbols",
    "orient_workspace",
]
