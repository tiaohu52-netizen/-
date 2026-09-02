"""Canonical LHGP admission namespace."""

from lhgp.admission.offer import ExecutorCandidateView, Offer
from lhgp.admission.refuse import AdmissionRefuseCode, AdmissionRefusedError

__all__ = [
    "AdmissionRefuseCode",
    "AdmissionRefusedError",
    "ExecutorCandidateView",
    "Offer",
]
