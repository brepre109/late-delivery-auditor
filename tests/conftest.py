from datetime import date

import pytest

from late_delivery_auditor import AuditConfig, Shipment, audit_shipment, load_rules


@pytest.fixture(scope="session")
def rules():
    return load_rules()


@pytest.fixture
def run(rules):
    """Audit a single shipment built from keyword arguments."""

    def _run(as_of="2026-09-27", naive_delivered="local", **kw):
        kw.setdefault("tracking", "TEST0001")
        kw.setdefault("carrier", "UPS")
        kw.setdefault("service", "UPS Next Day Air")
        kw.setdefault("residential", "no")
        cfg = AuditConfig(as_of=date.fromisoformat(as_of), naive_delivered=naive_delivered)
        return audit_shipment(Shipment(**kw), rules, cfg)

    return _run
