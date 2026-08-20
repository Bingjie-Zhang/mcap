#!/usr/bin/env python3
"""Shared MCAP helpers: one dotted_get, one streaming reader, one clock model.

Every script in this skill must import these instead of re-implementing them,
so field-path semantics and time semantics stay identical across tools.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator, Optional

CLOCKS = ("log_time", "publish_time", "header_time")


def _key_variants(key: str):
    """Yield the key plus snake_case<->camelCase variants.

    mcap CLI decodes protobuf via protojson, which emits camelCase field
    names, while source code and users write snake_case — accept both.
    """
    yield key
    import re as _re
    camel = _re.sub(r"_([a-z0-9])", lambda m: m.group(1).upper(), key)
    if camel != key:
        yield camel
    snake = _re.sub(r"(?<!^)([A-Z])", lambda m: "_" + m.group(1).lower(), key).lower()
    if snake != key:
        yield snake


def dotted_get(value: Any, path: str) -> Any:
    """Read a dotted path, accepting data.foo or foo, snake_case or camelCase.

    Canonical implementation: mcap_slice, mcap_event_locator and
    mcap_frame_correlation must all resolve paths identically.
    """
    candidates = [path]
    if not path.startswith("data."):
        candidates.append(f"data.{path}")
    for candidate in candidates:
        current = value
        for key in candidate.split("."):
            if not isinstance(current, dict):
                break
            for variant in _key_variants(key):
                if variant in current:
                    current = current[variant]
                    break
            else:
                break
        else:
            return current
    return None


def parse_field_spec(spec: str) -> tuple[str, str]:
    topic, separator, field = spec.partition(":")
    if not separator or not topic or not field:
        raise ValueError(f"field must be TOPIC:PATH, got {spec!r}")
    return topic, field


def record_time(record: dict[str, Any], clock: str, header_time_field: Optional[str] = None) -> Optional[float]:
    """Return the record's time in seconds on the requested clock, or None."""
    if clock == "log_time":
        raw = record.get("log_time")
    elif clock == "publish_time":
        raw = record.get("publish_time")
    elif clock == "header_time":
        raw = dotted_get(record, header_time_field or "data.header.timestamp")
    else:
        raise ValueError(f"unknown clock {clock!r}; expected one of {CLOCKS}")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def recording_bounds(path: Path) -> tuple[float, float]:
    """Recording [start, end] in seconds on log_time."""
    from mcap.reader import make_reader

    with path.open("rb") as stream:
        reader = make_reader(stream)
        summary = reader.get_summary()
        if summary is not None and summary.statistics is not None:
            stats = summary.statistics
            return stats.message_start_time / 1e9, stats.message_end_time / 1e9
        first: Optional[int] = None
        last: Optional[int] = None
        for _, _, message in reader.iter_messages(log_time_order=False):
            first = message.log_time if first is None else min(first, message.log_time)
            last = message.log_time if last is None else max(last, message.log_time)
    if first is None or last is None:
        raise ValueError("recording has no messages")
    return first / 1e9, last / 1e9


def _json_topics(path: Path, topics: list[str]) -> Optional[bool]:
    """True if every requested topic is json-encoded (in-process decodable).

    None when the summary is unavailable and we cannot tell cheaply.
    """
    from mcap.reader import make_reader

    with path.open("rb") as stream:
        reader = make_reader(stream)
        summary = reader.get_summary()
        if summary is None:
            return None
        encodings = {c.topic: c.message_encoding for c in summary.channels.values()}
        known = [t for t in topics if t in encodings]
        if not known:
            return None
        return all(encodings[t] == "json" for t in known)


def _stream_inprocess(path: Path, topics: list[str], start_secs: Optional[float], end_secs: Optional[float]) -> Iterator[dict[str, Any]]:
    from mcap.reader import make_reader

    # iter_messages 的 end_time 是排他语义，且 float 秒->ns 往返有 ±256ns 级误差：
    # start 向下留 1µs 余量、end 向上留 1µs 并 +1 消除排他，由 read_records 的
    # 含 epsilon 后置过滤裁掉多余消息 —— 恰在窗口边界的消息（含录制末帧）不再丢失。
    start_ns = None if start_secs is None else max(0, int(start_secs * 1e9) - 1_000)
    end_ns = None if end_secs is None else int(end_secs * 1e9) + 1_001
    with path.open("rb") as stream:
        reader = make_reader(stream)
        for _, channel, message in reader.iter_messages(
            topics=topics, start_time=start_ns, end_time=end_ns, log_time_order=True
        ):
            try:
                data = json.loads(message.data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                yield {"topic": channel.topic, "log_time": message.log_time / 1e9,
                       "publish_time": message.publish_time / 1e9, "data": None,
                       "decode_error": True}
                continue
            yield {"topic": channel.topic, "log_time": message.log_time / 1e9,
                   "publish_time": message.publish_time / 1e9, "sequence": message.sequence,
                   "data": data}


def _stream_cli(path: Path, topics: list[str], start_secs: Optional[float], end_secs: Optional[float],
                timeout_secs: float) -> Iterator[dict[str, Any]]:
    """Stream `mcap cat --json` line by line; never buffers the whole output."""
    command = ["mcap", "cat", str(path), "--json", "--topics", ",".join(topics)]
    if start_secs is not None:
        command.extend(["--start-secs", str(math.floor(start_secs))])
    if end_secs is not None:
        command.extend(["--end-secs", str(math.ceil(end_secs))])
    deadline = time.monotonic() + timeout_secs
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.monotonic() > deadline:
                raise TimeoutError(f"mcap cat exceeded {timeout_secs}s")
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                yield {"topic": None, "log_time": None, "data": None, "decode_error": True}
                continue
            yield record
        proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        if proc.returncode != 0:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(stderr.strip() or "mcap cat failed")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def read_records(
    recording: Path,
    topics: list[str],
    start_secs: Optional[float],
    end_secs: Optional[float],
    max_records: int = 200_000,
    max_bytes: int = 64 * 1024 * 1024,
    timeout_secs: float = 120.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read decoded records sorted by log_time, with hard resource limits.

    Returns (records, status). status["status"] is "complete" or "partial";
    a partial read reports which limit was hit instead of silently truncating.
    Prefers in-process decoding for json-encoded topics; falls back to a
    streaming `mcap cat` subprocess for other encodings.
    """
    started = time.monotonic()
    if start_secs is not None and end_secs is not None and end_secs <= start_secs:
        raise ValueError("end-secs must be greater than start-secs")
    json_only = _json_topics(recording, topics)
    if json_only:
        source: Iterator[dict[str, Any]] = _stream_inprocess(recording, topics, start_secs, end_secs)
        reader_used = "python-mcap"
    else:
        if shutil.which("mcap") is None:
            if json_only is False:
                raise RuntimeError("topics are not json-encoded and the `mcap` CLI is not installed")
            source = _stream_inprocess(recording, topics, start_secs, end_secs)
            reader_used = "python-mcap"
        else:
            source = _stream_cli(recording, topics, start_secs, end_secs, timeout_secs)
            reader_used = "mcap-cli"

    records: list[dict[str, Any]] = []
    decode_errors = 0
    consumed_bytes = 0
    limit_hit: Optional[str] = None
    deadline = time.monotonic() + timeout_secs
    for record in source:
        if record.get("decode_error"):
            decode_errors += 1
            continue
        log_time = record.get("log_time")
        if log_time is None:
            decode_errors += 1
            continue
        log_time = float(log_time)
        if start_secs is not None and log_time < start_secs - 1e-6:
            continue
        if end_secs is not None and log_time > end_secs + 1e-6:
            continue
        consumed_bytes += len(json.dumps(record.get("data"), ensure_ascii=True, separators=(",", ":")))
        records.append(record)
        if len(records) >= max_records:
            limit_hit = f"max_records={max_records}"
            break
        if consumed_bytes >= max_bytes:
            limit_hit = f"max_bytes={max_bytes}"
            break
        if time.monotonic() > deadline:
            limit_hit = f"timeout_secs={timeout_secs}"
            break
    records.sort(key=lambda r: float(r["log_time"]))
    status = {
        "status": "partial" if limit_hit else "complete",
        "limit_hit": limit_hit,
        "reader": reader_used,
        "records": len(records),
        "decode_errors": decode_errors,
        "approx_payload_bytes": consumed_bytes,
        "read_secs": round(time.monotonic() - started, 3),
    }
    return records, status


def add_limit_args(parser: Any) -> None:
    parser.add_argument("--max-records", type=int, default=200_000)
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--timeout-secs", type=float, default=120.0)
