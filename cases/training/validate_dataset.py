#!/usr/bin/env python3
"""Independent, raw-MCAP validation for the APA manager dataset.

This validator deliberately does not call mcap_event_locator.py.  It parses
the MCAP messages itself, evaluates the predicate in each expected anchor,
and compares both last_good and first_bad.  It is intended to catch a test
oracle that agrees with a tool by construction.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from mcap.reader import make_reader


ROOT = Path(__file__).resolve().parent


def get_path(data: Any, path: str) -> Any:
    if path.startswith("data."):
        path = path[5:]
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def as_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def is_bad(value: Any, op: str, raw_bound: str) -> bool | None:
    if value is None:
        return None
    if op == "regex":
        return re.search(raw_bound, str(value)) is not None
    bound = as_value(raw_bound)
    try:
        if op == "lt":
            return float(value) < float(bound)
        if op == "lte":
            return float(value) <= float(bound)
        if op == "gt":
            return float(value) > float(bound)
        if op == "gte":
            return float(value) >= float(bound)
        if op == "eq":
            return value == bound
        if op == "ne":
            return value != bound
    except (TypeError, ValueError):
        return False
    raise ValueError(f"unsupported predicate: {op}")


def read_topic(path: Path, topic: str) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    with path.open("rb") as stream:
        reader = make_reader(stream)
        for _, channel, message in reader.iter_messages(topics=[topic], log_time_order=True):
            records.append({
                "time": message.log_time / 1e9,
                "data": json.loads(message.data.decode("utf-8")),
            })
    return records, len(records)


def main() -> int:
    cases_path = ROOT / "cases.jsonl"
    failures: list[dict[str, Any]] = []
    total_messages = 0
    case_count = 0
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        case_count += 1
        recording = ROOT / case["recording"]
        # Count every channel independently from the selected anchor topic.
        all_count = 0
        with recording.open("rb") as stream:
            reader = make_reader(stream)
            for _, _, _ in reader.iter_messages(log_time_order=True):
                all_count += 1
        total_messages += all_count
        if all_count != case["record_count"]:
            failures.append({"case_id": case["case_id"], "kind": "record_count",
                             "expected": case["record_count"], "actual": all_count})

        anchor = case["expected"].get("anchor")
        if not anchor:
            continue
        op, bound = anchor["predicate"].split(" ", 1)
        rows, _ = read_topic(recording, anchor["topic"])
        samples = []
        for row in rows:
            value = get_path(row["data"], anchor["field"])
            samples.append((row["time"], value, is_bad(value, op, bound)))
        bad_index = next((i for i, (_, _, bad) in enumerate(samples) if bad is True), None)
        if bad_index is None:
            actual_status = "all_good"
            actual_last_good = None
            actual_first_bad = None
        elif bad_index == 0:
            actual_status = "bad_from_start"
            actual_last_good = None
            actual_first_bad = samples[0][0]
        else:
            actual_status = "transition_found"
            actual_last_good = samples[bad_index - 1][0]
            actual_first_bad = samples[bad_index][0]
        expected_last_good = anchor.get("last_good")
        expected_first_bad = anchor.get("first_bad")
        if actual_last_good != expected_last_good or actual_first_bad != expected_first_bad:
            failures.append({"case_id": case["case_id"], "kind": "anchor_boundary",
                             "status": actual_status,
                             "expected": {"last_good": expected_last_good, "first_bad": expected_first_bad},
                             "actual": {"last_good": actual_last_good, "first_bad": actual_first_bad}})

    result = {"status": "pass" if not failures else "fail", "cases": case_count,
              "messages": total_messages, "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
