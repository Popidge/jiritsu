from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .model import RESULT_SCHEMA_VERSION, WorkloadContract
from .probes import run_check
from .state import StatedSnapshot, collect_stated_facts


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
    contract: WorkloadContract,
    timeout_seconds: float = 5.0,
    stated_snapshot: StatedSnapshot | None = None,
) -> dict[str, Any]:
    capability_results: list[dict[str, Any]] = []
    for capability in contract.capabilities:
        checks = [
            run_check(check, timeout_seconds, stated_snapshot)
            for check in capability.checks
        ]
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
    contracts: Iterable[WorkloadContract],
    timeout_seconds: float = 5.0,
    *,
    stated_command: str | None = None,
    use_stated: bool = True,
) -> dict[str, Any]:
    contract_list = list(contracts)
    fact_ids = {
        check.parameters["fact"]
        for contract in contract_list
        for capability in contract.capabilities
        for check in capability.checks
        if check.check_type == "stated_fact"
    }
    stated_snapshot = collect_stated_facts(
        fact_ids,
        timeout_seconds=timeout_seconds,
        stated_command=stated_command,
        enabled=use_stated,
    )
    workloads = [
        assess_contract(contract, timeout_seconds, stated_snapshot)
        for contract in contract_list
    ]
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
    check_results = [
        check
        for workload in workloads
        for capability in workload["capabilities"]
        for check in capability["checks"]
    ]
    direct_count = sum(check["source"] == "direct_probe" for check in check_results)
    stated_count = sum(check["source"] == "jiritsu-stated" for check in check_results)
    fallback_count = sum("fallback" in check["details"] for check in check_results)
    if stated_count and direct_count:
        machine_source = "hybrid"
    elif stated_count:
        machine_source = "jiritsu-stated"
    else:
        machine_source = "direct_probes"
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": status,
        "assessed_at": _timestamp(),
        "machine_state": {
            "source": machine_source,
            "jiritsu_stated": stated_snapshot.status,
            "stated": stated_snapshot.public(),
            "stated_check_count": stated_count,
            "direct_probe_count": direct_count,
            "fallback_check_count": fallback_count,
        },
        "summary": {"workload_count": len(workloads), **counts},
        "workloads": workloads,
        "errors": [],
    }
