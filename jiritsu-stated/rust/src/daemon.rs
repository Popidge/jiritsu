use std::fs::Permissions;
use std::os::unix::fs::{FileTypeExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use notify::{RecommendedWatcher, RecursiveMode, Watcher};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::{RwLock, Semaphore, mpsc};
use tokio::time::{MissedTickBehavior, interval, sleep, timeout};

use crate::cache::StateCache;
use crate::catalog::{FACTS, FactSpec, RefreshClass, facts_for_class, select_facts};
use crate::collector::collect;
use crate::model::{
    CollectionError, MAX_REQUEST_BYTES, ProviderConfig, QueryResponse, RuntimeInfo, SCHEMA_VERSION,
};
use crate::protocol::{OPERATION_QUERY, Request};

const CONNECTION_LIMIT: usize = 64;
const EVENT_DEBOUNCE: Duration = Duration::from_millis(150);

#[derive(Clone, Debug)]
pub struct ServeOptions {
    pub socket: PathBuf,
    pub socket_mode: u32,
    pub provider: ProviderConfig,
    pub source_timeout: Duration,
    pub request_timeout: Duration,
    pub dynamic_refresh: Duration,
    pub full_refresh: Duration,
    pub watch: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RefreshRequest {
    All,
    Static,
    Dynamic,
}

pub async fn serve(options: ServeOptions) -> Result<(), CollectionError> {
    validate_options(&options)?;
    prepare_socket_path(&options.socket).await?;
    let listener = UnixListener::bind(&options.socket).map_err(|error| {
        CollectionError::new(
            "socket_bind_failed",
            format!(
                "Cannot bind the daemon socket {}: {error}",
                options.socket.display()
            ),
        )
    })?;
    if let Err(error) =
        tokio::fs::set_permissions(&options.socket, Permissions::from_mode(options.socket_mode))
            .await
    {
        drop(listener);
        remove_socket(&options.socket).await;
        return Err(CollectionError::new(
            "socket_permission_failed",
            format!(
                "Cannot set mode {:04o} on {}: {error}",
                options.socket_mode,
                options.socket.display()
            ),
        ));
    }

    let cache = Arc::new(RwLock::new(StateCache::new(
        options.socket.display().to_string(),
    )));
    refresh_once(
        &cache,
        &options.provider,
        &all_facts(),
        options.source_timeout,
    )
    .await;

    let (refresh_sender, refresh_receiver) = mpsc::channel(16);
    let refresh_options = options.clone();
    let refresh_cache = Arc::clone(&cache);
    let refresh_task = tokio::spawn(async move {
        refresh_worker(refresh_cache, refresh_options, refresh_receiver).await;
    });

    let dynamic_sender = refresh_sender.clone();
    let dynamic_period = options.dynamic_refresh;
    let dynamic_task = tokio::spawn(async move {
        let mut timer = interval(dynamic_period);
        timer.set_missed_tick_behavior(MissedTickBehavior::Skip);
        timer.tick().await;
        loop {
            timer.tick().await;
            if dynamic_sender.send(RefreshRequest::Dynamic).await.is_err() {
                break;
            }
        }
    });

    let full_sender = refresh_sender.clone();
    let full_period = options.full_refresh;
    let full_task = tokio::spawn(async move {
        let mut timer = interval(full_period);
        timer.set_missed_tick_behavior(MissedTickBehavior::Skip);
        timer.tick().await;
        loop {
            timer.tick().await;
            if full_sender.send(RefreshRequest::All).await.is_err() {
                break;
            }
        }
    });

    let _watcher = if options.watch {
        start_watcher(&options.provider, refresh_sender.clone())
    } else {
        None
    };

    let connections = Arc::new(Semaphore::new(CONNECTION_LIMIT));
    log_message(&format!(
        "ready socket={} epoch={}",
        options.socket.display(),
        cache.read().await.epoch()
    ));

    let shutdown = shutdown_signal();
    tokio::pin!(shutdown);
    let mut shutdown_error = None;
    loop {
        tokio::select! {
            result = listener.accept() => {
                match result {
                    Ok((stream, _)) => {
                        let Ok(permit) = Arc::clone(&connections).try_acquire_owned() else {
                            let runtime = daemon_runtime(&options.socket);
                            let response = QueryResponse::error(
                                CollectionError::new(
                                    "daemon_busy",
                                    "The daemon connection limit is reached",
                                ).retryable(true),
                                Vec::new(),
                                runtime,
                            );
                            tokio::spawn(write_response(stream, response));
                            continue;
                        };
                        let cache = Arc::clone(&cache);
                        let socket = options.socket.clone();
                        let request_timeout = options.request_timeout;
                        tokio::spawn(async move {
                            let _permit = permit;
                            handle_connection(stream, cache, socket, request_timeout).await;
                        });
                    }
                    Err(error) => {
                        log_message(&format!("accept error: {error}"));
                        sleep(Duration::from_millis(100)).await;
                    }
                }
            }
            result = &mut shutdown => {
                match result {
                    Ok(()) => log_message("shutdown signal received"),
                    Err(error) => shutdown_error = Some(error),
                }
                break;
            }
        }
    }

    drop(refresh_sender);
    refresh_task.abort();
    dynamic_task.abort();
    full_task.abort();
    drop(listener);
    remove_socket(&options.socket).await;
    shutdown_error.map_or(Ok(()), Err)
}

async fn handle_connection(
    stream: UnixStream,
    cache: Arc<RwLock<StateCache>>,
    socket: PathBuf,
    request_timeout: Duration,
) {
    let (reader, mut writer) = stream.into_split();
    let mut request_bytes = Vec::new();
    let read = timeout(
        request_timeout,
        reader
            .take(MAX_REQUEST_BYTES + 1)
            .read_to_end(&mut request_bytes),
    )
    .await;
    let response = match read {
        Err(_) => QueryResponse::error(
            CollectionError::new(
                "request_timeout",
                "The client did not finish the request before the timeout",
            ),
            Vec::new(),
            daemon_runtime(&socket),
        ),
        Ok(Err(error)) => QueryResponse::error(
            CollectionError::new(
                "request_read_failed",
                format!("Cannot read the daemon request: {error}"),
            ),
            Vec::new(),
            daemon_runtime(&socket),
        ),
        Ok(Ok(_)) if request_bytes.len() as u64 > MAX_REQUEST_BYTES => QueryResponse::error(
            CollectionError::new(
                "request_too_large",
                "The daemon request exceeds the 64 KiB limit",
            ),
            Vec::new(),
            daemon_runtime(&socket),
        ),
        Ok(Ok(_)) => build_response(&request_bytes, &cache, &socket).await,
    };
    if let Ok(payload) = serde_json::to_vec(&response) {
        let _ = writer.write_all(&payload).await;
        let _ = writer.shutdown().await;
    }
}

async fn build_response(
    request_bytes: &[u8],
    cache: &Arc<RwLock<StateCache>>,
    socket: &Path,
) -> QueryResponse {
    let request: Request = match serde_json::from_slice(request_bytes) {
        Ok(request) => request,
        Err(error) => {
            return QueryResponse::error(
                CollectionError::new(
                    "request_invalid",
                    format!("The daemon request is invalid JSON: {error}"),
                ),
                Vec::new(),
                daemon_runtime(socket),
            );
        }
    };
    if request.schema_version != SCHEMA_VERSION {
        return QueryResponse::error(
            CollectionError::new(
                "request_schema_unsupported",
                format!("The daemon accepts schema_version \"{SCHEMA_VERSION}\""),
            ),
            request.selectors,
            daemon_runtime(socket),
        );
    }
    if request.operation != OPERATION_QUERY {
        return QueryResponse::error(
            CollectionError::new(
                "operation_unsupported",
                format!(
                    "The daemon does not support operation {:?}",
                    request.operation
                ),
            ),
            request.selectors,
            daemon_runtime(socket),
        );
    }
    let specifications = match select_facts(&request.selectors) {
        Ok(specifications) => specifications,
        Err(error) => {
            return QueryResponse::error(error, request.selectors, daemon_runtime(socket));
        }
    };
    let fact_ids = specifications
        .iter()
        .map(|fact| fact.id)
        .collect::<Vec<_>>();
    cache.read().await.query(request.selectors, &fact_ids)
}

async fn write_response(mut stream: UnixStream, response: QueryResponse) {
    if let Ok(payload) = serde_json::to_vec(&response) {
        let _ = stream.write_all(&payload).await;
        let _ = stream.shutdown().await;
    }
}

async fn refresh_worker(
    cache: Arc<RwLock<StateCache>>,
    options: ServeOptions,
    mut receiver: mpsc::Receiver<RefreshRequest>,
) {
    while let Some(mut request) = receiver.recv().await {
        sleep(EVENT_DEBOUNCE).await;
        while let Ok(next) = receiver.try_recv() {
            request = combine_refresh(request, next);
        }
        let specifications = match request {
            RefreshRequest::All => all_facts(),
            RefreshRequest::Static => facts_for_class(RefreshClass::Static),
            RefreshRequest::Dynamic => facts_for_class(RefreshClass::Dynamic),
        };
        refresh_once(
            &cache,
            &options.provider,
            &specifications,
            options.source_timeout,
        )
        .await;
    }
}

async fn refresh_once(
    cache: &Arc<RwLock<StateCache>>,
    provider: &ProviderConfig,
    specifications: &[&'static FactSpec],
    source_timeout: Duration,
) {
    match collect(provider, specifications, source_timeout).await {
        Ok(outcomes) => {
            cache.write().await.apply(outcomes);
        }
        Err(error) => {
            let ids = specifications
                .iter()
                .map(|fact| fact.id)
                .collect::<Vec<_>>();
            let mut state = cache.write().await;
            state.record_provider_error(&ids, error.clone());
            log_message(&format!("refresh error: {error}"));
        }
    }
}

fn combine_refresh(left: RefreshRequest, right: RefreshRequest) -> RefreshRequest {
    match (left, right) {
        (RefreshRequest::All, _) | (_, RefreshRequest::All) => RefreshRequest::All,
        (RefreshRequest::Static, RefreshRequest::Dynamic)
        | (RefreshRequest::Dynamic, RefreshRequest::Static) => RefreshRequest::All,
        (same, _) => same,
    }
}

fn start_watcher(
    provider: &ProviderConfig,
    sender: mpsc::Sender<RefreshRequest>,
) -> Option<RecommendedWatcher> {
    let request = match provider {
        ProviderConfig::Fixture(_) => RefreshRequest::All,
        ProviderConfig::Live => RefreshRequest::Static,
    };
    let callback_sender = sender.clone();
    let mut watcher =
        match notify::recommended_watcher(move |result: notify::Result<notify::Event>| match result
        {
            Ok(_) => {
                let _ = callback_sender.try_send(request);
            }
            Err(error) => log_message(&format!("watch error: {error}")),
        }) {
            Ok(watcher) => watcher,
            Err(error) => {
                log_message(&format!("cannot initialize file watches: {error}"));
                return None;
            }
        };
    let paths = watch_paths(provider);
    let mut watched = 0;
    for (path, mode) in paths {
        if !path.exists() {
            continue;
        }
        match watcher.watch(&path, mode) {
            Ok(()) => watched += 1,
            Err(error) => log_message(&format!("cannot watch {}: {error}", path.display())),
        }
    }
    if watched == 0 {
        log_message("no file watch is active; periodic refresh remains active");
    }
    Some(watcher)
}

fn watch_paths(provider: &ProviderConfig) -> Vec<(PathBuf, RecursiveMode)> {
    match provider {
        ProviderConfig::Fixture(path) => vec![(
            path.parent()
                .unwrap_or_else(|| Path::new("."))
                .to_path_buf(),
            RecursiveMode::NonRecursive,
        )],
        ProviderConfig::Live => vec![
            (PathBuf::from("/etc/hostname"), RecursiveMode::NonRecursive),
            (
                PathBuf::from("/etc/os-release"),
                RecursiveMode::NonRecursive,
            ),
            (
                PathBuf::from("/var/lib/pacman/local"),
                RecursiveMode::Recursive,
            ),
            (
                PathBuf::from("/etc/snapper/configs"),
                RecursiveMode::Recursive,
            ),
        ],
    }
}

fn all_facts() -> Vec<&'static FactSpec> {
    FACTS.iter().collect()
}

fn daemon_runtime(socket: &Path) -> RuntimeInfo {
    RuntimeInfo {
        selected_provider: "daemon".to_owned(),
        source: socket.display().to_string(),
        fallback_errors: Vec::new(),
        cache: None,
    }
}

fn validate_options(options: &ServeOptions) -> Result<(), CollectionError> {
    if options.source_timeout.is_zero()
        || options.request_timeout.is_zero()
        || options.dynamic_refresh.is_zero()
        || options.full_refresh.is_zero()
    {
        return Err(CollectionError::new(
            "invalid_request",
            "Daemon timeout and refresh values must be greater than zero",
        ));
    }
    if options.socket_mode > 0o777 {
        return Err(CollectionError::new(
            "invalid_request",
            "The socket mode must be between 0000 and 0777",
        ));
    }
    Ok(())
}

async fn prepare_socket_path(socket: &Path) -> Result<(), CollectionError> {
    let parent = socket.parent().ok_or_else(|| {
        CollectionError::new(
            "socket_path_invalid",
            "The socket path has no parent directory",
        )
    })?;
    tokio::fs::create_dir_all(parent).await.map_err(|error| {
        CollectionError::new(
            "socket_directory_failed",
            format!(
                "Cannot create socket directory {}: {error}",
                parent.display()
            ),
        )
    })?;
    let metadata = match tokio::fs::symlink_metadata(socket).await {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            return Err(CollectionError::new(
                "socket_path_failed",
                format!("Cannot inspect socket path {}: {error}", socket.display()),
            ));
        }
    };
    if !metadata.file_type().is_socket() {
        return Err(CollectionError::new(
            "socket_path_unsafe",
            format!(
                "Refusing to replace a non-socket path: {}",
                socket.display()
            ),
        ));
    }
    if timeout(Duration::from_millis(200), UnixStream::connect(socket))
        .await
        .is_ok_and(|result| result.is_ok())
    {
        return Err(CollectionError::new(
            "daemon_already_running",
            format!(
                "A daemon already accepts connections at {}",
                socket.display()
            ),
        ));
    }
    tokio::fs::remove_file(socket).await.map_err(|error| {
        CollectionError::new(
            "socket_cleanup_failed",
            format!("Cannot remove stale socket {}: {error}", socket.display()),
        )
    })
}

async fn remove_socket(socket: &Path) {
    match tokio::fs::symlink_metadata(socket).await {
        Ok(metadata) if metadata.file_type().is_socket() => {
            if let Err(error) = tokio::fs::remove_file(socket).await {
                log_message(&format!(
                    "cannot remove socket {}: {error}",
                    socket.display()
                ));
            }
        }
        _ => {}
    }
}

#[cfg(unix)]
async fn shutdown_signal() -> Result<(), CollectionError> {
    use tokio::signal::unix::{SignalKind, signal};

    let mut terminate = signal(SignalKind::terminate()).map_err(|error| {
        CollectionError::new(
            "signal_handler_failed",
            format!("Cannot register the SIGTERM handler: {error}"),
        )
    })?;
    let mut interrupt = signal(SignalKind::interrupt()).map_err(|error| {
        CollectionError::new(
            "signal_handler_failed",
            format!("Cannot register the SIGINT handler: {error}"),
        )
    })?;
    tokio::select! {
        _ = terminate.recv() => {},
        _ = interrupt.recv() => {},
    }
    Ok(())
}

fn log_message(message: &str) {
    eprintln!("jiritsu-stated: {message}");
}
