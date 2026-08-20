#!/usr/bin/env python3
"""Read-only MCAP inventory that emits compact JSON to stdout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from mcap.reader import make_reader


def is_focus_topic(topic: str, focus_topics: list[str], focus_pattern: re.Pattern[str] | None) -> bool:
    return topic in focus_topics or (focus_pattern is not None and focus_pattern.search(topic) is not None)


def build_inventory(
    path: Path,
    top_n: int,
    focus_topics: list[str],
    focus_pattern: re.Pattern[str] | None,
) -> dict[str, Any]:
    with path.open("rb") as stream:
        reader = make_reader(stream)
        summary = reader.get_summary()
        if summary is None or summary.statistics is None:
            return scan_without_summary(path, top_n, focus_topics, focus_pattern)

        stats = summary.statistics
        duration_ns = max(0, stats.message_end_time - stats.message_start_time)
        topics: list[dict[str, Any]] = []
        for channel_id, count in stats.channel_message_counts.items():
            channel = summary.channels.get(channel_id)
            if channel is None:
                continue
            frequency_hz = count / (duration_ns / 1_000_000_000) if duration_ns else None
            topics.append(
                {
                    "topic": channel.topic,
                    "message_encoding": channel.message_encoding,
                    "schema_id": channel.schema_id,
                    "message_count": count,
                    "frequency_hz": round(frequency_hz, 3) if frequency_hz is not None else None,
                }
            )
        topics.sort(key=lambda item: item["message_count"], reverse=True)
        focus = [topic for topic in topics if is_focus_topic(topic["topic"], focus_topics, focus_pattern)]
        ordinary = [topic for topic in topics if topic not in focus]
        return {
            "recording": str(path),
            "readable": True,
            "primary_clock": "log_time",
            "time_range_ns": [stats.message_start_time, stats.message_end_time],
            "duration_s": round(duration_ns / 1_000_000_000, 3),
            "message_count": stats.message_count,
            "channel_count": stats.channel_count,
            "schema_count": stats.schema_count,
            "topics": ordinary[:top_n],
            "topics_truncated": len(ordinary) > top_n,
            "focus_topics": focus,
            "missing_focus_topics": [
                topic for topic in focus_topics if topic not in {item["topic"] for item in topics}
            ],
            "timestamp_fields": ["log_time"],
            "missing_timestamp_fields": ["publish_time", "message_header_time"],
        }


def scan_without_summary(
    path: Path,
    top_n: int,
    focus_topics: list[str],
    focus_pattern: re.Pattern[str] | None,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    first: int | None = None
    last: int | None = None
    with path.open("rb") as stream:
        reader = make_reader(stream)
        for _, channel, message in reader.iter_messages(log_time_order=False):
            counts[channel.topic] = counts.get(channel.topic, 0) + 1
            first = message.log_time if first is None else min(first, message.log_time)
            last = message.log_time if last is None else max(last, message.log_time)
    duration_ns = max(0, (last or 0) - (first or 0))
    topics = []
    for topic, count in counts.items():
        frequency_hz = count / (duration_ns / 1_000_000_000) if duration_ns else None
        topics.append({"topic": topic, "message_count": count, "frequency_hz": frequency_hz})
    topics.sort(key=lambda item: item["message_count"], reverse=True)
    focus = [topic for topic in topics if is_focus_topic(topic["topic"], focus_topics, focus_pattern)]
    ordinary = [topic for topic in topics if topic not in focus]
    return {
        "recording": str(path),
        "readable": True,
        "primary_clock": "log_time",
        "time_range_ns": [first, last],
        "duration_s": round(duration_ns / 1_000_000_000, 3),
        "message_count": sum(counts.values()),
        "channel_count": len(counts),
        "schema_count": None,
        "topics": ordinary[:top_n],
        "topics_truncated": len(ordinary) > top_n,
        "focus_topics": focus,
        "missing_focus_topics": [
            topic for topic in focus_topics if topic not in {item["topic"] for item in topics}
        ],
        "timestamp_fields": ["log_time"],
        "missing_timestamp_fields": ["publish_time", "message_header_time"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--focus-topic", action="append", default=[])
    parser.add_argument("--focus-regex")
    args = parser.parse_args()
    if args.top <= 0:
        parser.error("--top must be positive")
    try:
        focus_pattern = re.compile(args.focus_regex) if args.focus_regex else None
        started = time.monotonic()
        cache_dir = os.environ.get("MCAP_CACHE_DIR")
        cache_file = None
        if cache_dir:
            stat = args.recording.stat()
            key = hashlib.sha1(
                f"{args.recording.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|"
                f"{args.top}|{sorted(args.focus_topic)}|{args.focus_regex}".encode()
            ).hexdigest()
            cache_file = Path(cache_dir) / f"inventory-{key}.json"
            if cache_file.is_file():
                result = json.loads(cache_file.read_text())
                result["timing"] = {"total_secs": round(time.monotonic() - started, 3), "cache": "hit"}
                print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
                return 0
        result = build_inventory(args.recording, args.top, args.focus_topic, focus_pattern)
        result["timing"] = {"total_secs": round(time.monotonic() - started, 3)}
        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
            result["timing"]["cache"] = "miss-stored"
    except Exception as exc:
        print(json.dumps({"recording": str(args.recording), "readable": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
