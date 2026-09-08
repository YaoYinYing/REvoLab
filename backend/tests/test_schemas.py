"""Request-schema regressions: omission vs illegal null."""

import pytest
from pydantic import ValidationError

from revolab.schemas import EvidencePatch


def test_evidence_patch_rejects_null_for_required_fields():
    with pytest.raises(ValidationError):
        EvidencePatch(role=None)
    with pytest.raises(ValidationError):
        EvidencePatch(polarity=None)


def test_evidence_patch_allows_omission_and_nullable_clear():
    patch = EvidencePatch()  # full omission is valid
    assert patch.role is None
    assert patch.polarity is None
    # Nullable interpretive fields may be explicitly cleared.
    cleared = EvidencePatch(label=None, interpretation=None, confidence_source=None, scope=None)
    assert cleared.label is None
