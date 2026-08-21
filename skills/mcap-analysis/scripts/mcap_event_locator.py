#!/usr/bin/env python3
"""Locate abnormal events in MCAP topics with predicates, not raw field changes.

Gap semantics (missing-policy=unknown, changed mode): a value change straddling a
data gap is reported ONLY in gap_transitions and ONLY when the values on the two
sides differ; a pending (un-debounced) candidate interrupted by a gap is dropped.
The transition instant inside a gap is unprovable by design — check
missing_or_null_count and gap_transitions rather than assuming continuity.

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
    if isinstance(value, str):
        # protojson 把 int64/uint64 序列化为字符串；接受纯数字字符串
        try:
            number = float(value)
        except ValueError:
            return None
        return number if number == number and abs(number) != float("inf") else None
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
    sub_debounce_runs = 0      # 曾命中谓词但被 debounce/min-duration 重置的短 run
    longest_sub_run = 0
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
                    "sub_debounce_runs": sub_debounce_runs,
                    "longest_sub_debounce_run": longest_sub_run,
                    "note": None,
                }
        else:
            if run:
                sub_debounce_runs += 1
                longest_sub_run = max(longest_sub_run, len(run))
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
        "sub_debounce_runs": sub_debounce_runs,
        "longest_sub_debounce_run": longest_sub_run,
        "note": ("predicate matched in %d short run(s) (longest %d) suppressed by "
                 "debounce/min-duration; rerun with --debounce-count 1 --min-duration-ms 0 "
                 "to inspect" % (sub_debounce_runs, longest_sub_run))
                if sub_debounce_runs else None,
    }


def resolve_tolerance(samples: list[tuple[float, Any]], spec) -> tuple[float, str]:
    """Resolve --tolerance: float, or 'auto' = 4 x median |inter-sample delta| (noise floor)."""
    if spec != "auto":
        return float(spec), "explicit"
    numeric = 0
    deltas = []
    prev = None
    for _, v in samples:
        n = as_number(v)
        if n is None:
            prev = None  # 缺口/非数值：断开，不产生跨缺口伪 delta
            continue
        numeric += 1
        if prev is not None:
            deltas.append(abs(n - prev))
        prev = n
    if numeric < 8 or not deltas:
        return 0.0, f"auto-fallback({numeric} numeric samples)"
    deltas.sort()
    mid = len(deltas) // 2
    median = deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2.0
    return 4.0 * median, "auto"


def locate_changed(samples: list[tuple[float, Any]], args: argparse.Namespace) -> dict[str, Any]:
    tolerance, tolerance_mode = resolve_tolerance(samples, args.tolerance)
    args = argparse.Namespace(**{**vars(args), "tolerance": tolerance})
    transitions: list[dict[str, Any]] = []
    gap_transitions: list[dict[str, Any]] = []  # 跨缺口前后值不同：疑似变化，时刻不可证
    gap_before: tuple[float, Any] | None = None
    last_seen: tuple[float, Any] | None = None  # 缺口前最后一个真实（非缺失）样本
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
                if last_seen is not None:
                    gap_before = last_seen
                prior = None
                candidate = None
                stable = 0
                continue
        last_seen = (stamp, value)
        if prior is None:
            if gap_before is not None:
                if values_differ(gap_before[1], value, args.tolerance):
                    gap_transitions.append({
                        "before_gap_time": gap_before[0], "before_gap_value": gap_before[1],
                        "after_gap_time": stamp, "after_gap_value": value,
                        "note": "value differs across a data gap; transition instant unprovable",
                    })
                gap_before = None
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
        "tolerance_used": args.tolerance,
        "tolerance_mode": tolerance_mode,
        "gap_transition_count": len(gap_transitions),
        "gap_transitions": gap_transitions[: args.max_changes],
        "gap_transitions_truncated": len(gap_transitions) > args.max_changes,
    }


def predicate_text(args: argparse.Namespace) -> str:
    if args.condition in NUMERIC_CONDITIONS:
        return f"value {args.condition} {args.threshold}"
    if args.condition in {"eq", "ne"}:
        return f"value {args.condition} {args.value!r}"
    if args.condition == "regex":
        return f"value regex {args.pattern!r}"
    return f"changed(tolerance={args.tolerance!r}, debounce={args.debounce_count})"


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
    parser.add_argument("--value", help="Comparison value for eq/ne — canonical JSON text: booleans are lowercase true/false ('False' never matches)")
    parser.add_argument("--pattern", help="Regex for --condition regex")
    def tolerance_arg(raw: str):
        if raw.strip().lower() == "auto":
            return "auto"
        try:
            val = float(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"--tolerance must be a number or 'auto', got {raw!r}")
        if val != val or abs(val) == float("inf") or val < 0:
            raise argparse.ArgumentTypeError(f"--tolerance must be a finite non-negative number, got {raw!r}")
        return val

    parser.add_argument("--tolerance", type=tolerance_arg, default=0.0,
                        help="Numeric noise band for changed: a finite non-negative number, or 'auto' "
                             "(4 x median absolute delta between CONSECUTIVE samples, gaps excluded; "
                             "falls back to 0 when <8 numeric samples). Not applicable to threshold "
                             "predicates (a warning is emitted if combined).")
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
    if args.condition != "changed" and args.tolerance == "auto":
        print("warning: --tolerance auto only applies to --condition changed; ignored", file=sys.stderr)
        args.tolerance = 0.0
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
