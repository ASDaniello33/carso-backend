"""Tests for business error → HTTP mapping."""

from app.core.errors import NotFoundError, ReadOnlyViolationError


def test_read_only_violation_maps_to_403() -> None:
    # The mapping table enforces the read-only guarantee (instruction/10 §7).
    exc = ReadOnlyViolationError()
    assert exc.status_code == 403
    assert exc.code == "read_only_violation"


def test_error_payload_shape() -> None:
    exc = NotFoundError("Offre introuvable", details={"offre_id": "x"})
    assert exc.status_code == 404
    assert str(exc) == "Offre introuvable"
    assert exc.details == {"offre_id": "x"}
