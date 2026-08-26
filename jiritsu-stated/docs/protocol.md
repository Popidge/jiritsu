# `jiritsu-stated` daemon protocol

This document defines the Rust daemon protocol and cache behavior.

The protocol uses schema version `1.0`. It adds runtime metadata without changing the existing fact fields.

## Transport

The default endpoint is `/run/jiritsu/stated.sock`.

The transport is a Unix stream socket. Each connection contains one request and one response.

The client sends one JSON object. Then the client closes the write half of the connection.

The daemon sends one JSON object. Then the daemon closes the connection.

The daemon does not retain a client session.

## Request

A query request has this form:

```json
{
  "schema_version": "1.0",
  "operation": "query",
  "selectors": ["system", "hardware.cpu"]
}
```

The fields have these meanings:

| Field | Requirement | Meaning |
| --- | --- | --- |
| `schema_version` | Required | The protocol schema. The daemon accepts `1.0`. |
| `operation` | Required | The operation name. The daemon accepts `query`. |
| `selectors` | Optional | Exact fact IDs or group names. An empty array selects all facts. |

Unknown fields make the request invalid. An unknown selector produces the stable `unknown_selector` error.

The request limit is 64 KiB. The daemon rejects a larger request with `request_too_large`.

## Response

The response keeps the direct query fields:

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "collected_at": "2026-08-25T12:00:00Z",
  "query": {"selectors": ["system.hostname"]},
  "facts": {
    "system.hostname": {
      "value": "example-host",
      "source": {
        "id": "system.hostname",
        "kind": "file",
        "locator": "/etc/hostname"
      },
      "observed_at": "2026-08-25T11:59:58Z",
      "age_seconds": 2.0,
      "fixture": false
    }
  },
  "errors": [],
  "runtime": {
    "selected_provider": "daemon",
    "source": "/run/jiritsu/stated.sock",
    "fallback_errors": [],
    "cache": {
      "epoch": 42,
      "refreshed_at": "2026-08-25T11:59:58Z",
      "last_refresh_at": "2026-08-25T11:59:58Z",
      "last_refresh_errors": []
    }
  }
}
```

`status` is `ok`, `partial`, or `error`. A partial response contains successful facts and errors.

The response limit is 16 MiB. The client rejects a larger response.

## Runtime metadata

`runtime.selected_provider` identifies the selected provider:

| Value | Meaning |
| --- | --- |
| `daemon` | The CLI received a valid socket response. |
| `direct` | The Rust CLI collected live facts directly. |
| `fixture` | The Rust CLI collected fixture facts directly. |
| `none` | The request stopped before fact collection. |

`runtime.source` identifies the socket, fixture, or direct source set.

`runtime.fallback_errors` contains daemon errors that caused direct fallback. A successful daemon response has an empty array.

## Cache invariant

The cache epoch starts at zero. The initial collection advances it when a fact or error enters the cache.

These changes advance the epoch:

- A fact value changes.
- A fact source changes.
- A fixture status changes.
- A fact becomes available.
- A fact becomes unavailable before it has a cached value.

An observation timestamp change does not advance the epoch.

A failed refresh does not remove a successful cached fact. The response reports the increasing fact age.

`last_refresh_errors` reports errors from the most recent refresh. A later successful refresh clears this array.

## Refresh events

The daemon has three refresh event types:

| Event | Sources |
| --- | --- |
| Static | Hostname, operating system, kernel, Omarchy version, packages, CPU, and Snapper configurations |
| Dynamic | Services, memory, networks, and active root subvolume |
| All | Every source |

Filesystem notifications produce static events. A fixture notification produces an all event.

The dynamic timer produces a dynamic event every 15 seconds. The safety timer produces an all event every 300 seconds.

The refresh worker combines queued events and waits 150 milliseconds. This delay reduces duplicate work during file replacement or package transactions.

Only one refresh worker collects facts. Thus, two refresh operations cannot overwrite each other out of order.

## Failure behavior

The daemon limits active connections to 64. An additional connection receives `daemon_busy`.

The daemon gives a client two seconds to finish its request. An incomplete request receives `request_timeout`.

Invalid JSON receives `request_invalid`. An unknown operation receives `operation_unsupported`.

The daemon continues after client protocol errors. A source error affects only its fact.

If the fixture becomes invalid, the daemon keeps the last successful cache. The cache metadata reports the refresh error.

At startup, the daemon refuses to replace a non-socket path. It also refuses a socket that accepts connections.

The daemon removes a stale socket before startup. It removes its active socket during a normal shutdown.

## Compatibility rule

A new collector can change the source metadata. It must not change the meaning of an existing fact value.

New response metadata must be additive. Existing `1.0` consumers can ignore fields that they do not use.

A protocol version change is necessary when a request or existing response field changes meaning.
