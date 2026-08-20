#!/usr/bin/env python3
"""Extract compact field slices from a narrow MCAP time window (streaming, bounded)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from mcap_common import add_limit_args, dotted_get, parse_field_spec, read_records


def build_slice(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    field_specs = [parse_field_spec(spec) for spec in args.field]
    topics = list(dict.fromkeys(topic for topic, _ in field_specs))
    records, status = read_records(
        args.recording, topics, args.start_secs, args.end_secs,
        max_records=args.max_records, max_bytes=args.max_bytes, timeout_secs=args.timeout_secs,
    )
    output: list[dict[str, Any]] = []
    for record in records:
        topic = record.get("topic")
        for field_topic, field in field_specs:
            if topic != field_topic:
                continue
            output.append(
                {
                    "time": float(record["log_time"]),
                    "topic": topic,
                    "field": field,
                    "value": dotted_get(record, field),
                    "frame": None,
                    "unit": None,
                    "validity": None,
                    "publish_time": record.get("publish_time"),
                    "clock": "log_time",
                }
            )
    return output, status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--start-secs", type=float, required=True)
    parser.add_argument("--end-secs", type=float, required=True)
    parser.add_argument(
        "--field",
        action="append",
        required=True,
        metavar="TOPIC:PATH",
        help="Field to retain; repeat for multiple topic/field pairs",
    )
    add_limit_args(parser)
    args = parser.parse_args()
    try:
        started = time.monotonic()
        rows, status = build_slice(args)
        for row in rows:
            print(json.dumps(row, ensure_ascii=True, separators=(",", ":")))
        total = round(time.monotonic() - started, 3)
        read_secs = status.get("read_secs")
        timing = {
            "total_secs": total,
            "read_secs": read_secs,
            "analyze_secs": round(total - read_secs, 3) if read_secs is not None else None,
        }
        # Final structured status line: partial reads are reported, never silent.
        print(json.dumps({"read": status, "timing": timing}, ensure_ascii=True, separators=(",", ":")))
    except (OSError, RuntimeError, ValueError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"recording": str(args.recording), "error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
