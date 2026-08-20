#!/usr/bin/env python3
"""Correlate a downstream MCAP predicate with the latest fresh upstream frame.

Explicit time model: choose the clock for each side (log_time / publish_time /
header_time) and bound staleness with --max-age-ms. A downstream evaluation
matched to an upstream frame older than max_age_ms is reported as stale and
kept out of the fresh predicate statistics, so "reused an expired frame" can
no longer masquerade as "upstream input was normal".
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from mcap_common import add_limit_args, dotted_get, read_records, record_time


def matches(text: str, pattern: Optional[str], use_regex: bool) -> Optional[bool]:
    if pattern is None:
        return None
    return re.search(pattern, text) is not None if use_regex else pattern in text


def build_result(args: argparse.Namespace) -> dict[str, Any]:
    # Single pass over the recording: both topics in one read.
    all_records, read_status = read_records(
        args.recording, [args.source_topic, args.target_topic], args.start_secs, args.end_secs,
        max_records=args.max_records, max_bytes=args.max_bytes, timeout_secs=args.timeout_secs)
    source_records = [r for r in all_records if r.get("topic") == args.source_topic]
    target_records = [r for r in all_records if r.get("topic") == args.target_topic]
    if not source_records:
        raise ValueError(f"no source messages found for {args.source_topic}")
    if not target_records:
        raise ValueError(f"no target messages found for {args.target_topic}")

    # Time each side on its requested clock; drop records whose clock is unreadable.
    sources: list[tuple[float, dict[str, Any]]] = []
    source_clock_missing = 0
    for record in source_records:
        stamp = record_time(record, args.source_clock, args.source_header_time_field)
        if stamp is None:
            source_clock_missing += 1
            continue
        sources.append((stamp, record))
    sources.sort(key=lambda item: item[0])
    source_times = [stamp for stamp, _ in sources]
    if not sources:
        raise ValueError(f"no source message exposes clock {args.source_clock}")

    max_age_secs = args.max_age_ms / 1000.0 if args.max_age_ms is not None else None
    rows: list[dict[str, Any]] = []
    results_by_source: dict[str, list[bool]] = defaultdict(list)
    target_clock_missing = 0
    target_field_missing = 0
    source_id_missing = 0
    previous_source_id_num: Optional[float] = None
    source_id_resets = 0

    for target in target_records:
        target_time = record_time(target, args.target_clock, args.target_header_time_field)
        if target_time is None:
            target_clock_missing += 1
            continue
        index = bisect.bisect_right(source_times, target_time) - 1
        if index >= 0 and not args.allow_equal_time and source_times[index] == target_time:
            index -= 1
        if index < 0:
            rows.append({"target_time": target_time, "age_status": "no_prior_source"})
            continue
        source_time, source = sources[index]
        age_ms = round((target_time - source_time) * 1000.0, 3)
        age_status = "fresh" if args.max_age_ms is None or age_ms <= args.max_age_ms else "stale"

        source_id = dotted_get(source, args.source_id_field) if args.source_id_field else None
        if args.source_id_field and source_id is None:
            source_id_missing += 1
        if isinstance(source_id, (int, float)) and not isinstance(source_id, bool):
            if previous_source_id_num is not None and source_id < previous_source_id_num:
                source_id_resets += 1
            previous_source_id_num = float(source_id)

        field_value = dotted_get(target, args.target_field)
        if field_value is None:
            target_field_missing += 1
            rows.append({"target_time": target_time, "source_time": source_time,
                         "age_ms": age_ms, "age_status": age_status, "target_field_missing": True})
            continue
        target_text = field_value if isinstance(field_value, str) else json.dumps(field_value, sort_keys=True)
        raw = bool(matches(target_text, args.match, args.regex))
        qualified = matches(target_text, args.qualified_match, args.regex)
        row: dict[str, Any] = {
            "target_time": target_time,
            "source_time": source_time,
            "age_ms": age_ms,
            "age_status": age_status,
            "match_clock": f"{args.source_clock}->{args.target_clock}",
            "raw_predicate": raw,
        }
        if source_id is not None:
            row["source_id"] = source_id
            results_by_source[json.dumps(source_id, sort_keys=True)].append(raw)
        if qualified is not None:
            row["qualified_predicate"] = qualified
        rows.append(row)

    evaluated = [row for row in rows if "raw_predicate" in row]
    if not evaluated:
        raise ValueError("no target message could be evaluated against a preceding source frame")
    fresh = [row for row in evaluated if row["age_status"] == "fresh"]
    stale = [row for row in evaluated if row["age_status"] == "stale"]

    flips = []
    for previous, current in zip(evaluated, evaluated[1:]):
        if previous["raw_predicate"] == current["raw_predicate"]:
            continue
        flips.append({
            "from_source_id": previous.get("source_id"),
            "to_source_id": current.get("source_id"),
            "source_changed": previous.get("source_id") != current.get("source_id"),
            "target_time": current["target_time"],
            "age_status": current["age_status"],
        })

    reused = [values for values in results_by_source.values() if len(values) > 1]
    summary: dict[str, Any] = {
        "recording": str(args.recording),
        "clock": {"source": args.source_clock, "target": args.target_clock,
                  "max_age_ms": args.max_age_ms, "allow_equal_time": args.allow_equal_time},
        "window_secs": [args.start_secs, args.end_secs],
        "source_topic": args.source_topic,
        "target_topic": args.target_topic,
        "read": read_status,
        "source_messages": len(source_records),
        "target_messages": len(target_records),
        "matched_target_messages": len(evaluated),
        "fresh_matches": len(fresh),
        "stale_matches": len(stale),
        "no_prior_source": sum(1 for row in rows if row.get("age_status") == "no_prior_source"),
        "raw_true_count_fresh": sum(row["raw_predicate"] for row in fresh),
        "raw_true_count_stale": sum(row["raw_predicate"] for row in stale),
        "quality": {
            "source_clock_missing": source_clock_missing,
            "target_clock_missing": target_clock_missing,
            "target_field_missing": target_field_missing,
            "source_id_missing": source_id_missing,
            "source_id_resets": source_id_resets,
        },
        "reused_source_frames": len(reused),
        "reused_frames_consistent": sum(len(set(values)) == 1 for values in reused),
        "reused_frames_mixed": sum(len(set(values)) > 1 for values in reused),
        "predicate_flips": len(flips),
        "flips_on_source_change": sum(flip["source_changed"] for flip in flips),
        "flips_without_source_change": sum(not flip["source_changed"] for flip in flips),
        "flips": flips[: args.max_flips],
        "flips_truncated": len(flips) > args.max_flips,
    }
    if args.qualified_match is not None:
        summary["qualified_true_count"] = sum(bool(row.get("qualified_predicate")) for row in evaluated)
        summary["raw_qualified_mismatch"] = sum(
            row["raw_predicate"] != row.get("qualified_predicate") for row in evaluated)
    if args.rows:
        summary["rows"] = rows
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--source-topic", required=True)
    parser.add_argument("--target-topic", required=True)
    parser.add_argument("--start-secs", type=float)
    parser.add_argument("--end-secs", type=float)
    parser.add_argument("--source-clock", default="log_time",
                        choices=["log_time", "publish_time", "header_time"])
    parser.add_argument("--target-clock", default="log_time",
                        choices=["log_time", "publish_time", "header_time"])
    parser.add_argument("--source-header-time-field", default="data.header.timestamp")
    parser.add_argument("--target-header-time-field", default="data.header.timestamp")
    parser.add_argument("--max-age-ms", type=float,
                        help="Source frames older than this are reported stale, not fresh input")
    parser.add_argument("--allow-equal-time", dest="allow_equal_time", action="store_true", default=True)
    parser.add_argument("--no-allow-equal-time", dest="allow_equal_time", action="store_false")
    parser.add_argument("--source-id-field",
                        help="Optional dotted path of the source frame ID; omit if the schema has none")
    parser.add_argument("--target-field", required=True)
    parser.add_argument("--match", required=True, help="Raw-predicate substring or regex")
    parser.add_argument("--qualified-match", help="Qualified-predicate substring or regex")
    parser.add_argument("--regex", action="store_true")
    parser.add_argument("--rows", action="store_true", help="Include per-evaluation rows")
    parser.add_argument("--max-flips", type=int, default=20)
    add_limit_args(parser)
    args = parser.parse_args()
    if args.start_secs is not None and args.end_secs is not None and args.end_secs <= args.start_secs:
        parser.error("--end-secs must be greater than --start-secs")
    if args.max_flips <= 0:
        parser.error("--max-flips must be positive")
    try:
        started = time.monotonic()
        result = build_result(args)
        total = round(time.monotonic() - started, 3)
        read_secs = result.get("read", {}).get("read_secs")
        result["timing"] = {
            "total_secs": total,
            "read_secs": read_secs,
            "analyze_secs": round(total - read_secs, 3) if read_secs is not None else None,
        }
    except (OSError, RuntimeError, ValueError, TimeoutError, json.JSONDecodeError) as exc:
        print(json.dumps({"recording": str(args.recording), "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
