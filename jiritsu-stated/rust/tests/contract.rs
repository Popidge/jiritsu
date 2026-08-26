use std::path::PathBuf;
use std::process::{Command, Output};

use serde_json::Value;

fn module_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn fixture() -> PathBuf {
    module_root().join("tests/fixtures/healthy.json")
}

fn rust_command(arguments: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_jiritsu-stated"))
        .args(arguments)
        .output()
        .expect("run Rust command")
}

fn python_command(arguments: &[&str]) -> Output {
    Command::new(module_root().join("bin/jiritsu-stated-python"))
        .args(arguments)
        .output()
        .expect("run Python reference command")
}

fn parse(output: &Output) -> Value {
    serde_json::from_slice(&output.stdout).unwrap_or_else(|error| {
        panic!(
            "invalid JSON: {error}; stderr={}",
            String::from_utf8_lossy(&output.stderr)
        )
    })
}

fn normalized_query(mut value: Value) -> Value {
    let object = value.as_object_mut().expect("query response object");
    object.remove("collected_at");
    object.remove("runtime");
    for fact in object["facts"]
        .as_object_mut()
        .expect("facts object")
        .values_mut()
    {
        fact.as_object_mut()
            .expect("fact object")
            .remove("age_seconds");
    }
    value
}

#[test]
fn rust_and_python_match_for_the_complete_fixture() {
    let fixture = fixture();
    let fixture = fixture.to_str().expect("fixture path");
    let rust = rust_command(&["query", "--fixture", fixture]);
    let python = python_command(&["query", "--fixture", fixture]);

    assert!(
        rust.status.success(),
        "{}",
        String::from_utf8_lossy(&rust.stderr)
    );
    assert!(
        python.status.success(),
        "{}",
        String::from_utf8_lossy(&python.stderr)
    );
    assert_eq!(
        normalized_query(parse(&rust)),
        normalized_query(parse(&python))
    );
}

#[test]
fn rust_and_python_catalogs_match() {
    let rust = rust_command(&["catalog"]);
    let python = python_command(&["catalog"]);

    assert!(rust.status.success());
    assert!(python.status.success());
    assert_eq!(parse(&rust), parse(&python));
}

#[test]
fn missing_daemon_uses_direct_collection_and_reports_the_fallback() {
    let directory = tempfile::TempDir::new().expect("temporary directory");
    let missing = directory.path().join("missing.sock");
    let output = rust_command(&[
        "query",
        "system.hostname",
        "--socket",
        missing.to_str().expect("socket path"),
    ]);
    let response = parse(&output);

    assert!(output.status.success());
    assert_eq!(response["status"], "ok");
    assert_eq!(response["runtime"]["selected_provider"], "direct");
    assert_eq!(
        response["runtime"]["fallback_errors"][0]["code"],
        "daemon_unavailable"
    );
    assert!(response["facts"]["system.hostname"]["value"].is_string());
}

#[test]
fn required_missing_daemon_returns_a_structured_error() {
    let directory = tempfile::TempDir::new().expect("temporary directory");
    let missing = directory.path().join("missing.sock");
    let output = rust_command(&[
        "query",
        "system.hostname",
        "--socket",
        missing.to_str().expect("socket path"),
        "--require-daemon",
    ]);
    let response = parse(&output);

    assert_eq!(output.status.code(), Some(1));
    assert_eq!(response["status"], "error");
    assert_eq!(response["runtime"]["selected_provider"], "none");
    assert_eq!(response["errors"][0]["code"], "daemon_unavailable");
}
