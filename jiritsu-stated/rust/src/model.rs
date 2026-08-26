use std::collections::BTreeMap;
use std::path::PathBuf;

use chrono::{DateTime, SecondsFormat, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const SCHEMA_VERSION: &str = "1.0";
pub const MAX_REQUEST_BYTES: u64 = 64 * 1024;
pub const MAX_RESPONSE_BYTES: u64 = 16 * 1024 * 1024;

pub fn timestamp(value: DateTime<Utc>) -> String {
    value.to_rfc3339_opts(SecondsFormat::AutoSi, true)
}

#[derive(Clone, Debug)]
pub enum SourceLocator {
    Command(&'static [&'static str]),
    File(&'static str),
}

#[derive(Clone, Debug)]
pub struct SourceSpec {
    pub id: &'static str,
    pub locator: SourceLocator,
}

impl SourceSpec {
    pub fn public(&self) -> Source {
        match &self.locator {
            SourceLocator::Command(arguments) => Source {
                id: self.id.to_owned(),
                kind: "command".to_owned(),
                locator: arguments.join(" "),
            },
            SourceLocator::File(path) => Source {
                id: self.id.to_owned(),
                kind: "file".to_owned(),
                locator: (*path).to_owned(),
            },
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Source {
    pub id: String,
    pub kind: String,
    pub locator: String,
}

#[derive(Clone, Debug)]
pub struct Observation {
    pub source: Source,
    pub text: String,
    pub observed_at: DateTime<Utc>,
    pub fixture: bool,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct FactRecord {
    pub value: Value,
    pub source: Source,
    pub observed_at: String,
    pub age_seconds: f64,
    pub fixture: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CollectionError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fact_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<Box<Source>>,
}

impl CollectionError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            retryable: false,
            fact_id: None,
            source: None,
        }
    }

    pub fn source(mut self, source: Source) -> Self {
        self.source = Some(Box::new(source));
        self
    }

    pub fn fact(mut self, fact_id: impl Into<String>) -> Self {
        self.fact_id = Some(fact_id.into());
        self
    }

    pub fn retryable(mut self, retryable: bool) -> Self {
        self.retryable = retryable;
        self
    }
}

impl std::fmt::Display for CollectionError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.message)
    }
}

impl std::error::Error for CollectionError {}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ProviderError {
    pub code: String,
    pub provider: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    pub message: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CacheMetadata {
    pub epoch: u64,
    pub refreshed_at: String,
    pub last_refresh_at: String,
    #[serde(default)]
    pub last_refresh_errors: Vec<CollectionError>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RuntimeInfo {
    pub selected_provider: String,
    pub source: String,
    #[serde(default)]
    pub fallback_errors: Vec<ProviderError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cache: Option<CacheMetadata>,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct QueryEcho {
    #[serde(default)]
    pub selectors: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct QueryResponse {
    pub schema_version: String,
    pub status: String,
    pub collected_at: String,
    pub query: QueryEcho,
    pub facts: BTreeMap<String, FactRecord>,
    #[serde(default)]
    pub errors: Vec<CollectionError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime: Option<RuntimeInfo>,
}

impl QueryResponse {
    pub fn error(error: CollectionError, selectors: Vec<String>, runtime: RuntimeInfo) -> Self {
        Self {
            schema_version: SCHEMA_VERSION.to_owned(),
            status: "error".to_owned(),
            collected_at: timestamp(Utc::now()),
            query: QueryEcho { selectors },
            facts: BTreeMap::new(),
            errors: vec![error],
            runtime: Some(runtime),
        }
    }
}

#[derive(Clone, Debug)]
pub enum ProviderConfig {
    Live,
    Fixture(PathBuf),
}

impl ProviderConfig {
    pub fn name(&self) -> &'static str {
        match self {
            Self::Live => "direct",
            Self::Fixture(_) => "fixture",
        }
    }

    pub fn source(&self) -> String {
        match self {
            Self::Live => "Omarchy and standard Linux".to_owned(),
            Self::Fixture(path) => path.display().to_string(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct CachedFact {
    pub value: Value,
    pub source: Source,
    pub observed_at: DateTime<Utc>,
    pub fixture: bool,
}

impl CachedFact {
    pub fn semantic_eq(&self, other: &Self) -> bool {
        self.value == other.value && self.source == other.source && self.fixture == other.fixture
    }

    pub fn public(&self, collected_at: DateTime<Utc>) -> FactRecord {
        let age_milliseconds = collected_at
            .signed_duration_since(self.observed_at)
            .num_milliseconds()
            .max(0);
        FactRecord {
            value: self.value.clone(),
            source: self.source.clone(),
            observed_at: timestamp(self.observed_at),
            age_seconds: age_milliseconds as f64 / 1000.0,
            fixture: self.fixture,
        }
    }
}

#[derive(Clone, Debug)]
pub struct FactOutcome {
    pub fact_id: &'static str,
    pub result: Result<CachedFact, CollectionError>,
}
