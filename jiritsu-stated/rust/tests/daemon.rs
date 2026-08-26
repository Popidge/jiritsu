use std::fs;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output};
use std::thread::sleep;
use std::time::{Duration, Instant};

use serde_json::Value;
use tempfile::TempDir;

struct Daemon {
    child: Child,
    socket: PathBuf,
}

impl Drop for Daemon {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = Command::new("kill")
                .args(["-TERM", &self.child.id().to_string()])
                .status();
            let deadline = Instant::now() + Duration::from_secs(3);
            while Instant::now() < deadline {
                if self.child.try_wait().ok().flatten().is_some() {
                    return;
                }
                sleep(Duration::from_millis(25));
            }
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

fn module_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_jiritsu-stated")
}

fn run(arguments: &[&str]) -> Output {
    Command::new(binary())
        .args(arguments)
        .output()
        .expect("run stated")
}

fn parse(output: &Output) -> Value {
    serde_json::from_slice(&output.stdout).unwrap_or_else(|error| {
        panic!(
            "invalid JSON: {error}; stderr={}",
            String::from_utf8_lossy(&output.stderr)
        )
    })
}

fn start_daemon(directory: &TempDir) -> (Daemon, PathBuf) {
    let fixture = directory.path().join("state.json");
    fs::copy(module_root().join("tests/fixtures/healthy.json"), &fixture).expect("copy fixture");
    let socket = directory.path().join("stated.sock");
    let child = Command::new(binary())
        .args([
            "serve",
            "--socket",
            socket.to_str().expect("socket path"),
            "--fixture",
            fixture.to_str().expect("fixture path"),
            "--dynamic-refresh-seconds",
            "3600",
            "--full-refresh-seconds",
            "3600",
        ])
        .spawn()
        .expect("start daemon");
    let daemon = Daemon {
        child,
        socket: socket.clone(),
    };
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if socket.exists() {
            let output = query(&socket, &["system.hostname"]);
            if output.status.success() {
                return (daemon, fixture);
            }
        }
        sleep(Duration::from_millis(25));
    }
    panic!("daemon did not become ready");
}

fn query(socket: &Path, selectors: &[&str]) -> Output {
    let mut arguments = vec!["query"];
    arguments.extend_from_slice(selectors);
    arguments.extend_from_slice(&[
        "--socket",
        socket.to_str().expect("socket path"),
        "--require-daemon",
    ]);
    run(&arguments)
}

#[test]
fn daemon_serves_cached_facts_and_reloads_fixture_events() {
    let directory = TempDir::new().expect("temporary directory");
    let (daemon, fixture) = start_daemon(&directory);

    let first = parse(&query(&daemon.socket, &["system.hostname"]));
    assert_eq!(first["status"], "ok");
    assert_eq!(first["facts"]["system.hostname"]["value"], "test-machine");
    assert_eq!(first["runtime"]["selected_provider"], "daemon");
    assert_eq!(first["runtime"]["cache"]["epoch"], 1);

    let mut payload: Value =
        serde_json::from_slice(&fs::read(&fixture).expect("read fixture")).expect("fixture JSON");
    fs::write(&fixture, b"{").expect("write invalid fixture");
    let error_deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let response = parse(&query(&daemon.socket, &["system.hostname"]));
        if response["runtime"]["cache"]["last_refresh_errors"]
            .as_array()
            .is_some_and(|errors| !errors.is_empty())
        {
            assert_eq!(response["status"], "ok");
            assert_eq!(
                response["facts"]["system.hostname"]["value"],
                "test-machine"
            );
            assert_eq!(response["runtime"]["cache"]["epoch"], 1);
            assert_eq!(
                response["runtime"]["cache"]["last_refresh_errors"][0]["code"],
                "fixture_invalid"
            );
            break;
        }
        assert!(
            Instant::now() < error_deadline,
            "invalid fixture error was not reported"
        );
        sleep(Duration::from_millis(50));
    }

    payload["sources"]["system.hostname"]["content"] = Value::String("event-host\n".to_owned());
    fs::write(
        &fixture,
        serde_json::to_vec_pretty(&payload).expect("encode fixture"),
    )
    .expect("write fixture");

    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let response = parse(&query(&daemon.socket, &["system.hostname"]));
        if response["facts"]["system.hostname"]["value"] == "event-host" {
            assert_eq!(response["runtime"]["cache"]["epoch"], 2);
            assert_eq!(
                response["runtime"]["cache"]["last_refresh_errors"],
                serde_json::json!([])
            );
            break;
        }
        assert!(Instant::now() < deadline, "fixture event was not collected");
        sleep(Duration::from_millis(50));
    }
}

#[test]
fn malformed_and_oversized_requests_do_not_stop_the_daemon() {
    let directory = TempDir::new().expect("temporary directory");
    let (daemon, _) = start_daemon(&directory);

    let malformed = raw_request(&daemon.socket, b"{");
    assert_eq!(malformed["errors"][0]["code"], "request_invalid");

    let oversized = raw_request(&daemon.socket, &vec![b'x'; 64 * 1024 + 1]);
    assert_eq!(oversized["errors"][0]["code"], "request_too_large");

    let response = parse(&query(&daemon.socket, &["system.hostname"]));
    assert_eq!(response["status"], "ok");
}

#[test]
fn daemon_refuses_unsafe_or_active_socket_paths() {
    let directory = TempDir::new().expect("temporary directory");
    let unsafe_path = directory.path().join("unsafe.sock");
    fs::write(&unsafe_path, "keep this file").expect("write protected file");
    let fixture = module_root().join("tests/fixtures/healthy.json");
    let unsafe_output = run(&[
        "serve",
        "--socket",
        unsafe_path.to_str().expect("socket path"),
        "--fixture",
        fixture.to_str().expect("fixture path"),
    ]);
    assert_eq!(unsafe_output.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&unsafe_output.stderr).contains("socket_path_unsafe"));
    assert_eq!(
        fs::read_to_string(&unsafe_path).expect("read protected file"),
        "keep this file"
    );

    let (daemon, fixture) = start_daemon(&directory);
    let active_output = run(&[
        "serve",
        "--socket",
        daemon.socket.to_str().expect("socket path"),
        "--fixture",
        fixture.to_str().expect("fixture path"),
    ]);
    assert_eq!(active_output.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&active_output.stderr).contains("daemon_already_running"));

    let response = parse(&query(&daemon.socket, &["system.hostname"]));
    assert_eq!(response["status"], "ok");
}

fn raw_request(socket: &Path, payload: &[u8]) -> Value {
    let mut stream = UnixStream::connect(socket).expect("connect socket");
    stream.write_all(payload).expect("write request");
    stream
        .shutdown(std::net::Shutdown::Write)
        .expect("finish request");
    let mut response = Vec::new();
    stream.read_to_end(&mut response).expect("read response");
    serde_json::from_slice(&response).expect("response JSON")
}
