from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .model import RESULT_SCHEMA_VERSION, WorkloadContract
from .probes import run_check


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _capability_status(checks: list[dict[str, Any]]) -> str:
    statuses = {check["status"] for check in checks}
    if "fail" in statuses:
        return "fail"
    if "error" in statuses:
        return "error"
    return "pass"


def assess_contract(
    contract: WorkloadContract, timeout_seconds: float = 5.0
) -> dict[str, Any]:
    capability_results: list[dict[str, Any]] = []
    for capability in contract.capabilities:
        checks = [run_check(check, timeout_seconds) for check in capability.checks]
        capability_results.append(
            {
                "id": capability.capability_id,
                "title": capability.title,
                "description": capability.description,
                "importance": capability.importance,
                "status": _capability_status(checks),
                "checks": checks,
            }
        )

    critical_pass = all(
        capability["status"] == "pass"
        for capability in capability_results
        if capability["importance"] == "critical"
    )
    all_pass = all(capability["status"] == "pass" for capability in capability_results)
    if not critical_pass:
        status = "unhealthy"
    elif not all_pass:
        status = "degraded"
    else:
        status = "healthy"
    return {
        "id": contract.contract_id,
        "title": contract.title,
        "description": contract.description,
        "status": status,
        "contract_source": {"kind": contract.source_kind, "path": contract.source},
        "capabilities": capability_results,
    }


def assess_contracts(
    contracts: Iterable[WorkloadContract], timeout_seconds: float = 5.0
) -> dict[str, Any]:
    workloads = [assess_contract(contract, timeout_seconds) for contract in contracts]
    counts = {
        status: sum(workload["status"] == status for workload in workloads)
        for status in ("healthy", "degraded", "unhealthy")
    }
    if counts["unhealthy"]:
        status = "unhealthy"
    elif counts["degraded"]:
        status = "degraded"
    else:
        status = "healthy"
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": status,
        "assessed_at": _timestamp(),
        "machine_state": {
            "source": "direct_probes",
            "jiritsu_stated": "not_used",
        },
        "summary": {"workload_count": len(workloads), **counts},
        "workloads": workloads,
        "errors": [],
    }
