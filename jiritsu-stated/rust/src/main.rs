use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use clap::{Args, Parser, Subcommand};
use jiritsu_stated_rs::catalog::{catalog, select_facts};
use jiritsu_stated_rs::collector::direct_response;
use jiritsu_stated_rs::daemon::{ServeOptions, serve};
use jiritsu_stated_rs::model::{
    CollectionError, ProviderConfig, ProviderError, QueryResponse, RuntimeInfo, SCHEMA_VERSION,
};
use jiritsu_stated_rs::protocol::query_daemon;
use serde_json::json;

const DEFAULT_SOCKET: &str = "/run/jiritsu/stated.sock";

#[derive(Debug, Parser)]
#[command(name = "jiritsu-stated", version, about = "Read cached machine state")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Query selected machine facts.
    Query(QueryArguments),
    /// List available facts without machine probes.
    Catalog(OutputArguments),
    /// Run the long-lived state daemon.
    Serve(ServeArguments),
}

#[derive(Debug, Args)]
struct OutputArguments {
    /// Indent the JSON response.
    #[arg(long)]
    pretty: bool,
}

#[derive(Debug, Args)]
struct QueryArguments {
    /// Exact fact IDs or group names. Omit these values to select all facts.
    selectors: Vec<String>,

    /// Replay source payloads from this JSON file.
    #[arg(long)]
    fixture: Option<PathBuf>,

    /// Read sources directly and do not connect to the daemon.
    #[arg(long, conflicts_with = "require_daemon")]
    direct: bool,

    /// Return an error when the daemon is unavailable.
    #[arg(long, conflicts_with_all = ["direct", "fixture"])]
    require_daemon: bool,

    /// Unix socket for the daemon.
    #[arg(long, env = "JIRITSU_STATED_SOCKET", default_value = DEFAULT_SOCKET)]
    socket: PathBuf,

    /// Timeout for a daemon request or each direct source.
    #[arg(long, default_value_t = 5.0)]
    timeout: f64,

    /// Indent the JSON response.
    #[arg(long)]
    pretty: bool,
}

#[derive(Debug, Args)]
struct ServeArguments {
    /// Unix socket for the daemon.
    #[arg(long, env = "JIRITSU_STATED_SOCKET", default_value = DEFAULT_SOCKET)]
    socket: PathBuf,

    /// Replay this fixture as the daemon source.
    #[arg(long)]
    fixture: Option<PathBuf>,

    /// Mode for the created socket.
    #[arg(long, default_value = "0660", value_parser = parse_mode)]
    socket_mode: u32,

    /// Timeout for each source command.
    #[arg(long, default_value_t = 5.0)]
    source_timeout: f64,

    /// Timeout for one socket request.
    #[arg(long, default_value_t = 2.0)]
    request_timeout: f64,

    /// Refresh period for dynamic facts.
    #[arg(long, default_value_t = 15.0)]
    dynamic_refresh_seconds: f64,

    /// Safety refresh period for all facts.
    #[arg(long, default_value_t = 300.0)]
    full_refresh_seconds: f64,

    /// Disable filesystem event watches.
    #[arg(long)]
    no_watch: bool,
}

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    match cli.command {
        Command::Catalog(arguments) => {
            emit(
                &json!({"schema_version": SCHEMA_VERSION, "facts": catalog()}),
                arguments.pretty,
            );
            ExitCode::SUCCESS
        }
        Command::Query(arguments) => query(arguments).await,
        Command::Serve(arguments) => run_daemon(arguments).await,
    }
}

async fn query(arguments: QueryArguments) -> ExitCode {
    let timeout_duration = match positive_duration(arguments.timeout, "--timeout") {
        Ok(duration) => duration,
        Err(error) => {
            return emit_request_error(error, arguments.selectors, arguments.pretty, 64);
        }
    };
    let specifications = match select_facts(&arguments.selectors) {
        Ok(specifications) => specifications,
        Err(error) => {
            return emit_request_error(error, arguments.selectors, arguments.pretty, 64);
        }
    };
    let mut fallback_errors = Vec::new();
    if arguments.fixture.is_none() && !arguments.direct {
        match query_daemon(
            &arguments.socket,
            arguments.selectors.clone(),
            timeout_duration,
        )
        .await
        {
            Ok(response) => {
                let exit = status_exit(&response.status);
                emit(&response, arguments.pretty);
                return ExitCode::from(exit);
            }
            Err(error) if arguments.require_daemon => {
                let response = QueryResponse::error(
                    CollectionError::new(error.code.clone(), error.message.clone()).retryable(true),
                    arguments.selectors,
                    RuntimeInfo {
                        selected_provider: "none".to_owned(),
                        source: arguments.socket.display().to_string(),
                        fallback_errors: vec![error],
                        cache: None,
                    },
                );
                emit(&response, arguments.pretty);
                return ExitCode::from(1);
            }
            Err(error) => fallback_errors.push(error),
        }
    }
    let provider = arguments
        .fixture
        .map_or(ProviderConfig::Live, ProviderConfig::Fixture);
    match direct_response(
        &provider,
        &specifications,
        arguments.selectors.clone(),
        timeout_duration,
        fallback_errors,
    )
    .await
    {
        Ok(response) => {
            let exit = status_exit(&response.status);
            emit(&response, arguments.pretty);
            ExitCode::from(exit)
        }
        Err(error) => {
            let exit = if error.code.starts_with("fixture_") {
                65
            } else {
                1
            };
            emit_request_error(error, arguments.selectors, arguments.pretty, exit)
        }
    }
}

async fn run_daemon(arguments: ServeArguments) -> ExitCode {
    let source_timeout = match positive_duration(arguments.source_timeout, "--source-timeout") {
        Ok(duration) => duration,
        Err(error) => return daemon_argument_error(error),
    };
    let request_timeout = match positive_duration(arguments.request_timeout, "--request-timeout") {
        Ok(duration) => duration,
        Err(error) => return daemon_argument_error(error),
    };
    let dynamic_refresh = match positive_duration(
        arguments.dynamic_refresh_seconds,
        "--dynamic-refresh-seconds",
    ) {
        Ok(duration) => duration,
        Err(error) => return daemon_argument_error(error),
    };
    let full_refresh =
        match positive_duration(arguments.full_refresh_seconds, "--full-refresh-seconds") {
            Ok(duration) => duration,
            Err(error) => return daemon_argument_error(error),
        };
    let provider = arguments
        .fixture
        .map_or(ProviderConfig::Live, ProviderConfig::Fixture);
    let options = ServeOptions {
        socket: arguments.socket,
        socket_mode: arguments.socket_mode,
        provider,
        source_timeout,
        request_timeout,
        dynamic_refresh,
        full_refresh,
        watch: !arguments.no_watch,
    };
    match serve(options).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("jiritsu-stated: {}: {}", error.code, error.message);
            ExitCode::from(if error.code == "invalid_request" {
                64
            } else {
                1
            })
        }
    }
}

fn emit_request_error(
    error: CollectionError,
    selectors: Vec<String>,
    pretty: bool,
    exit: u8,
) -> ExitCode {
    let runtime = RuntimeInfo {
        selected_provider: "none".to_owned(),
        source: "request".to_owned(),
        fallback_errors: Vec::<ProviderError>::new(),
        cache: None,
    };
    emit(&QueryResponse::error(error, selectors, runtime), pretty);
    ExitCode::from(exit)
}

fn emit<T: serde::Serialize>(value: &T, pretty: bool) {
    let result = if pretty {
        serde_json::to_writer_pretty(std::io::stdout().lock(), value)
    } else {
        serde_json::to_writer(std::io::stdout().lock(), value)
    };
    if let Err(error) = result {
        eprintln!("jiritsu-stated: cannot write JSON: {error}");
    } else {
        println!();
    }
}

fn status_exit(status: &str) -> u8 {
    match status {
        "ok" => 0,
        "partial" => 2,
        _ => 1,
    }
}

fn parse_mode(value: &str) -> Result<u32, String> {
    let mode = u32::from_str_radix(value, 8)
        .map_err(|_| "the socket mode must be an octal value".to_owned())?;
    if mode > 0o777 {
        Err("the socket mode must be between 0000 and 0777".to_owned())
    } else {
        Ok(mode)
    }
}

fn positive_duration(value: f64, name: &str) -> Result<Duration, CollectionError> {
    let duration = Duration::try_from_secs_f64(value).map_err(|_| {
        CollectionError::new(
            "invalid_request",
            format!("{name} must be a finite number of seconds"),
        )
    })?;
    if duration.is_zero() {
        Err(CollectionError::new(
            "invalid_request",
            format!("{name} must be greater than zero"),
        ))
    } else {
        Ok(duration)
    }
}

fn daemon_argument_error(error: CollectionError) -> ExitCode {
    eprintln!("jiritsu-stated: {}: {}", error.code, error.message);
    ExitCode::from(64)
}
