"""late-delivery-auditor: find parcel shipments that qualify for carrier money-back guarantee claims."""

from .audit import AuditConfig, Result, Shipment, Status, audit_all, audit_shipment, summarise
from .csvio import read_shipments, write_results
from .rules import RuleSet, load_rules

__version__ = "0.1.0"

__all__ = [
    "AuditConfig", "Result", "RuleSet", "Shipment", "Status", "audit_all", "audit_shipment",
    "load_rules", "read_shipments", "summarise", "write_results", "__version__",
]
