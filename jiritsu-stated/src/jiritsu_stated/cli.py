from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Sequence

from .collectors import FACTS, select_facts
from .model import CollectionError, SCHEMA_VERSION, isoformat_utc, utc_now
from .sources import FixtureSourceProvider, LiveSourceProvider


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2
EXIT_USAGE = 64
EXIT_DATA = 65


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CollectionError("invalid_request", message)


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="jiritsu-stated")
    subparsers = parser.add_subparsers(dest="command", required=True)

    query = subparsers.add_parser("query", help="collect facts")
    query.add_argument(
        "selectors",
        nargs="*",
        help="exact fact IDs or category prefixes; all facts when omitted",
    )
    query.add_argument(
        "--fixture", metavar="PATH", help="replay source payloads from JSON"
    )
    query.add_argument(
        "--timeout",
        metavar="SECONDS",
        type=float,
        default=5.0,
        help="timeout for each live command source (default: 5)",
    )
    query.add_argument("--pretty", action="store_true", help="indent JSON output")

    catalog = subparsers.add_parser(
        "catalog", help="list available facts without probing"
    )
    catalog.add_argument("--pretty", action="store_true", help="indent JSON output")
    return parser


def emit(payload: dict[str, Any], pretty: bool = False) -> None:
    json.dump(
        payload,
        sys.stdout,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=False,
        separators=None if pretty else (",", ":"),
    )
    sys.stdout.write("\n")


def error_payload(error: CollectionError) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "facts": {},
        "errors": [error.public()],
    }


def run_catalog(pretty: bool) -> int:
    emit(
        {
            "schema_version": SCHEMA_VERSION,
            "facts": [
                {
                    "id": fact.fact_id,
                    "description": fact.description,
                    "source": fact.source.public(),
                }
                for fact in FACTS
            ],
        },
        pretty,
    )
    return EXIT_OK


def run_query(arguments: argparse.Namespace) -> int:
    if arguments.timeout <= 0:
        raise CollectionError("invalid_request", "--timeout must be greater than zero")
    definitions = select_facts(arguments.selectors)
    provider = (
        FixtureSourceProvider.load(arguments.fixture)
        if arguments.fixture
        else LiveSourceProvider(arguments.timeout)
    )
    facts: dict[str, Any] = {}
    observation_times: dict[str, datetime] = {}
    errors: list[dict[str, Any]] = []
    for definition in definitions:
        try:
            observation = provider.observe(definition.source)
            try:
                value = definition.parser(observation.text)
            except (TypeError, ValueError, KeyError) as error:
                raise CollectionError(
                    "parse_error",
                    f"Source payload does not match the {definition.fact_id} contract: {error}",
                    source=definition.source,
                ) from error
            facts[definition.fact_id] = {
                "value": value,
                "source": observation.source.public(),
                "observed_at": isoformat_utc(observation.observed_at),
                "fixture": observation.fixture,
            }
            observation_times[definition.fact_id] = observation.observed_at
        except CollectionError as error:
            errors.append(error.public(definition.fact_id))

    collected_at = utc_now()
    for fact_id, fact in facts.items():
        age_seconds = max(
            0.0, (collected_at - observation_times[fact_id]).total_seconds()
        )
        fact["age_seconds"] = round(age_seconds, 3)

    status = "ok" if not errors else ("partial" if facts else "error")
    emit(
        {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "collected_at": isoformat_utc(collected_at),
            "query": {"selectors": list(arguments.selectors)},
            "facts": facts,
            "errors": errors,
        },
        arguments.pretty,
    )
    if not errors:
        return EXIT_OK
    return EXIT_PARTIAL if facts else EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        if arguments.command == "catalog":
            return run_catalog(arguments.pretty)
        return run_query(arguments)
    except CollectionError as error:
        pretty = bool(getattr(locals().get("arguments", None), "pretty", False))
        emit(error_payload(error), pretty)
        if error.code.startswith("fixture_"):
            return EXIT_DATA
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
