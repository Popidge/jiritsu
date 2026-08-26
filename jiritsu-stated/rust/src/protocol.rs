use std::path::Path;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixStream;
use tokio::time::timeout;

use crate::model::{
    MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, ProviderError, QueryResponse, SCHEMA_VERSION,
};

pub const OPERATION_QUERY: &str = "query";

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub schema_version: String,
    pub operation: String,
    #[serde(default)]
    pub selectors: Vec<String>,
}

impl Request {
    pub fn query(selectors: Vec<String>) -> Self {
        Self {
            schema_version: SCHEMA_VERSION.to_owned(),
            operation: OPERATION_QUERY.to_owned(),
            selectors,
        }
    }
}

pub async fn query_daemon(
    socket: &Path,
    selectors: Vec<String>,
    timeout_duration: Duration,
) -> Result<QueryResponse, ProviderError> {
    let source = socket.display().to_string();
    let mut stream = match timeout(timeout_duration, UnixStream::connect(socket)).await {
        Ok(Ok(stream)) => stream,
        Ok(Err(error)) => {
            return Err(provider_error(
                "daemon_unavailable",
                &source,
                format!("Cannot connect to the stated daemon: {error}"),
            ));
        }
        Err(_) => {
            return Err(provider_error(
                "daemon_timeout",
                &source,
                format!(
                    "The stated daemon did not accept a connection within {} seconds",
                    timeout_duration.as_secs_f64()
                ),
            ));
        }
    };
    let request = serde_json::to_vec(&Request::query(selectors)).map_err(|error| {
        provider_error(
            "daemon_request_invalid",
            &source,
            format!("Cannot encode the daemon request: {error}"),
        )
    })?;
    if request.len() as u64 > MAX_REQUEST_BYTES {
        return Err(provider_error(
            "daemon_request_too_large",
            &source,
            "The daemon request exceeds the 64 KiB limit".to_owned(),
        ));
    }
    timeout(timeout_duration, stream.write_all(&request))
        .await
        .map_err(|_| {
            provider_error(
                "daemon_timeout",
                &source,
                "The stated daemon did not read the request before the timeout".to_owned(),
            )
        })?
        .map_err(|error| {
            provider_error(
                "daemon_failed",
                &source,
                format!("Cannot write the daemon request: {error}"),
            )
        })?;
    stream.shutdown().await.map_err(|error| {
        provider_error(
            "daemon_failed",
            &source,
            format!("Cannot finish the daemon request: {error}"),
        )
    })?;
    let mut response = Vec::new();
    let read_result = timeout(
        timeout_duration,
        stream
            .take(MAX_RESPONSE_BYTES + 1)
            .read_to_end(&mut response),
    )
    .await
    .map_err(|_| {
        provider_error(
            "daemon_timeout",
            &source,
            "The stated daemon did not return a response before the timeout".to_owned(),
        )
    })?;
    read_result.map_err(|error| {
        provider_error(
            "daemon_failed",
            &source,
            format!("Cannot read the daemon response: {error}"),
        )
    })?;
    if response.len() as u64 > MAX_RESPONSE_BYTES {
        return Err(provider_error(
            "daemon_response_too_large",
            &source,
            "The daemon response exceeds the 16 MiB limit".to_owned(),
        ));
    }
    let response: QueryResponse = serde_json::from_slice(&response).map_err(|error| {
        provider_error(
            "daemon_response_invalid",
            &source,
            format!("The daemon returned invalid JSON: {error}"),
        )
    })?;
    if response.schema_version != SCHEMA_VERSION {
        return Err(provider_error(
            "daemon_response_invalid",
            &source,
            format!("The daemon response does not use schema_version \"{SCHEMA_VERSION}\""),
        ));
    }
    Ok(response)
}

fn provider_error(code: &str, source: &str, message: String) -> ProviderError {
    ProviderError {
        code: code.to_owned(),
        provider: "daemon".to_owned(),
        source: Some(source.to_owned()),
        message,
    }
}
