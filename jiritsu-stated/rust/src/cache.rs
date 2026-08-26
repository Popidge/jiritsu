use std::collections::{BTreeMap, BTreeSet};

use chrono::{DateTime, Utc};

use crate::model::{
    CacheMetadata, CachedFact, CollectionError, FactOutcome, QueryEcho, QueryResponse, RuntimeInfo,
    SCHEMA_VERSION, timestamp,
};

#[derive(Clone, Debug)]
pub struct StateCache {
    facts: BTreeMap<String, CachedFact>,
    errors: BTreeMap<String, CollectionError>,
    epoch: u64,
    refreshed_at: DateTime<Utc>,
    last_refresh_at: DateTime<Utc>,
    last_refresh_errors: Vec<CollectionError>,
    socket_source: String,
}

impl StateCache {
    pub fn new(socket_source: String) -> Self {
        let now = Utc::now();
        Self {
            facts: BTreeMap::new(),
            errors: BTreeMap::new(),
            epoch: 0,
            refreshed_at: now,
            last_refresh_at: now,
            last_refresh_errors: Vec::new(),
            socket_source,
        }
    }

    pub fn apply(&mut self, outcomes: Vec<FactOutcome>) -> bool {
        let mut changed = false;
        let mut successful_refresh = false;
        let mut refresh_errors = Vec::new();
        for outcome in outcomes {
            match outcome.result {
                Ok(fact) => {
                    successful_refresh = true;
                    changed |= self
                        .facts
                        .get(outcome.fact_id)
                        .is_none_or(|previous| !previous.semantic_eq(&fact));
                    changed |= self.errors.remove(outcome.fact_id).is_some();
                    self.facts.insert(outcome.fact_id.to_owned(), fact);
                }
                Err(error) => {
                    refresh_errors.push(error.clone());
                    if !self.facts.contains_key(outcome.fact_id) {
                        changed |= self
                            .errors
                            .get(outcome.fact_id)
                            .is_none_or(|previous| previous != &error);
                        self.errors.insert(outcome.fact_id.to_owned(), error);
                    }
                }
            }
        }
        let now = Utc::now();
        self.last_refresh_at = now;
        if successful_refresh {
            self.refreshed_at = now;
        }
        self.last_refresh_errors = refresh_errors;
        if changed {
            self.epoch = self.epoch.saturating_add(1);
        }
        changed
    }

    pub fn record_provider_error(
        &mut self,
        fact_ids: &[&'static str],
        error: CollectionError,
    ) -> bool {
        let mut changed = false;
        let mut refresh_errors = Vec::new();
        for fact_id in fact_ids {
            let fact_error = error.clone().fact(*fact_id);
            refresh_errors.push(fact_error.clone());
            if !self.facts.contains_key(*fact_id) {
                changed |= self
                    .errors
                    .get(*fact_id)
                    .is_none_or(|previous| previous != &fact_error);
                self.errors.insert((*fact_id).to_owned(), fact_error);
            }
        }
        self.last_refresh_at = Utc::now();
        self.last_refresh_errors = refresh_errors;
        if changed {
            self.epoch = self.epoch.saturating_add(1);
        }
        changed
    }

    pub fn query(&self, selectors: Vec<String>, fact_ids: &[&'static str]) -> QueryResponse {
        let collected_at = Utc::now();
        let requested: BTreeSet<&str> = fact_ids.iter().copied().collect();
        let facts = self
            .facts
            .iter()
            .filter(|(fact_id, _)| requested.contains(fact_id.as_str()))
            .map(|(fact_id, fact)| (fact_id.clone(), fact.public(collected_at)))
            .collect::<BTreeMap<_, _>>();
        let errors = self
            .errors
            .iter()
            .filter(|(fact_id, _)| requested.contains(fact_id.as_str()))
            .map(|(_, error)| error.clone())
            .collect::<Vec<_>>();
        let status = if errors.is_empty() {
            "ok"
        } else if facts.is_empty() {
            "error"
        } else {
            "partial"
        };
        QueryResponse {
            schema_version: SCHEMA_VERSION.to_owned(),
            status: status.to_owned(),
            collected_at: timestamp(collected_at),
            query: QueryEcho { selectors },
            facts,
            errors,
            runtime: Some(RuntimeInfo {
                selected_provider: "daemon".to_owned(),
                source: self.socket_source.clone(),
                fallback_errors: Vec::new(),
                cache: Some(CacheMetadata {
                    epoch: self.epoch,
                    refreshed_at: timestamp(self.refreshed_at),
                    last_refresh_at: timestamp(self.last_refresh_at),
                    last_refresh_errors: self.last_refresh_errors.clone(),
                }),
            }),
        }
    }

    pub fn epoch(&self) -> u64 {
        self.epoch
    }
}

#[cfg(test)]
mod tests {
    use chrono::{Duration, Utc};
    use serde_json::json;

    use super::StateCache;
    use crate::model::{CachedFact, CollectionError, FactOutcome, Source};

    fn fact(value: i64, observed_at: chrono::DateTime<Utc>) -> CachedFact {
        CachedFact {
            value: json!(value),
            source: Source {
                id: "test.source".to_owned(),
                kind: "file".to_owned(),
                locator: "/test".to_owned(),
            },
            observed_at,
            fixture: true,
        }
    }

    #[test]
    fn epoch_changes_only_for_semantic_state_changes() {
        let now = Utc::now();
        let mut cache = StateCache::new("/test/stated.sock".to_owned());

        assert!(cache.apply(vec![FactOutcome {
            fact_id: "test.fact",
            result: Ok(fact(1, now)),
        }]));
        assert_eq!(cache.epoch(), 1);

        assert!(!cache.apply(vec![FactOutcome {
            fact_id: "test.fact",
            result: Ok(fact(1, now + Duration::seconds(1))),
        }]));
        assert_eq!(cache.epoch(), 1);

        assert!(cache.apply(vec![FactOutcome {
            fact_id: "test.fact",
            result: Ok(fact(2, now + Duration::seconds(2))),
        }]));
        assert_eq!(cache.epoch(), 2);

        assert!(!cache.apply(vec![FactOutcome {
            fact_id: "test.fact",
            result: Err(CollectionError::new("source_failed", "temporary error").fact("test.fact")),
        }]));
        assert_eq!(cache.epoch(), 2);
        let response = cache.query(vec!["test.fact".to_owned()], &["test.fact"]);
        assert_eq!(response.status, "ok");
        assert_eq!(response.facts["test.fact"].value, json!(2));
        assert_eq!(
            response.runtime.unwrap().cache.unwrap().last_refresh_errors[0].code,
            "source_failed"
        );
    }
}
