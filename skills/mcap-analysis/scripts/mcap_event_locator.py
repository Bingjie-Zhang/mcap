#!/usr/bin/env python3
"""Locate abnormal events in MCAP topics with predicates, not raw field changes.

A sample is classified good / bad / unknown by an explicit predicate
(gt/gte/lt/lte/eq/ne/regex/changed). The locator reports last_good and
first_bad with debounce and minimum-duration semantics, so float jitter and
normal continuous variation do not register as events.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from mcap_common import (
    add_limit_args,
    dotted_get,
    parse_field_spec,
    read_records,
    record_time,
    recording_bounds,
)

NUMERIC_CONDITIONS = {"gt", "gte", "lt", "lte"}


def as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def classify(value: Any, args: argparse.Namespace, pattern: Optional[re.Pattern]) -> str:
    """Return 'bad' when the predicate matches, 'good' otherwise, 'unknown' on missing."""
    if value is None:
        return {"unknown": "unknown", "skip": "skip", "bad": "bad"}[args.missing_policy]
    condition = args.condition
    if condition in NUMERIC_CONDITIONS:
        number = as_number(value)
        if number is None:
            return "unknown"
        threshold = args.threshold
        return "bad" if {
            "gt": number > threshold,
            "gte": number >= threshold,
            "lt": number < threshold,
            "lte": number <= threshold,
        }[condition] else "good"
    if condition == "eq":
        return "bad" if _canonical(value) == args.value else "good"
    if condition == "ne":
        return "bad" if _canonical(value) != args.value else "good"
    if condition == "regex":
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True, sort_keys=True)
        return "bad" if pattern is not None and pattern.search(text) else "good"
    raise ValueError(f"unsupported condition {condition!r}")


def _canonical(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def values_differ(prior: Any, current: Any, tolerance: float) -> bool:
    prior_num, current_num = as_number(prior), as_number(current)
    if prior_num is not None and current_num is not None:
        return abs(current_num - prior_num) > tolerance
    return _canonical(prior) != _canonical(current)


def locate_predicate(samples: list[tuple[float, Any]], args: argparse.Namespace,
                     pattern: Optional[re.Pattern]) -> dict[str, Any]:
    labeled = []
    missing = 0
    for stamp, value in samples:
        state = classify(value, args, pattern)
        if state == "skip":
            missing += 1
            continue
        if state == "unknown":
            missing += 1
        labeled.append((stamp, value, state))
    last_good: Optional[dict[str, Any]] = None
    run: list[tuple[float, Any]] = []
    for stamp, value, state in labeled:
        if state == "bad":
            run.append((stamp, value))
            duration_ms = (run[-1][0] - run[0][0]) * 1000.0
            if len(run) >= args.debounce_count and duration_ms >= args.min_duration_ms:
                return {
                    "status": "transition_found" if last_good else "bad_from_start",
                    "last_good": last_good,
                    "first_bad": {"time": run[0][0], "value": run[0][1]},
                    "bad_run_count": len(run),
                    "bad_duration_ms": round(duration_ms, 3),
                    "missing_or_null_count": missing,
                }
        else:
            run = []
            if state == "good":
                last_good = {"time": stamp, "value": value}
    if run:
        status = "bad_but_below_debounce"
    elif last_good is not None:
        status = "all_good"
    else:
        status = "no_classifiable_samples"
    return {
        "status": status,
        "last_good": last_good,
        "first_bad": {"time": run[0][0], "value": run[0][1]} if run else None,
        "bad_run_count": len(run),
        "missing_or_null_count": missing,
    }


def locate_changed(samples: list[tuple[float, Any]], args: argparse.Namespace) -> dict[str, Any]:
    transitions: list[dict[str, Any]] = []
    missing = 0
    prior: Optional[tuple[float, Any]] = None
    candidate: Optional[dict[str, Any]] = None
    stable = 0
    for stamp, value in samples:
        if value is None:
            missing += 1
            if args.missing_policy == "skip":
                continue
            if args.missing_policy == "unknown":
                # 缺失视为断线：重置基线，不把 值->null / null->值 当作事件
                prior = None
                candidate = None
                stable = 0
                continue
        if prior is None:
            prior = (stamp, value)
            continue
        if candidate is not None:
            if values_differ(candidate["first_changed_value"], value, args.tolerance):
                if values_differ(prior[1], value, args.tolerance):
                    # 仍偏离旧基线：换挡（如 1->2->3），以同一旧基线重开候选，不吞跃迁
                    candidate = {
                        "last_good_time": prior[0],
                        "last_good_value": prior[1],
                        "first_changed_time": stamp,
                        "first_changed_value": value,
                    }
                    stable = 1
                else:
                    candidate = None  # 回落到旧基线附近：确为噪声
                    stable = 0
                    prior = (stamp, value)
                continue
            stable += 1
            if stable >= args.debounce_count:
                transitions.append(candidate)
                prior = (stamp, value)
                candidate = None
                stable = 0
            continue
        if values_differ(prior[1], value, args.tolerance):
            candidate = {
                "last_good_time": prior[0],
                "last_good_value": prior[1],
                "first_changed_time": stamp,
                "first_changed_value": value,
            }
            stable = 1
            if stable >= args.debounce_count:
                transitions.append(candidate)
                prior = (stamp, value)
                candidate = None
                stable = 0
        else:
            prior = (stamp, value)
    return {
        "status": "transitions_found" if transitions else "no_transition",
        "transition_count": len(transitions),
        "transitions": transitions[: args.max_changes],
        "transitions_truncated": len(transitions) > args.max_changes,
        "missing_or_null_count": missing,
    }


def predicate_text(args: argparse.Namespace) -> str:
    if args.condition in NUMERIC_CONDITIONS:
        return f"value {args.condition} {args.threshold}"
    if args.condition in {"eq", "ne"}:
        return f"value {args.condition} {args.value!r}"
    if args.condition == "regex":
        return f"value regex {args.pattern!r}"
    return f"changed(tolerance={args.tolerance}, debounce={args.debounce_count})"


def locate(args: argparse.Namespace) -> dict[str, Any]:
    field_specs = [parse_field_spec(spec) for spec in args.field]
    topics = list(dict.fromkeys(topic for topic, _ in field_specs))
    start_secs, end_secs = recording_bounds(args.recording)
    if args.start_secs is not None:
        start_secs = max(start_secs, args.start_secs)
    if args.end_secs is not None:
        end_secs = min(end_secs, args.end_secs)
    records, read_status = read_records(
        args.recording, topics, start_secs, end_secs,
        max_records=args.max_records, max_bytes=args.max_bytes, timeout_secs=args.timeout_secs,
    )
    pattern = re.compile(args.pattern) if args.pattern else None

    results = []
    for field_topic, field in field_specs:
        samples: list[tuple[float, Any]] = []
        for record in records:
            if record.get("topic") != field_topic:
                continue
            stamp = record_time(record, args.clock, args.header_time_field)
            if stamp is None:
                continue
            samples.append((stamp, dotted_get(record, field)))
        if args.condition == "changed":
            outcome = locate_changed(samples, args)
        else:
            outcome = locate_predicate(samples, args, pattern)
        outcome.update({"topic": field_topic, "field": field, "sample_count": len(samples)})
        results.append(outcome)

    return {
        "recording": str(args.recording),
        "clock": args.clock,
        "window_secs": [start_secs, end_secs],
        "predicate": predicate_text(args),
        "read": read_status,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--start-secs", type=float)
    parser.add_argument("--end-secs", type=float)
    parser.add_argument("--field", action="append", required=True, metavar="TOPIC:PATH")
    parser.add_argument("--condition", default="changed",
                        choices=["changed", "gt", "gte", "lt", "lte", "eq", "ne", "regex"])
    parser.add_argument("--threshold", type=float, help="Numeric bound for gt/gte/lt/lte")
    parser.add_argument("--value", help="Comparison value for eq/ne (string or canonical JSON)")
    parser.add_argument("--pattern", help="Regex for --condition regex")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="Numeric noise band for changed; differences <= tolerance are not events")
    parser.add_argument("--debounce-count", type=int, default=1,
                        help="Consecutive samples required to confirm an event")
    parser.add_argument("--min-duration-ms", type=float, default=0.0,
                        help="Minimum duration of the bad run before it counts")
    parser.add_argument("--missing-policy", default="unknown", choices=["unknown", "skip", "bad"])
    parser.add_argument("--clock", default="log_time", choices=["log_time", "publish_time", "header_time"])
    parser.add_argument("--header-time-field", default="data.header.timestamp")
    parser.add_argument("--max-changes", type=int, default=40)
    add_limit_args(parser)
    args = parser.parse_args()
    if args.start_secs is not None and args.end_secs is not None and args.end_secs <= args.start_secs:
        parser.error("--end-secs must be greater than --start-secs")
    if args.condition in NUMERIC_CONDITIONS and args.threshold is None:
        parser.error(f"--condition {args.condition} requires --threshold")
    if args.condition in {"eq", "ne"} and args.value is None:
        parser.error(f"--condition {args.condition} requires --value")
    if args.condition == "regex" and not args.pattern:
        parser.error("--condition regex requires --pattern")
    if args.debounce_count <= 0 or args.max_changes <= 0:
        parser.error("--debounce-count and --max-changes must be positive")
    try:
        started = time.monotonic()
        result = locate(args)
        total = round(time.monotonic() - started, 3)
        read_secs = result.get("read", {}).get("read_secs")
        result["timing"] = {
            "total_secs": total,
            "read_secs": read_secs,
            "analyze_secs": round(total - read_secs, 3) if read_secs is not None else None,
        }
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    except (OSError, RuntimeError, ValueError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"recording": str(args.recording), "error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
