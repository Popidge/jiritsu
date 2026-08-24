from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable
from typing import Any

from .model import CollectionError, FactDefinition, SourceSpec


def command(source_id: str, *arguments: str) -> SourceSpec:
    return SourceSpec(source_id, "command", tuple(arguments))


def system_file(source_id: str, path: str) -> SourceSpec:
    return SourceSpec(source_id, "file", path)


def require_text(text: str) -> str:
    value = text.strip()
    if not value:
        raise ValueError("source returned no value")
    return value


def parse_hostname(text: str) -> str:
    return require_text(text)


def parse_os_release(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        values = shlex.split(raw_value, posix=True)
        fields[key] = values[0] if values else ""
    if "ID" not in fields or "NAME" not in fields:
        raise ValueError("os-release does not contain ID and NAME")
    result = {"id": fields["ID"], "name": fields["NAME"]}
    for source_key, output_key in (
        ("VERSION_ID", "version_id"),
        ("VERSION", "version"),
        ("PRETTY_NAME", "pretty_name"),
    ):
        if source_key in fields:
            result[output_key] = fields[source_key]
    return result


def parse_kernel(text: str) -> dict[str, str]:
    values = require_text(text).split()
    if len(values) != 3:
        raise ValueError(
            "uname output must contain kernel name, release, and architecture"
        )
    return {"name": values[0], "release": values[1], "architecture": values[2]}


def parse_omarchy_version(text: str) -> str:
    return require_text(text)


def parse_packages(text: str) -> dict[str, Any]:
    packages: list[dict[str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"invalid pacman record on line {line_number}")
        packages.append({"name": parts[0], "version": parts[1]})
    packages.sort(key=lambda package: package["name"])
    return {"count": len(packages), "packages": packages}


def parse_service_state(text: str) -> str:
    return require_text(text)


def parse_running_services(text: str) -> dict[str, Any]:
    names: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            names.append(stripped.split(maxsplit=1)[0])
    names.sort()
    return {"count": len(names), "units": names}


def _load_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"invalid JSON at line {error.lineno}, column {error.colno}"
        ) from error


def parse_cpu(text: str) -> dict[str, Any]:
    payload = _load_json(text)
    records = payload.get("lscpu") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError("lscpu JSON does not contain an lscpu array")
    fields = {
        str(record.get("field", "")).rstrip(":"): record.get("data")
        for record in records
        if isinstance(record, dict)
    }
    required = ("Architecture", "CPU(s)", "Model name")
    if any(not fields.get(key) for key in required):
        raise ValueError("lscpu JSON lacks required CPU fields")
    result: dict[str, Any] = {
        "architecture": fields["Architecture"],
        "logical_cpu_count": int(str(fields["CPU(s)"])),
        "model_name": fields["Model name"],
    }
    optional_text = {
        "Vendor ID": "vendor_id",
        "Virtualization": "virtualization",
    }
    optional_integer = {
        "Thread(s) per core": "threads_per_core",
        "Core(s) per socket": "cores_per_socket",
        "Socket(s)": "socket_count",
    }
    for input_key, output_key in optional_text.items():
        if fields.get(input_key):
            result[output_key] = fields[input_key]
    for input_key, output_key in optional_integer.items():
        if fields.get(input_key):
            result[output_key] = int(str(fields[input_key]))
    return result


def parse_memory(text: str) -> dict[str, int]:
    fields: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, raw_value = line.split(":", 1)
        parts = raw_value.split()
        if not parts:
            continue
        multiplier = 1024 if len(parts) > 1 and parts[1] == "kB" else 1
        fields[name] = int(parts[0]) * multiplier
    if "MemTotal" not in fields or "MemAvailable" not in fields:
        raise ValueError("meminfo lacks MemTotal or MemAvailable")
    return {
        "total_bytes": fields["MemTotal"],
        "available_bytes": fields["MemAvailable"],
    }


def _optional_int(values: dict[str, str], key: str) -> int | None:
    return int(values[key]) if key in values and values[key] else None


def _optional_float(values: dict[str, str], key: str) -> float | None:
    return float(values[key]) if key in values and values[key] else None


def parse_active_network(text: str) -> dict[str, Any] | None:
    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid Omarchy network record on line {line_number}")
        values[parts[0]] = parts[1]
    if not values:
        return None
    result: dict[str, Any] = {
        "interface": values.get("iface"),
        "kind": values.get("type"),
        "ipv4_address": values.get("ip"),
        "prefix_length": _optional_int(values, "prefix"),
        "gateway": values.get("gateway"),
        "ssid": values.get("ssid"),
        "signal_dbm": _optional_float(values, "signal_dbm"),
        "frequency_mhz": _optional_float(values, "freq"),
        "bitrate": values.get("bitrate"),
        "received_bytes": _optional_int(values, "rx_bytes"),
        "transmitted_bytes": _optional_int(values, "tx_bytes"),
        "router_ping_ms": _optional_float(values, "router_ping_ms"),
        "internet_ping_ms": _optional_float(values, "internet_ping_ms"),
    }
    return {key: value for key, value in result.items() if value is not None}


def parse_network_interfaces(text: str) -> list[dict[str, Any]]:
    payload = _load_json(text)
    if not isinstance(payload, list):
        raise ValueError("ip JSON root must be an array")
    interfaces: list[dict[str, Any]] = []
    for record in payload:
        if not isinstance(record, dict) or not isinstance(record.get("ifname"), str):
            raise ValueError("ip JSON contains an invalid interface record")
        addresses: list[dict[str, Any]] = []
        for address in record.get("addr_info", []):
            if (
                not isinstance(address, dict)
                or "family" not in address
                or "local" not in address
            ):
                raise ValueError("ip JSON contains an invalid address record")
            addresses.append(
                {
                    "family": address["family"],
                    "address": address["local"],
                    "prefix_length": address.get("prefixlen"),
                    "scope": address.get("scope"),
                }
            )
        interfaces.append(
            {
                "name": record["ifname"],
                "state": record.get("operstate"),
                "kind": record.get("link_type"),
                "mtu": record.get("mtu"),
                "addresses": addresses,
            }
        )
    interfaces.sort(key=lambda interface: interface["name"])
    return interfaces


def parse_snapshot_configurations(text: str) -> list[dict[str, str]]:
    payload = _load_json(text)
    configurations = payload.get("configs") if isinstance(payload, dict) else None
    if not isinstance(configurations, list):
        raise ValueError("snapper JSON does not contain a configs array")
    result: list[dict[str, str]] = []
    for record in configurations:
        if not isinstance(record, dict) or not isinstance(record.get("config"), str):
            raise ValueError("snapper JSON contains an invalid configuration")
        if not isinstance(record.get("subvolume"), str):
            raise ValueError("snapper configuration lacks a subvolume")
        result.append({"name": record["config"], "subvolume": record["subvolume"]})
    result.sort(key=lambda configuration: configuration["name"])
    return result


def parse_active_root(text: str) -> dict[str, Any]:
    payload = _load_json(text)
    filesystems = payload.get("filesystems") if isinstance(payload, dict) else None
    if not isinstance(filesystems, list) or len(filesystems) != 1:
        raise ValueError("findmnt JSON must contain one root filesystem")
    record = filesystems[0]
    if not isinstance(record, dict):
        raise ValueError("findmnt JSON contains an invalid filesystem record")
    source = record.get("source")
    filesystem = record.get("fstype")
    options = record.get("options", "")
    if not isinstance(source, str) or not isinstance(filesystem, str):
        raise ValueError("findmnt JSON lacks the root source or filesystem type")
    if not isinstance(options, str):
        raise ValueError("findmnt JSON contains invalid mount options")

    source_match = re.fullmatch(r"(.+)\[(.+)]", source)
    device = source_match.group(1) if source_match else source
    source_subvolume = source_match.group(2) if source_match else None
    option_subvolume = next(
        (
            option.split("=", 1)[1]
            for option in options.split(",")
            if option.startswith("subvol=")
        ),
        None,
    )
    subvolume = option_subvolume or source_subvolume
    snapshot_match = re.search(r"/\.snapshots/(\d+)/snapshot(?:/|$)", subvolume or "")
    return {
        "filesystem": filesystem,
        "device": device,
        "subvolume": subvolume,
        "snapper_snapshot_id": int(snapshot_match.group(1)) if snapshot_match else None,
    }


FACTS: tuple[FactDefinition, ...] = (
    FactDefinition(
        "system.hostname",
        "The configured static hostname.",
        system_file("system.hostname", "/etc/hostname"),
        parse_hostname,
    ),
    FactDefinition(
        "system.os",
        "Operating-system identity from os-release.",
        system_file("system.os_release", "/etc/os-release"),
        parse_os_release,
    ),
    FactDefinition(
        "system.kernel",
        "Running kernel name, release, and architecture.",
        command("system.uname", "uname", "-srm"),
        parse_kernel,
    ),
    FactDefinition(
        "system.omarchy.version",
        "Installed Omarchy version reported by its supported CLI.",
        command("omarchy.version", "omarchy", "version"),
        parse_omarchy_version,
    ),
    FactDefinition(
        "packages.installed",
        "Installed native packages and their exact versions.",
        command("packages.pacman_query", "pacman", "-Q"),
        parse_packages,
    ),
    FactDefinition(
        "services.system_state",
        "The overall systemd manager state.",
        command(
            "services.system_state",
            "systemctl",
            "show",
            "--property=SystemState",
            "--value",
        ),
        parse_service_state,
    ),
    FactDefinition(
        "services.running",
        "Names of currently running system service units.",
        command(
            "services.running",
            "systemctl",
            "list-units",
            "--type=service",
            "--state=running",
            "--no-legend",
            "--no-pager",
            "--plain",
        ),
        parse_running_services,
    ),
    FactDefinition(
        "hardware.cpu",
        "CPU architecture, topology, and model.",
        command("hardware.lscpu", "lscpu", "--json"),
        parse_cpu,
    ),
    FactDefinition(
        "hardware.memory",
        "Physical memory totals in bytes.",
        system_file("hardware.meminfo", "/proc/meminfo"),
        parse_memory,
    ),
    FactDefinition(
        "networks.active",
        "Active network details reported by Omarchy.",
        command("omarchy.network_status", "omarchy", "network", "status", "--verbose"),
        parse_active_network,
    ),
    FactDefinition(
        "networks.interfaces",
        "Kernel network interfaces and assigned addresses.",
        command("networks.ip_address", "ip", "-j", "address", "show"),
        parse_network_interfaces,
    ),
    FactDefinition(
        "snapshots.configurations",
        "Snapper configurations and their managed subvolumes.",
        command("snapshots.snapper_configs", "snapper", "--jsonout", "list-configs"),
        parse_snapshot_configurations,
    ),
    FactDefinition(
        "snapshots.active_root",
        "The active root subvolume and its Snapper snapshot ID, if present.",
        command(
            "snapshots.active_root",
            "findmnt",
            "--json",
            "--output",
            "SOURCE,FSTYPE,OPTIONS",
            "--target",
            "/",
        ),
        parse_active_root,
    ),
)


FACTS_BY_ID = {fact.fact_id: fact for fact in FACTS}


def select_facts(selectors: Iterable[str]) -> list[FactDefinition]:
    requested = list(selectors)
    if not requested:
        return list(FACTS)
    selected: list[FactDefinition] = []
    unknown: list[str] = []
    for selector in requested:
        matches = [
            fact
            for fact in FACTS
            if fact.fact_id == selector
            or fact.fact_id.startswith(selector.rstrip(".") + ".")
        ]
        if not matches:
            unknown.append(selector)
            continue
        for fact in matches:
            if fact not in selected:
                selected.append(fact)
    if unknown:
        raise CollectionError(
            "unknown_selector",
            "Unknown fact selector(s): " + ", ".join(unknown),
        )
    return selected
