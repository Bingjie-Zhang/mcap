#!/usr/bin/env python3
"""Generate negative cases apa-016 .. apa-020 for the APA manager training set.

All five are "looks suspicious, but nothing is wrong" recordings: the correct
answer is to report NO fault. Each expected_manager entry has anchor=null, a
note, and negative_checks listing the predicates most likely to misfire on the
scenario (all must come back all_good / no_transition from
mcap_event_locator.py with the stated debounce).

Deterministic (SEED fixed); writes only apa-016..apa-020 files into
recordings/, cases/, expected_manager/. Existing files are not touched.
Topic names, payload helpers and file formats are imported from
generate_extension.py so the encoding stays byte-compatible with apa-010..015.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from generate_extension import (  # noqa: E402
    CASES,
    EXPECTED,
    RECORDINGS,
    TOPICS,
    Record,
    control,
    decision,
    obstacle,
    planning,
    slot,
    state,
    vehicle,
    write_mcap,
)

SEED = 20260822
RNG = random.Random(SEED)


def ws_default(i: int, complete_at: int) -> int:
    """2 (init) -> 3 (plan) -> 5 (parking) -> 8 (finished) at complete_at."""
    if i < 2:
        return 2
    if i == 2:
        return 3
    return 5 if i < complete_at else 8


def baseline(duration: int, complete_at: int, *, skip: set[str] = frozenset()) -> list[Record]:
    """1Hz healthy rows for all topics, parking completes at complete_at."""
    out: list[Record] = []
    for i in range(duration + 1):
        t = float(i)
        rows = {
            "state": state(t, i, ws_default(i, complete_at)),
            "decision": decision(t, i, stop_distance=max(0.5, 1.3 - 0.05 * i)),
            "planning": planning(t, i, clearance=max(0.3, 0.58 - 0.01 * i)),
            "control": control(t, i),
            "obstacle": obstacle(t, i, x=1.2 + 0.02 * i),
            "slot": slot(t, i),
            "vehicle": vehicle(t, i) if i < complete_at
            else vehicle(t, i, gear="P", velocity=0.0),
        }
        for key, data in rows.items():
            if key in skip:
                continue
            out.append((t, TOPICS[key], data, 0.0))
    return out


def check(topic_key: str, field: str, predicate: str, expect: str, *,
          debounce: int = 2, window: list[float] | None = None,
          tolerance: str | None = None, note: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "topic": TOPICS[topic_key], "field": field, "predicate": predicate,
        "debounce_count": debounce, "expect": expect,
    }
    if window is not None:
        item["window_secs"] = window
    if tolerance is not None:
        item["tolerance"] = tolerance
    if note is not None:
        item["note"] = note
    return item


# ---------------------------------------------------------------- apa-016

def case_016() -> dict[str, Any]:
    duration, complete_at = 12, 11
    records = baseline(duration, complete_at, skip={"decision"})

    # decision at 2 Hz: stop_distance hugs the 0.15 finish threshold from above
    # (0.16 .. 0.249) for the whole recording but never crosses it.
    for k in range(0, 25):
        t = k * 0.5
        sd = round(0.16 + 0.09 * RNG.random(), 3)
        if abs(t - 4.5) < 1e-9:
            sd = 0.161  # closest legal approach, still >= 0.16
        if abs(t - 9.0) < 1e-9:
            sd = 0.249
        sd = min(max(sd, 0.16), 0.249)
        records.append((t, TOPICS["decision"],
                        decision(t, k, stop_distance=sd,
                                 debug=f"near_gate sd={sd:.3f} gate=0.15"), 0.0))

    expected = {
        "clock": "log_time",
        "anchor": None,
        "note": "near_threshold_no_cross",
        "route": [],
        "negative_checks": [
            check("decision", "stop_distance", "lt 0.15", "all_good",
                  note="全程0.16~0.249贴阈值波动，最低0.160，从不越过0.15"),
            check("state", "exit_reason", "ne 0", "all_good"),
            check("decision", "collision_result", "regex Close", "all_good"),
        ],
        "confidence": "high",
    }
    return {
        "case_id": "apa-016-near-threshold-no-cross",
        "symptom": "stop_distance全程在0.16~0.25之间紧贴0.15完成门限波动，看似随时要触发停车，实际从未越界，泊车在11.0s正常完成。",
        "depth": "quick", "recording": "apa-016-near-threshold-no-cross.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "negative-near-threshold",
                   "initiating_cause": "none (normal tight-clearance approach)",
                   "necessary_condition": None, "amplifier": None,
                   "consequence": "clean completion, work_state=8 at 11.0s, exit_reason stays 0",
                   "observability_gap": None},
    }


# ---------------------------------------------------------------- apa-017

def case_017() -> dict[str, Any]:
    duration, complete_at = 12, 11
    records = baseline(duration, complete_at, skip={"planning"})

    # trajectory_num: 3 -> 0 (single frame at 4.0s, normal replan) -> 2 -> 1.
    for i in range(duration + 1):
        t = float(i)
        if i < 4:
            pl = planning(t, i, count=3, clearance=round(0.58 - 0.01 * i, 3))
        elif i == 4:
            pl = planning(t, i, count=0, clearance=round(0.58 - 0.01 * i, 3), replan=True)
        elif i <= 8:
            pl = planning(t, i, count=2, clearance=round(0.58 - 0.01 * i, 3))
        else:
            pl = planning(t, i, count=1, clearance=round(0.58 - 0.01 * i, 3))
        records.append((t, TOPICS["planning"], pl, 0.0))

    expected = {
        "clock": "log_time",
        "anchor": None,
        "note": "single_replan_transient_zero",
        "route": [],
        "negative_checks": [
            check("planning", "trajectory_num", "eq 0", "all_good",
                  note="4.0s重规划瞬时归零仅1帧，debounce=2应滤掉；这是正常单次重规划，不是apa-004类故障"),
            check("decision", "stop_distance", "lt 0.15", "all_good"),
            check("state", "exit_reason", "ne 0", "all_good"),
        ],
        "debounce_note": {
            "topic": TOPICS["planning"], "field": "trajectory_num", "predicate": "eq 0",
            "debounce_1": {"status": "transition_found", "first_bad": 4.0},
            "debounce_2": {"status": "all_good"},
            "note": "debounce=1能看到4.0s那一帧（证明瞬时事件真实存在），debounce=2不报——瞬时归零属正常重规划",
        },
        "confidence": "high",
    }
    return {
        "case_id": "apa-017-single-replan-normal",
        "symptom": "泊入中段trajectory_num出现3→0→2→1，其中0仅持续一帧（4.0s正常单次重规划），随后按序完成泊车。",
        "depth": "quick", "recording": "apa-017-single-replan-normal.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "negative-single-replan",
                   "initiating_cause": "one normal mid-maneuver replan (not a fault)",
                   "necessary_condition": None, "amplifier": None,
                   "consequence": "trajectory regenerates within one cycle; completion at 11.0s, exit_reason stays 0",
                   "observability_gap": None},
    }


# ---------------------------------------------------------------- apa-018

def case_018() -> dict[str, Any]:
    duration, complete_at = 12, 11
    records = baseline(duration, complete_at, skip={"decision", "obstacle", "vehicle"})

    # Pedestrian walks through the path: obstacle distance dips to 0.5m at
    # 6.5s then recovers; obstacle stays valid the whole time.
    ped_x = {5.0: 1.6, 5.5: 1.1, 6.0: 0.7, 6.5: 0.5, 7.0: 0.55,
             7.5: 0.9, 8.0: 1.4, 8.5: 2.0}
    for k in range(0, 25):  # obstacle + decision at 2 Hz
        t = k * 0.5
        x = ped_x.get(t, 2.0)
        records.append((t, TOPICS["obstacle"],
                        obstacle(t, k, object_id=812, x=x, y=0.1, confidence=0.95), 0.0))
        sd = round(min(max(0.5, 1.3 - 0.05 * t), x), 3)
        in_pass = 5.0 <= t <= 8.5
        records.append((t, TOPICS["decision"],
                        decision(t, k, stop_distance=sd,
                                 debug="pedestrian passing, slow down" if in_pass
                                 else "collision=open"), 0.0))

    for i in range(duration + 1):  # vehicle at 1 Hz: slow down, never stop
        t = float(i)
        if i >= complete_at:
            records.append((t, TOPICS["vehicle"], vehicle(t, i, gear="P", velocity=0.0), 0.0))
        elif i in (6, 7):
            records.append((t, TOPICS["vehicle"], vehicle(t, i, velocity=-0.08), 0.0))
        elif i == 8:
            records.append((t, TOPICS["vehicle"], vehicle(t, i, velocity=-0.18), 0.0))
        else:
            records.append((t, TOPICS["vehicle"], vehicle(t, i), 0.0))

    expected = {
        "clock": "log_time",
        "anchor": None,
        "note": "brief_obstacle_pass_no_fault",
        "route": [],
        "negative_checks": [
            check("decision", "stop_distance", "lt 0.15", "all_good",
                  note="行人路过时stop_distance最低0.5m，减速即可，从未低于0.15停车线"),
            check("decision", "collision_result", "regex Close", "all_good",
                  note="决策全程保持Open，无碰撞停车输出"),
            check("obstacle", "valid", "eq false", "all_good"),
        ],
        "confidence": "high",
    }
    return {
        "case_id": "apa-018-brief-obstacle-pass",
        "symptom": "6.5s前后行人正常路过，障碍距离短暂降到0.5m又离开；决策减速（车速-0.28→-0.08）未停车，11.0s完成泊车。",
        "depth": "quick", "recording": "apa-018-brief-obstacle-pass.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "negative-transient-obstacle",
                   "initiating_cause": "pedestrian crossing the path (normal environment event)",
                   "necessary_condition": None, "amplifier": None,
                   "consequence": "temporary slowdown only; completion at 11.0s, exit_reason stays 0",
                   "observability_gap": None},
    }


# ---------------------------------------------------------------- apa-019

def case_019() -> dict[str, Any]:
    duration, complete_at = 18, 17  # ~50% longer than the usual 12s recordings
    records: list[Record] = []
    for i in range(duration + 1):
        t = float(i)
        ws = 2 if i < 3 else (3 if i == 3 else (5 if i < complete_at else 8))
        records.append((t, TOPICS["state"], state(t, i, ws), 0.0))
        records.append((t, TOPICS["decision"],
                        decision(t, i, stop_distance=round(max(0.18, 1.3 - 0.07 * i), 3),
                                 debug="slow but healthy"), 0.0))
        records.append((t, TOPICS["planning"],
                        planning(t, i, clearance=round(max(0.3, 0.58 - 0.015 * i), 3)), 0.0))
        records.append((t, TOPICS["control"], control(t, i, lat_err=0.03, lon_err=0.04), 0.0))
        records.append((t, TOPICS["obstacle"], obstacle(t, i, x=1.4 + 0.01 * i), 0.0))
        records.append((t, TOPICS["slot"], slot(t, i), 0.0))
        if i < complete_at:
            records.append((t, TOPICS["vehicle"], vehicle(t, i, velocity=-0.12), 0.0))
        else:
            records.append((t, TOPICS["vehicle"], vehicle(t, i, gear="P", velocity=0.0), 0.0))

    expected = {
        "clock": "log_time",
        "anchor": None,
        "note": "slow_but_normal",
        "route": [],
        "negative_checks": [
            check("vehicle", "velocity_mps", "gte -0.05", "all_good",
                  window=[0.0, 16.5],
                  note="机动窗口内车速恒为-0.12m/s（慢但在动），不是停滞；窗口截到16.5s排除入位后正常的P挡v=0帧"),
            check("decision", "stop_distance", "lt 0.15", "all_good"),
            check("state", "exit_reason", "ne 0", "all_good"),
        ],
        "confidence": "high",
    }
    return {
        "case_id": "apa-019-slow-but-normal",
        "symptom": "全程车速仅-0.12m/s，泊入耗时17s（比常规12s长约50%），看似卡住，但每个信号健康、状态机按2→3→5→8正常走完。",
        "depth": "quick", "recording": "apa-019-slow-but-normal.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "negative-slow-normal",
                   "initiating_cause": "conservative low-speed profile (not a fault)",
                   "necessary_condition": None, "amplifier": None,
                   "consequence": "completion at 17.0s with all signals in range, exit_reason stays 0",
                   "observability_gap": None},
    }


# ---------------------------------------------------------------- apa-020

def case_020() -> dict[str, Any]:
    duration, complete_at = 12, 11

    def noisy(base: float) -> float:
        return round(base * (1 + 0.05 * RNG.uniform(-1.0, 1.0)), 3)

    records: list[Record] = []
    for i in range(duration + 1):
        t = float(i)
        records.append((t, TOPICS["state"], state(t, i, ws_default(i, complete_at)), 0.0))
        records.append((t, TOPICS["decision"],
                        decision(t, i, stop_distance=noisy(max(0.4, 1.3 - 0.08 * i)),
                                 debug="sensor noise visible, all within limits"), 0.0))
        records.append((t, TOPICS["planning"],
                        planning(t, i, clearance=noisy(0.55)), 0.0))
        te = noisy(0.05)
        records.append((t, TOPICS["control"],
                        control(t, i, lat_err=noisy(0.04), lon_err=noisy(0.05),
                                tracking_error=te), 0.0))
        records.append((t, TOPICS["obstacle"], obstacle(t, i, x=noisy(1.5), y=0.12), 0.0))
        records.append((t, TOPICS["slot"], slot(t, i), 0.0))
        if i < complete_at:
            records.append((t, TOPICS["vehicle"], vehicle(t, i, velocity=noisy(-0.28)), 0.0))
        else:
            records.append((t, TOPICS["vehicle"], vehicle(t, i, gear="P", velocity=0.0), 0.0))

    expected = {
        "clock": "log_time",
        "anchor": None,
        "note": "noisy_but_valid",
        "route": [],
        "negative_checks": [
            check("decision", "stop_distance", "lt 0.15", "all_good",
                  note="±5%乘性噪声下最低值仍在0.38以上，远离0.15阈值"),
            check("control", "tracking_error", "gt 0.3", "all_good",
                  note="tracking_error基线0.05±5%，噪声不越0.3阈值"),
            check("control", "tracking_error", "changed", "no_transition",
                  tolerance="auto", debounce=2,
                  note="changed+tolerance=auto+debounce=2下纯测量噪声不应报跳变"),
        ],
        "confidence": "high",
    }
    return {
        "case_id": "apa-020-noisy-but-valid",
        "symptom": "各数值信号叠加约±5%测量噪声，曲线毛糙看似异常，但没有任何信号越过阈值，11.0s正常完成泊车。",
        "depth": "quick", "recording": "apa-020-noisy-but-valid.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "negative-measurement-noise",
                   "initiating_cause": "sensor measurement noise (~5%), not a fault",
                   "necessary_condition": None, "amplifier": None,
                   "consequence": "all thresholds respected; completion at 11.0s, exit_reason stays 0",
                   "observability_gap": None},
    }


def main() -> None:
    for directory in (RECORDINGS, CASES, EXPECTED):
        directory.mkdir(parents=True, exist_ok=True)
    cases = [case_016(), case_017(), case_018(), case_019(), case_020()]
    for case in cases:
        write_mcap(case["recording"], case["records"])
        case_view = {k: v for k, v in case.items() if k != "records"}
        case_view["recording"] = f"recordings/{case['recording']}"
        case_view["record_count"] = len(case["records"])
        (CASES / f"{case['case_id']}.json").write_text(
            json.dumps(case_view, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (EXPECTED / f"{case['case_id']}.json").write_text(
            json.dumps(case["expected"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{case['case_id']}: {len(case['records'])} records")


if __name__ == "__main__":
    main()
