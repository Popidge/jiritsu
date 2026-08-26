use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::Value;
use tokio::process::Command;
use tokio::time::timeout;

use crate::model::{
    CollectionError, Observation, ProviderConfig, SCHEMA_VERSION, SourceLocator, SourceSpec,
};

const ERROR_DETAIL_LIMIT: usize = 500;
const FIXTURE_LIMIT: u64 = 16 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize)]
struct FixturePayload {
    schema_version: String,
    sources: BTreeMap<String, FixtureEntry>,
}

#[derive(Clone, Debug, Deserialize)]
struct FixtureEntry {
    kind: String,
    observed_at: String,
    #[serde(default)]
    stdout: Option<String>,
    #[serde(default)]
    stderr: Option<String>,
    #[serde(default)]
    content: Option<String>,
    #[serde(default)]
    exit_code: Option<i32>,
    #[serde(default)]
    error: Option<Value>,
    #[serde(default)]
    retryable: bool,
}

#[derive(Clone, Debug)]
enum ProviderData {
    Live,
    Fixture(Arc<BTreeMap<String, FixtureEntry>>),
}

#[derive(Clone, Debug)]
pub struct SourceProvider {
    data: ProviderData,
    timeout: Duration,
}

impl SourceProvider {
    pub async fn load(
        configuration: &ProviderConfig,
        timeout_duration: Duration,
    ) -> Result<Self, CollectionError> {
        match configuration {
            ProviderConfig::Live => Ok(Self {
                data: ProviderData::Live,
                timeout: timeout_duration,
            }),
            ProviderConfig::Fixture(path) => {
                let metadata = tokio::fs::metadata(path).await.map_err(|error| {
                    let code = if error.kind() == std::io::ErrorKind::NotFound {
                        "fixture_not_found"
                    } else if error.kind() == std::io::ErrorKind::PermissionDenied {
                        "fixture_denied"
                    } else {
                        "fixture_invalid"
                    };
                    CollectionError::new(code, format!("Cannot read fixture: {error}"))
                })?;
                if metadata.len() > FIXTURE_LIMIT {
                    return Err(CollectionError::new(
                        "fixture_invalid",
                        "Fixture exceeds the 16 MiB limit",
                    ));
                }
                let text = tokio::fs::read_to_string(path).await.map_err(|error| {
                    let code = if error.kind() == std::io::ErrorKind::NotFound {
                        "fixture_not_found"
                    } else if error.kind() == std::io::ErrorKind::PermissionDenied {
                        "fixture_denied"
                    } else {
                        "fixture_invalid"
                    };
                    CollectionError::new(code, format!("Cannot read fixture: {error}"))
                })?;
                if text.len() as u64 > FIXTURE_LIMIT {
                    return Err(CollectionError::new(
                        "fixture_invalid",
                        "Fixture exceeds the 16 MiB limit",
                    ));
                }
                let payload: FixturePayload = serde_json::from_str(&text).map_err(|error| {
                    CollectionError::new(
                        "fixture_invalid",
                        format!(
                            "Fixture is not valid JSON at line {}, column {}",
                            error.line(),
                            error.column()
                        ),
                    )
                })?;
                validate_fixture(&payload)?;
                Ok(Self {
                    data: ProviderData::Fixture(Arc::new(payload.sources)),
                    timeout: timeout_duration,
                })
            }
        }
    }

    pub async fn observe(&self, source: &SourceSpec) -> Result<Observation, CollectionError> {
        match &self.data {
            ProviderData::Live => self.observe_live(source).await,
            ProviderData::Fixture(sources) => observe_fixture(sources, source),
        }
    }

    async fn observe_live(&self, source: &SourceSpec) -> Result<Observation, CollectionError> {
        match &source.locator {
            SourceLocator::File(path) => {
                let text = tokio::fs::read_to_string(path).await.map_err(|error| {
                    let code = match error.kind() {
                        std::io::ErrorKind::NotFound => "source_unavailable",
                        std::io::ErrorKind::PermissionDenied => "source_denied",
                        _ => "source_failed",
                    };
                    let message = match error.kind() {
                        std::io::ErrorKind::NotFound => {
                            "Required system file does not exist".to_owned()
                        }
                        std::io::ErrorKind::PermissionDenied => {
                            "Permission was denied while reading the source".to_owned()
                        }
                        _ => format!("Could not read source: {error}"),
                    };
                    CollectionError::new(code, message)
                        .source(source.public())
                        .retryable(code == "source_failed")
                })?;
                Ok(Observation {
                    source: source.public(),
                    text,
                    observed_at: Utc::now(),
                    fixture: false,
                })
            }
            SourceLocator::Command(arguments) => {
                let mut command = Command::new(arguments[0]);
                command
                    .args(&arguments[1..])
                    .env("LC_ALL", "C.UTF-8")
                    .kill_on_drop(true);
                let output = match timeout(self.timeout, command.output()).await {
                    Ok(Ok(output)) => output,
                    Ok(Err(error)) => {
                        let code = if error.kind() == std::io::ErrorKind::NotFound {
                            "source_unavailable"
                        } else {
                            "source_failed"
                        };
                        let message = if error.kind() == std::io::ErrorKind::NotFound {
                            format!("Required command is not installed: {}", arguments[0])
                        } else {
                            format!("Could not run source: {error}")
                        };
                        return Err(CollectionError::new(code, message)
                            .source(source.public())
                            .retryable(code == "source_failed"));
                    }
                    Err(_) => {
                        return Err(CollectionError::new(
                            "source_timeout",
                            format!(
                                "Source did not respond within {} seconds",
                                self.timeout.as_secs_f64()
                            ),
                        )
                        .source(source.public())
                        .retryable(true));
                    }
                };
                let stdout = String::from_utf8(output.stdout).map_err(|error| {
                    CollectionError::new(
                        "source_failed",
                        format!("Source standard output is not UTF-8: {error}"),
                    )
                    .source(source.public())
                })?;
                let stderr = String::from_utf8_lossy(&output.stderr);
                if !output.status.success() {
                    let exit_status = output
                        .status
                        .code()
                        .map_or_else(|| "signal".to_owned(), |code| code.to_string());
                    let raw_detail = if stderr.trim().is_empty() {
                        stdout.trim()
                    } else {
                        stderr.trim()
                    };
                    let detail = bounded(raw_detail);
                    let suffix = if detail.is_empty() {
                        String::new()
                    } else {
                        format!(": {detail}")
                    };
                    return Err(CollectionError::new(
                        "source_failed",
                        format!("Source exited with status {exit_status}{suffix}"),
                    )
                    .source(source.public())
                    .retryable(true));
                }
                Ok(Observation {
                    source: source.public(),
                    text: stdout,
                    observed_at: Utc::now(),
                    fixture: false,
                })
            }
        }
    }
}

fn validate_fixture(payload: &FixturePayload) -> Result<(), CollectionError> {
    if payload.schema_version != SCHEMA_VERSION {
        return Err(CollectionError::new(
            "fixture_invalid",
            format!("Fixture schema_version must be \"{SCHEMA_VERSION}\""),
        ));
    }
    for (source_id, entry) in &payload.sources {
        if entry.kind != "command" && entry.kind != "file" {
            return Err(CollectionError::new(
                "fixture_invalid",
                format!("Fixture source {source_id:?} has an invalid kind"),
            ));
        }
        DateTime::parse_from_rfc3339(&entry.observed_at).map_err(|_| {
            CollectionError::new(
                "fixture_invalid",
                format!("Fixture source {source_id:?} has an invalid observed_at"),
            )
        })?;
        if entry.error.is_none() {
            let has_text = if entry.kind == "command" {
                entry.stdout.is_some()
            } else {
                entry.content.is_some()
            };
            if !has_text {
                let field = if entry.kind == "command" {
                    "stdout"
                } else {
                    "content"
                };
                return Err(CollectionError::new(
                    "fixture_invalid",
                    format!("Fixture source {source_id:?} needs string {field}"),
                ));
            }
        }
    }
    Ok(())
}

fn observe_fixture(
    sources: &BTreeMap<String, FixtureEntry>,
    source: &SourceSpec,
) -> Result<Observation, CollectionError> {
    let entry = sources.get(source.id).ok_or_else(|| {
        CollectionError::new(
            "fixture_source_missing",
            "Fixture has no payload for this source",
        )
        .source(source.public())
    })?;
    let expected_kind = source.public().kind;
    if entry.kind != expected_kind {
        return Err(CollectionError::new(
            "fixture_source_mismatch",
            format!(
                "Fixture source kind is {:?}, expected {:?}",
                entry.kind, expected_kind
            ),
        )
        .source(source.public()));
    }
    if let Some(error) = &entry.error {
        let message = error
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| error.to_string());
        return Err(CollectionError::new("source_failed", message)
            .source(source.public())
            .retryable(entry.retryable));
    }
    let exit_code = entry.exit_code.unwrap_or(0);
    if entry.kind == "command" && exit_code != 0 {
        let detail = entry.stderr.as_deref().unwrap_or_default().trim();
        let suffix = if detail.is_empty() {
            String::new()
        } else {
            format!(": {detail}")
        };
        return Err(CollectionError::new(
            "source_failed",
            format!("Source exited with status {exit_code}{suffix}"),
        )
        .source(source.public())
        .retryable(entry.retryable));
    }
    let text = if entry.kind == "command" {
        entry.stdout.clone().unwrap_or_default()
    } else {
        entry.content.clone().unwrap_or_default()
    };
    let observed_at = DateTime::parse_from_rfc3339(&entry.observed_at)
        .map_err(|_| {
            CollectionError::new(
                "fixture_invalid",
                "Fixture source has an invalid observed_at",
            )
            .source(source.public())
        })?
        .with_timezone(&Utc);
    Ok(Observation {
        source: source.public(),
        text,
        observed_at,
        fixture: true,
    })
}

fn bounded(value: &str) -> String {
    if value.chars().count() <= ERROR_DETAIL_LIMIT {
        return value.to_owned();
    }
    value
        .chars()
        .take(ERROR_DETAIL_LIMIT - 3)
        .collect::<String>()
        + "..."
}
