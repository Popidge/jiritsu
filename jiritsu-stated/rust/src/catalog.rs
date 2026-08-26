use std::collections::BTreeMap;
use std::sync::LazyLock;

use serde::Serialize;
use serde_json::{Map, Value, json};

use crate::model::{CollectionError, SourceLocator, SourceSpec};

pub type Parser = fn(&str) -> Result<Value, String>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RefreshClass {
    Static,
    Dynamic,
}

#[derive(Clone, Debug)]
pub struct FactSpec {
    pub id: &'static str,
    pub description: &'static str,
    pub source: SourceSpec,
    pub parser: Parser,
    pub refresh_class: RefreshClass,
}

fn command(id: &'static str, arguments: &'static [&'static str]) -> SourceSpec {
    SourceSpec {
        id,
        locator: SourceLocator::Command(arguments),
    }
}

fn file(id: &'static str, path: &'static str) -> SourceSpec {
    SourceSpec {
        id,
        locator: SourceLocator::File(path),
    }
}

fn require_text(text: &str) -> Result<&str, String> {
    let value = text.trim();
    if value.is_empty() {
        Err("source returned no value".to_owned())
    } else {
        Ok(value)
    }
}

fn parse_hostname(text: &str) -> Result<Value, String> {
    Ok(json!(require_text(text)?))
}

fn parse_os_release(text: &str) -> Result<Value, String> {
    let mut fields = BTreeMap::new();
    for line in text.lines() {
        if line.is_empty() || line.starts_with('#') || !line.contains('=') {
            continue;
        }
        let Some((key, raw_value)) = line.split_once('=') else {
            continue;
        };
        let values = shell_words::split(raw_value).map_err(|error| error.to_string())?;
        fields.insert(key, values.first().cloned().unwrap_or_default());
    }
    let id = fields
        .get("ID")
        .ok_or_else(|| "os-release does not contain ID and NAME".to_owned())?;
    let name = fields
        .get("NAME")
        .ok_or_else(|| "os-release does not contain ID and NAME".to_owned())?;
    let mut result = Map::new();
    result.insert("id".to_owned(), json!(id));
    result.insert("name".to_owned(), json!(name));
    for (source, output) in [
        ("VERSION_ID", "version_id"),
        ("VERSION", "version"),
        ("PRETTY_NAME", "pretty_name"),
    ] {
        if let Some(value) = fields.get(source) {
            result.insert(output.to_owned(), json!(value));
        }
    }
    Ok(Value::Object(result))
}

fn parse_kernel(text: &str) -> Result<Value, String> {
    let values: Vec<&str> = require_text(text)?.split_whitespace().collect();
    if values.len() != 3 {
        return Err("uname output must contain kernel name, release, and architecture".to_owned());
    }
    Ok(json!({
        "name": values[0],
        "release": values[1],
        "architecture": values[2]
    }))
}

fn parse_omarchy_version(text: &str) -> Result<Value, String> {
    Ok(json!(require_text(text)?))
}

fn parse_packages(text: &str) -> Result<Value, String> {
    let mut packages = Vec::new();
    for (index, line) in text.lines().enumerate() {
        if line.is_empty() {
            continue;
        }
        let (name, version) = line
            .split_once(char::is_whitespace)
            .ok_or_else(|| format!("invalid pacman record on line {}", index + 1))?;
        packages.push(json!({"name": name, "version": version.trim_start()}));
    }
    packages.sort_by(|left, right| {
        left["name"]
            .as_str()
            .unwrap_or_default()
            .cmp(right["name"].as_str().unwrap_or_default())
    });
    Ok(json!({"count": packages.len(), "packages": packages}))
}

fn parse_service_state(text: &str) -> Result<Value, String> {
    Ok(json!(require_text(text)?))
}

fn parse_running_services(text: &str) -> Result<Value, String> {
    let mut units: Vec<&str> = text
        .lines()
        .filter_map(|line| line.split_whitespace().next())
        .collect();
    units.sort_unstable();
    Ok(json!({"count": units.len(), "units": units}))
}

fn parse_json(text: &str) -> Result<Value, String> {
    serde_json::from_str(text).map_err(|error| {
        format!(
            "invalid JSON at line {}, column {}",
            error.line(),
            error.column()
        )
    })
}

fn parse_cpu(text: &str) -> Result<Value, String> {
    let payload = parse_json(text)?;
    let records = payload
        .get("lscpu")
        .and_then(Value::as_array)
        .ok_or_else(|| "lscpu JSON does not contain an lscpu array".to_owned())?;
    let mut fields: BTreeMap<String, &Value> = BTreeMap::new();
    for record in records {
        if let (Some(field), Some(data)) = (
            record.get("field").and_then(Value::as_str),
            record.get("data"),
        ) {
            fields.insert(field.trim_end_matches(':').to_owned(), data);
        }
    }
    for required in ["Architecture", "CPU(s)", "Model name"] {
        if fields.get(required).is_none_or(|value| value.is_null()) {
            return Err("lscpu JSON lacks required CPU fields".to_owned());
        }
    }
    let integer = |name: &str| -> Result<i64, String> {
        fields[name]
            .as_str()
            .ok_or_else(|| format!("lscpu field {name} is not text"))?
            .parse::<i64>()
            .map_err(|error| error.to_string())
    };
    let mut result = Map::new();
    result.insert("architecture".to_owned(), fields["Architecture"].clone());
    result.insert("logical_cpu_count".to_owned(), json!(integer("CPU(s)")?));
    result.insert("model_name".to_owned(), fields["Model name"].clone());
    for (source, output) in [
        ("Vendor ID", "vendor_id"),
        ("Virtualization", "virtualization"),
    ] {
        if let Some(value) = fields.get(source).filter(|value| !value.is_null()) {
            result.insert(output.to_owned(), (*value).clone());
        }
    }
    for (source, output) in [
        ("Thread(s) per core", "threads_per_core"),
        ("Core(s) per socket", "cores_per_socket"),
        ("Socket(s)", "socket_count"),
    ] {
        if fields.contains_key(source) {
            result.insert(output.to_owned(), json!(integer(source)?));
        }
    }
    Ok(Value::Object(result))
}

fn parse_memory(text: &str) -> Result<Value, String> {
    let mut fields = BTreeMap::new();
    for line in text.lines() {
        let Some((name, raw_value)) = line.split_once(':') else {
            continue;
        };
        let parts: Vec<&str> = raw_value.split_whitespace().collect();
        if parts.is_empty() {
            continue;
        }
        let value = parts[0].parse::<u64>().map_err(|error| error.to_string())?;
        let multiplier = if parts.get(1) == Some(&"kB") { 1024 } else { 1 };
        fields.insert(name, value * multiplier);
    }
    let total = fields
        .get("MemTotal")
        .ok_or_else(|| "meminfo lacks MemTotal or MemAvailable".to_owned())?;
    let available = fields
        .get("MemAvailable")
        .ok_or_else(|| "meminfo lacks MemTotal or MemAvailable".to_owned())?;
    Ok(json!({"total_bytes": total, "available_bytes": available}))
}

fn parse_active_network(text: &str) -> Result<Value, String> {
    let mut values = BTreeMap::new();
    for (index, line) in text.lines().enumerate() {
        if line.is_empty() {
            continue;
        }
        let (key, value) = line
            .split_once('\t')
            .ok_or_else(|| format!("invalid Omarchy network record on line {}", index + 1))?;
        values.insert(key, value);
    }
    if values.is_empty() {
        return Ok(Value::Null);
    }
    let mut result = Map::new();
    for (source, output) in [
        ("iface", "interface"),
        ("type", "kind"),
        ("ip", "ipv4_address"),
        ("gateway", "gateway"),
        ("ssid", "ssid"),
        ("bitrate", "bitrate"),
    ] {
        if let Some(value) = values.get(source) {
            result.insert(output.to_owned(), json!(value));
        }
    }
    for (source, output) in [
        ("prefix", "prefix_length"),
        ("rx_bytes", "received_bytes"),
        ("tx_bytes", "transmitted_bytes"),
    ] {
        if let Some(value) = values.get(source) {
            let number = value.parse::<u64>().map_err(|error| error.to_string())?;
            result.insert(output.to_owned(), json!(number));
        }
    }
    for (source, output) in [
        ("signal_dbm", "signal_dbm"),
        ("freq", "frequency_mhz"),
        ("router_ping_ms", "router_ping_ms"),
        ("internet_ping_ms", "internet_ping_ms"),
    ] {
        if let Some(value) = values.get(source) {
            let number = value.parse::<f64>().map_err(|error| error.to_string())?;
            result.insert(output.to_owned(), json!(number));
        }
    }
    Ok(Value::Object(result))
}

fn parse_network_interfaces(text: &str) -> Result<Value, String> {
    let payload = parse_json(text)?;
    let records = payload
        .as_array()
        .ok_or_else(|| "ip JSON root must be an array".to_owned())?;
    let mut interfaces = Vec::new();
    for record in records {
        let name = record
            .get("ifname")
            .and_then(Value::as_str)
            .ok_or_else(|| "ip JSON contains an invalid interface record".to_owned())?;
        let address_records = record
            .get("addr_info")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut addresses = Vec::new();
        for address in address_records {
            let family = address
                .get("family")
                .ok_or_else(|| "ip JSON contains an invalid address record".to_owned())?;
            let local = address
                .get("local")
                .ok_or_else(|| "ip JSON contains an invalid address record".to_owned())?;
            addresses.push(json!({
                "family": family,
                "address": local,
                "prefix_length": address.get("prefixlen").cloned().unwrap_or(Value::Null),
                "scope": address.get("scope").cloned().unwrap_or(Value::Null)
            }));
        }
        interfaces.push(json!({
            "name": name,
            "state": record.get("operstate").cloned().unwrap_or(Value::Null),
            "kind": record.get("link_type").cloned().unwrap_or(Value::Null),
            "mtu": record.get("mtu").cloned().unwrap_or(Value::Null),
            "addresses": addresses
        }));
    }
    interfaces.sort_by(|left, right| {
        left["name"]
            .as_str()
            .unwrap_or_default()
            .cmp(right["name"].as_str().unwrap_or_default())
    });
    Ok(Value::Array(interfaces))
}

fn parse_snapshot_configurations(text: &str) -> Result<Value, String> {
    let payload = parse_json(text)?;
    let records = payload
        .get("configs")
        .and_then(Value::as_array)
        .ok_or_else(|| "snapper JSON does not contain a configs array".to_owned())?;
    let mut configurations = Vec::new();
    for record in records {
        let name = record
            .get("config")
            .and_then(Value::as_str)
            .ok_or_else(|| "snapper JSON contains an invalid configuration".to_owned())?;
        let subvolume = record
            .get("subvolume")
            .and_then(Value::as_str)
            .ok_or_else(|| "snapper configuration lacks a subvolume".to_owned())?;
        configurations.push(json!({"name": name, "subvolume": subvolume}));
    }
    configurations.sort_by(|left, right| {
        left["name"]
            .as_str()
            .unwrap_or_default()
            .cmp(right["name"].as_str().unwrap_or_default())
    });
    Ok(Value::Array(configurations))
}

fn parse_active_root(text: &str) -> Result<Value, String> {
    let payload = parse_json(text)?;
    let filesystems = payload
        .get("filesystems")
        .and_then(Value::as_array)
        .ok_or_else(|| "findmnt JSON must contain one root filesystem".to_owned())?;
    if filesystems.len() != 1 {
        return Err("findmnt JSON must contain one root filesystem".to_owned());
    }
    let record = &filesystems[0];
    let source = record
        .get("source")
        .and_then(Value::as_str)
        .ok_or_else(|| "findmnt JSON lacks the root source or filesystem type".to_owned())?;
    let filesystem = record
        .get("fstype")
        .and_then(Value::as_str)
        .ok_or_else(|| "findmnt JSON lacks the root source or filesystem type".to_owned())?;
    let options = record
        .get("options")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let (device, source_subvolume) = if let Some(open) = source.rfind('[') {
        if source.ends_with(']') {
            (&source[..open], Some(&source[open + 1..source.len() - 1]))
        } else {
            (source, None)
        }
    } else {
        (source, None)
    };
    let option_subvolume = options
        .split(',')
        .find_map(|option| option.strip_prefix("subvol="));
    let subvolume = option_subvolume.or(source_subvolume);
    let snapshot_id = subvolume.and_then(|value| {
        let marker = "/.snapshots/";
        let rest = value.split_once(marker)?.1;
        let (number, suffix) = rest.split_once('/')?;
        (suffix == "snapshot" || suffix.starts_with("snapshot/"))
            .then(|| number.parse::<u64>().ok())
            .flatten()
    });
    Ok(json!({
        "filesystem": filesystem,
        "device": device,
        "subvolume": subvolume,
        "snapper_snapshot_id": snapshot_id
    }))
}

pub static FACTS: LazyLock<Vec<FactSpec>> = LazyLock::new(|| {
    vec![
        FactSpec {
            id: "system.hostname",
            description: "The configured static hostname.",
            source: file("system.hostname", "/etc/hostname"),
            parser: parse_hostname,
            refresh_class: RefreshClass::Static,
        },
        FactSpec {
            id: "system.os",
            description: "Operating-system identity from os-release.",
            source: file("system.os_release", "/etc/os-release"),
            parser: parse_os_release,
            refresh_class: RefreshClass::Static,
        },
        FactSpec {
            id: "system.kernel",
            description: "Running kernel name, release, and architecture.",
            source: command("system.uname", &["uname", "-srm"]),
            parser: parse_kernel,
            refresh_class: RefreshClass::Static,
        },
        FactSpec {
            id: "system.omarchy.version",
            description: "Installed Omarchy version reported by its supported CLI.",
            source: command("omarchy.version", &["omarchy", "version"]),
            parser: parse_omarchy_version,
            refresh_class: RefreshClass::Static,
        },
        FactSpec {
            id: "packages.installed",
            description: "Installed native packages and their exact versions.",
            source: command("packages.pacman_query", &["pacman", "-Q"]),
            parser: parse_packages,
            refresh_class: RefreshClass::Static,
        },
        FactSpec {
            id: "services.system_state",
            description: "The overall systemd manager state.",
            source: command(
                "services.system_state",
                &["systemctl", "show", "--property=SystemState", "--value"],
            ),
            parser: parse_service_state,
            refresh_class: RefreshClass::Dynamic,
        },
        FactSpec {
            id: "services.running",
            description: "Names of currently running system service units.",
            source: command(
                "services.running",
                &[
                    "systemctl",
                    "list-units",
                    "--type=service",
                    "--state=running",
                    "--no-legend",
                    "--no-pager",
                    "--plain",
                ],
            ),
            parser: parse_running_services,
            refresh_class: RefreshClass::Dynamic,
        },
        FactSpec {
            id: "hardware.cpu",
            description: "CPU architecture, topology, and model.",
            source: command("hardware.lscpu", &["lscpu", "--json"]),
            parser: parse_cpu,
            refresh_class: RefreshClass::Static,
        },
        FactSpec {
            id: "hardware.memory",
            description: "Physical memory totals in bytes.",
            source: file("hardware.meminfo", "/proc/meminfo"),
            parser: parse_memory,
            refresh_class: RefreshClass::Dynamic,
        },
        FactSpec {
            id: "networks.active",
            description: "Active network details reported by Omarchy.",
            source: command(
                "omarchy.network_status",
                &["omarchy", "network", "status", "--verbose"],
            ),
            parser: parse_active_network,
            refresh_class: RefreshClass::Dynamic,
        },
        FactSpec {
            id: "networks.interfaces",
            description: "Kernel network interfaces and assigned addresses.",
            source: command("networks.ip_address", &["ip", "-j", "address", "show"]),
            parser: parse_network_interfaces,
            refresh_class: RefreshClass::Dynamic,
        },
        FactSpec {
            id: "snapshots.configurations",
            description: "Snapper configurations and their managed subvolumes.",
            source: command(
                "snapshots.snapper_configs",
                &["snapper", "--jsonout", "list-configs"],
            ),
            parser: parse_snapshot_configurations,
            refresh_class: RefreshClass::Static,
        },
        FactSpec {
            id: "snapshots.active_root",
            description: "The active root subvolume and its Snapper snapshot ID, if present.",
            source: command(
                "snapshots.active_root",
                &[
                    "findmnt",
                    "--json",
                    "--output",
                    "SOURCE,FSTYPE,OPTIONS",
                    "--target",
                    "/",
                ],
            ),
            parser: parse_active_root,
            refresh_class: RefreshClass::Dynamic,
        },
    ]
});

pub fn select_facts(selectors: &[String]) -> Result<Vec<&'static FactSpec>, CollectionError> {
    if selectors.is_empty() {
        return Ok(FACTS.iter().collect());
    }
    let mut selected = Vec::new();
    let mut unknown = Vec::new();
    for selector in selectors {
        let prefix = format!("{}.", selector.trim_end_matches('.'));
        let matches: Vec<&FactSpec> = FACTS
            .iter()
            .filter(|fact| fact.id == selector || fact.id.starts_with(&prefix))
            .collect();
        if matches.is_empty() {
            unknown.push(selector.clone());
            continue;
        }
        for fact in matches {
            if !selected
                .iter()
                .any(|existing: &&FactSpec| existing.id == fact.id)
            {
                selected.push(fact);
            }
        }
    }
    if unknown.is_empty() {
        Ok(selected)
    } else {
        Err(CollectionError::new(
            "unknown_selector",
            format!("Unknown fact selector(s): {}", unknown.join(", ")),
        ))
    }
}

pub fn facts_for_class(class: RefreshClass) -> Vec<&'static FactSpec> {
    FACTS
        .iter()
        .filter(|fact| fact.refresh_class == class)
        .collect()
}

#[derive(Serialize)]
pub struct CatalogFact {
    pub id: &'static str,
    pub description: &'static str,
    pub source: crate::model::Source,
}

pub fn catalog() -> Vec<CatalogFact> {
    FACTS
        .iter()
        .map(|fact| CatalogFact {
            id: fact.id,
            description: fact.description,
            source: fact.source.public(),
        })
        .collect()
}
