use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;

use crate::catalog::FactSpec;
use crate::model::{CachedFact, CollectionError, FactOutcome, ProviderConfig};
use crate::provider::SourceProvider;

pub async fn collect(
    configuration: &ProviderConfig,
    specifications: &[&'static FactSpec],
    timeout: Duration,
) -> Result<Vec<FactOutcome>, CollectionError> {
    let provider = Arc::new(SourceProvider::load(configuration, timeout).await?);
    let mut tasks = Vec::with_capacity(specifications.len());
    for specification in specifications {
        let provider = Arc::clone(&provider);
        let specification = *specification;
        tasks.push(tokio::spawn(async move {
            let result = match provider.observe(&specification.source).await {
                Ok(observation) => match (specification.parser)(&observation.text) {
                    Ok(value) => Ok(CachedFact {
                        value,
                        source: observation.source,
                        observed_at: observation.observed_at,
                        fixture: observation.fixture,
                    }),
                    Err(error) => Err(CollectionError::new(
                        "parse_error",
                        format!(
                            "Source payload does not match the {} contract: {error}",
                            specification.id
                        ),
                    )
                    .source(specification.source.public())
                    .fact(specification.id)),
                },
                Err(error) => Err(error.fact(specification.id)),
            };
            FactOutcome {
                fact_id: specification.id,
                result,
            }
        }));
    }

    let mut outcomes = Vec::with_capacity(tasks.len());
    for (specification, task) in specifications.iter().zip(tasks) {
        match task.await {
            Ok(outcome) => outcomes.push(outcome),
            Err(error) => outcomes.push(FactOutcome {
                fact_id: specification.id,
                result: Err(CollectionError::new(
                    "collector_failed",
                    format!("Collector task stopped unexpectedly: {error}"),
                )
                .source(specification.source.public())
                .fact(specification.id)
                .retryable(true)),
            }),
        }
    }
    Ok(outcomes)
}

pub async fn direct_response(
    configuration: &ProviderConfig,
    specifications: &[&'static FactSpec],
    selectors: Vec<String>,
    timeout: Duration,
    fallback_errors: Vec<crate::model::ProviderError>,
) -> Result<crate::model::QueryResponse, CollectionError> {
    let outcomes = collect(configuration, specifications, timeout).await?;
    let collected_at = Utc::now();
    let mut facts = std::collections::BTreeMap::new();
    let mut errors = Vec::new();
    for outcome in outcomes {
        match outcome.result {
            Ok(fact) => {
                facts.insert(outcome.fact_id.to_owned(), fact.public(collected_at));
            }
            Err(error) => errors.push(error),
        }
    }
    let status = if errors.is_empty() {
        "ok"
    } else if facts.is_empty() {
        "error"
    } else {
        "partial"
    };
    Ok(crate::model::QueryResponse {
        schema_version: crate::model::SCHEMA_VERSION.to_owned(),
        status: status.to_owned(),
        collected_at: crate::model::timestamp(collected_at),
        query: crate::model::QueryEcho { selectors },
        facts,
        errors,
        runtime: Some(crate::model::RuntimeInfo {
            selected_provider: configuration.name().to_owned(),
            source: configuration.source(),
            fallback_errors,
            cache: None,
        }),
    })
}
