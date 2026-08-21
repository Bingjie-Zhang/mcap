#!/usr/bin/env python3
"""Generate extension cases apa-010 .. apa-015 for the APA manager training set.

Deterministic (SEED fixed); writes only apa-010..apa-015 files into
recordings/, cases/, expected_manager/. Existing apa-001..009 files are not
touched. Topic names, payload helpers and file formats follow
generate_dataset.py (apa_manager_training_20260821).
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from mcap.writer import Writer

SEED = 20260821
RNG = random.Random(SEED)

ROOT = Path(__file__).resolve().parent
RECORDINGS = ROOT / "recordings"
CASES = ROOT / "cases"
EXPECTED = ROOT / "expected_manager"

SCHEMA = b'{"type":"object"}'

TOPICS = {
    "state": "/functions/parking_pnc/apa_state",
    "decision": "/functions/parking_pnc/apa_decision",
    "planning": "/functions/parking_pnc/apa_planning_trajectory",
    "control": "/functions/parking_pnc/apa_control_state",
    "obstacle": "/functions/perception/obstacle_pk",
    "slot": "/functions/perception/parking_slot_info",
    "vehicle": "/sensor/chassis/vehicle_status",
}

Record = tuple[float, str, dict[str, Any], float]


def hdr(t: float, seq: int) -> dict[str, Any]:
    return {"timestamp": round(t, 3), "seq": seq}


def state(t: float, seq: int, work_state: int, exit_reason: int = 0, *, valid: bool = True,
          mode: str = "APA_IN", mirror_state: int = 2) -> dict[str, Any]:
    return {
        "header": hdr(t, seq),
        "work_state": work_state,
        "exit_reason": exit_reason,
        "mode": mode,
        "validity": valid,
        "mirror_state": mirror_state,
    }


def decision(t: float, seq: int, *, result: str = "Open", stop_distance: float = 1.5,
             stop_reason: str = "NONE", parking_status: int = 4, planning_status: int = 5,
             errcode: int = 0, debug: str = "collision=open",
             header_offset: float = 0.0) -> dict[str, Any]:
    return {
        "header": hdr(t + header_offset, seq),
        "seq": seq,
        "apa_status": {
            "parking_working_status": parking_status,
            "per_planning_status": planning_status,
        },
        "collision_result": result,
        "stop_distance": round(stop_distance, 3),
        "stop_reason": stop_reason,
        "errcode": errcode,
        "decDebugStr": debug,
    }


def planning(t: float, seq: int, *, count: int = 3, slot_id: int = 42,
             valid: bool = True, clearance: float = 0.55, replan: bool = False) -> dict[str, Any]:
    target_slot = {
        "slot_id": slot_id, "type": "PARALLEL", "depth_m": 5.1, "width_m": 2.35,
        "yaw_rad": 1.56, "valid": valid,
    }
    traj = []
    for i in range(count):
        traj.append({
            "id": 100 + i,
            "valid": valid,
            "trajectory": [
                {"x": round(1.2 - i * 0.05, 2), "y": round(-0.1 - i * 0.2, 2),
                 "yaw": -1.55, "v_mps": -0.28, "driving_direction": -1},
                {"x": round(0.8 - i * 0.05, 2), "y": round(-0.6 - i * 0.2, 2),
                 "yaw": -1.57, "v_mps": -0.18, "driving_direction": -1},
            ],
        })
    return {
        "header": hdr(t, seq),
        "seq": seq,
        "apa_traj": traj,
        "trajectory_num": count,
        "target_slot": target_slot,
        "clearance": round(clearance, 3),
        "replan": replan,
        "validity": valid,
    }


def control(t: float, seq: int, *, control_state: int = 0, lat_err: float = 0.04,
            lon_err: float = 0.05, yaw_err: float = 0.02, spd_err: float = 0.03,
            tracking_error: float | None = None) -> dict[str, Any]:
    if tracking_error is None:
        tracking_error = max(abs(lat_err), abs(lon_err))
    return {
        "header": hdr(t, seq),
        "seq": seq,
        "control_state": control_state,
        "lat_control_state": 2 if control_state == 0 else 4,
        "lon_control_state": 3 if control_state == 0 else 7,
        "control_err": {
            "lat_err": round(lat_err, 3), "lon_err": round(lon_err, 3),
            "yaw_err": round(yaw_err, 3), "spd_err": round(spd_err, 3),
        },
        "tracking_error": round(tracking_error, 3),
    }


def obstacle(t: float, seq: int, *, object_id: int = 701, x: float = 1.2,
             y: float = 0.15, valid: bool = True, frame: str = "vehicle",
             confidence: float = 0.92) -> dict[str, Any]:
    return {
        "header": hdr(t, seq),
        "seq": seq,
        "object_id": object_id,
        "pose": {"x": round(x, 3), "y": round(y, 3), "yaw": 0.02},
        "shape": {"length_m": 0.45, "width_m": 0.32},
        "valid": valid,
        "frame": frame,
        "confidence": round(confidence, 3),
    }


def slot(t: float, seq: int, *, slot_id: int = 42, valid: bool = True,
         width: float = 2.35, depth: float = 5.1) -> dict[str, Any]:
    return {
        "header": hdr(t, seq), "seq": seq, "slot_id": slot_id, "valid": valid,
        "type": "PARALLEL", "width_m": width, "depth_m": depth, "yaw_rad": 1.56,
    }


def vehicle(t: float, seq: int, *, gear: str = "R", velocity: float = -0.28,
            epb: bool = False, validity: bool = True) -> dict[str, Any]:
    return {
        "header": hdr(t, seq), "seq": seq, "gear": gear,
        "velocity_mps": round(velocity, 3), "epb": epb, "validity": validity,
    }


def write_mcap(name: str, records: list[Record]) -> str:
    path = RECORDINGS / name
    with path.open("wb") as stream:
        writer = Writer(stream, compression=0)
        writer.start(profile="apa-manager-training", library="synthetic-source-aligned")
        schema_id = writer.register_schema("apa.synthetic.json", "jsonschema", SCHEMA)
        channels = {
            topic: writer.register_channel(topic, "json", schema_id)
            for topic in sorted({topic for _, topic, _, _ in records})
        }
        ordered = sorted(records, key=lambda r: (r[0], r[1]))
        for seq, (t, topic, data, publish_offset) in enumerate(ordered, start=1):
            log_ns = int(round(t * 1e9))
            pub_ns = int(round((t + publish_offset) * 1e9))
            writer.add_message(channels[topic], log_ns, json.dumps(data, separators=(",", ":")).encode(), pub_ns, seq)
        writer.finish()
    return str(path)


def background(duration: int, *, skip: set[str] = frozenset(),
               ws_of=None) -> list[Record]:
    """1Hz baseline for all topics; per-topic overrides applied by callers."""
    out: list[Record] = []
    for i in range(duration + 1):
        t = float(i)
        if ws_of is not None:
            ws = ws_of(i)
        else:
            ws = 2 if i < 2 else (3 if i == 2 else (5 if i < duration else 8))
        rows = {
            "state": state(t, i, ws),
            "decision": decision(t, i, stop_distance=max(0.5, 1.3 - 0.05 * i)),
            "planning": planning(t, i, clearance=max(0.3, 0.58 - 0.01 * i)),
            "control": control(t, i),
            "obstacle": obstacle(t, i, x=1.2 + 0.02 * i),
            "slot": slot(t, i),
            "vehicle": vehicle(t, i),
        }
        for key, data in rows.items():
            if key in skip:
                continue
            out.append((t, TOPICS[key], data, 0.0))
    return out


# ---------------------------------------------------------------- apa-010

def case_010() -> dict[str, Any]:
    duration = 12
    jitter = [0.13, 0.18, 0.12, 0.19, 0.14, 0.17, 0.13, 0.16, 0.12, 0.18]  # t=3.0..7.5
    stable = [0.12, 0.11, 0.10, 0.10, 0.09, 0.09, 0.08, 0.08, 0.08]        # t=8.0..12.0

    def ws_of(i: int) -> int:
        if i < 2:
            return 2
        if i == 2:
            return 3
        return 5 if i < 10 else 8

    records = background(duration, skip={"decision"}, ws_of=ws_of)

    gate_timer = 0
    prev_active = False
    for k in range(0, 25):  # decision at 2 Hz: t = 0.0 .. 12.0
        t = k * 0.5
        if t < 3.0:
            sd = max(0.5, 1.3 - 0.3 * t)
        elif t < 8.0:
            sd = jitter[int((t - 3.0) / 0.5)]
        else:
            sd = stable[int((t - 8.0) / 0.5)]
        active = sd < 0.15
        gate_timer = min(2000, gate_timer + 500) if (active and prev_active) else (0 if not active else 0)
        prev_active = active
        data = decision(t, k, stop_distance=sd,
                        debug=f"finish_gate={'on' if active else 'off'} timer_ms={gate_timer}")
        data["timer_gate_active"] = active
        data["gate_timer_ms"] = gate_timer
        records.append((t, TOPICS["decision"], data, 0.0))

    expected = {
        "clock": "log_time",
        "anchor": {"topic": TOPICS["state"], "field": "work_state",
                   "predicate": "eq 8", "last_good": 9.0, "first_bad": 10.0},
        "window_secs": [3.0, 10.5],
        "specialist": "apa_decision_analysis",
        "inputs": [{"topic": TOPICS["decision"], "field": "stop_distance", "max_age_ms": 600},
                   {"topic": TOPICS["decision"], "field": "timer_gate_active", "max_age_ms": 600}],
        "flap_check": {"topic": TOPICS["decision"], "field": "timer_gate_active", "min_flips": 10},
        "question": "完成计时门控为何迟至10.0s才满足？stop_distance是否在0.15阈值附近反复进出导致计时清零？",
        "boundary": "decision finish-gate timer -> state transition",
        "confidence": "high",
    }
    return {
        "case_id": "apa-010-timer-gate-jitter",
        "symptom": "车辆早已接近停止，但完成状态迟迟不切，最后才切换。",
        "depth": "standard", "recording": "apa-010-timer-gate-jitter.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "timer-gate-jitter",
                   "initiating_cause": "stop_distance jitters around the 0.15m finish-gate threshold",
                   "necessary_condition": "finish requires 2s continuous gate-active",
                   "amplifier": "timer resets to 0 on every gate exit (5 round trips)",
                   "consequence": "state switch to work_state=8 delayed until 10.0s",
                   "observability_gap": None},
    }


# ---------------------------------------------------------------- apa-011

def case_011() -> dict[str, Any]:
    duration = 12
    bad_obstacle = {4.0, 5.0, 7.0, 8.0, 9.0}
    fail_at = {4.0: 1, 5.0: 2, 6.0: 2, 7.0: 3, 8.0: 4, 9.0: 5, 10.0: 5, 11.0: 5, 12.0: 5}

    def ws_of(i: int) -> int:
        if i < 2:
            return 2
        if i == 2:
            return 3
        return 5 if i < 10 else 7

    records = background(duration, skip={"state", "obstacle", "planning", "decision"}, ws_of=ws_of)
    for i in range(duration + 1):
        t = float(i)
        if t >= 10.0:
            st = state(t, i, 7, 118, mode="ABORT")
        else:
            st = state(t, i, ws_of(i))
        records.append((t, TOPICS["state"], st, 0.0))

        if t in bad_obstacle:
            ob = obstacle(t, i, x=1.2, valid=False, confidence=0.18)
        else:
            ob = obstacle(t, i, x=1.2 + 0.02 * i)
        records.append((t, TOPICS["obstacle"], ob, 0.0))

        fails = fail_at.get(t, 0)
        if t >= 4.0 and t != 6.0:
            pl = planning(t, i, count=0, clearance=0.0, replan=True)
        elif t == 6.0:
            pl = planning(t, i, count=3, clearance=0.5, replan=True)
        else:
            pl = planning(t, i, clearance=max(0.3, 0.58 - 0.01 * i))
        pl["replan_fail_count"] = fails
        records.append((t, TOPICS["planning"], pl, 0.0))

        if t >= 9.0:
            de = decision(t, i, result="Open", stop_distance=1.0, planning_status=4,
                          errcode=118, debug="replan fail count reached threshold")
        else:
            de = decision(t, i, stop_distance=max(0.5, 1.3 - 0.05 * i))
        records.append((t, TOPICS["decision"], de, 0.0))

    expected = {
        "clock": "log_time",
        "anchor": {"topic": TOPICS["state"], "field": "exit_reason",
                   "predicate": "eq 118", "last_good": 9.0, "first_bad": 10.0},
        "window_secs": [3.5, 10.5],
        "specialist": "apa_planning_analysis",
        "inputs": [{"topic": TOPICS["planning"], "field": "replan_fail_count", "max_age_ms": 1100},
                   {"topic": TOPICS["obstacle"], "field": "valid", "max_age_ms": 1100}],
        "trace_path": [TOPICS["state"], TOPICS["planning"], TOPICS["obstacle"]],
        "question": "ABORT前replan_fail_count为何持续上升？规划失败时段是否与障碍物valid=false间歇异常对齐？",
        "boundary": "perception validity -> planning replan -> state ABORT",
        "confidence": "high",
    }
    return {
        "case_id": "apa-011-cascade-perception-replan-abort",
        "symptom": "泊入过程中规划反复失败，最终APA进入ABORT。",
        "depth": "standard", "recording": "apa-011-cascade-perception-replan-abort.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "cascade-perception-planning-abort",
                   "initiating_cause": "intermittent obstacle valid=false (4,5,7,8,9s)",
                   "necessary_condition": "planning aborts after 5 accumulated replan failures",
                   "amplifier": "fail counter never resets on brief recovery at 6.0s",
                   "consequence": "state ABORT with exit_reason=118 at 10.0s",
                   "observability_gap": None},
    }


# ---------------------------------------------------------------- apa-012

def case_012() -> dict[str, Any]:
    duration = 12

    def ws_of(i: int) -> int:
        if i < 2:
            return 2
        if i == 2:
            return 3
        return 5 if i < duration else 8

    records = background(duration, skip={"obstacle", "decision"}, ws_of=ws_of)

    obstacle_times: list[float] = []
    seq = 0
    t = 0.0
    while t < 6.0 - 1e-9:  # 10 Hz
        obstacle_times.append(round(t, 1))
        t += 0.1
    t = 6.0
    while t <= 12.0 + 1e-9:  # 2 Hz
        obstacle_times.append(round(t, 1))
        t += 0.5
    for seq, ot in enumerate(obstacle_times):
        records.append((ot, TOPICS["obstacle"], obstacle(ot, seq, x=1.2 + 0.005 * seq), 0.0))

    for k in range(0, 121):  # decision at 10 Hz: t = 0.0 .. 12.0
        dt = round(k * 0.1, 1)
        latest = max(ot for ot in obstacle_times if ot <= dt + 1e-9)
        age_ms = round((dt - latest) * 1000.0, 1)
        stale = age_ms > 150.0
        data = decision(dt, k, stop_distance=1.0,
                        debug=f"obstacle_age_ms={age_ms} stale={str(stale).lower()}")
        data["obstacle_input_age_ms"] = age_ms
        data["stale_frame"] = stale
        records.append((dt, TOPICS["decision"], data, 0.0))

    expected = {
        "clock": "log_time",
        "anchor": {"topic": TOPICS["decision"], "field": "obstacle_input_age_ms",
                   "predicate": "gt 150", "last_good": 6.1, "first_bad": 6.2},
        "window_secs": [5.5, 12.0],
        "specialist": "apa_perception_analysis",
        "inputs": [{"topic": TOPICS["obstacle"], "field": "header.timestamp", "max_age_ms": 150}],
        "stale_check": {"source_topic": TOPICS["obstacle"], "target_topic": TOPICS["decision"],
                        "max_age_ms": 150, "min_stale": 30},
        "question": "obstacle_pk是否从6.0s起由10Hz降到2Hz（未断流），使决策消费的旧帧比例上升？",
        "boundary": "obstacle publish rate -> decision input freshness",
        "confidence": "high",
    }
    return {
        "case_id": "apa-012-topic-rate-drop",
        "symptom": "决策侧障碍物输入帧龄间歇性超标，旧帧占比明显上升，但上游并未断流。",
        "depth": "standard", "recording": "apa-012-topic-rate-drop.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "input-rate-drop",
                   "initiating_cause": "obstacle_pk rate drops 10Hz -> 2Hz at 6.0s",
                   "necessary_condition": "decision consumes latest-available frame at 10Hz",
                   "amplifier": "no timeout: stale frames silently reused",
                   "consequence": "3 of 5 decision cycles per 0.5s window use frames older than 150ms",
                   "observability_gap": None},
    }


# ---------------------------------------------------------------- apa-013

def case_013() -> dict[str, Any]:
    duration = 25

    def ws_of(i: int) -> int:
        if i < 2:
            return 2
        if i == 2:
            return 3
        return 5 if i < 24 else 7

    records = background(duration, skip={"state", "control", "decision"}, ws_of=ws_of)
    for i in range(duration + 1):
        t = float(i)
        if i >= 24:
            st = state(t, i, 7, 106)
        else:
            st = state(t, i, ws_of(i))
        records.append((t, TOPICS["state"], st, 0.0))

        err = 0.05 if t < 2.0 else round(0.05 + 0.013 * (t - 2.0), 3)
        ctl = control(t, i, control_state=0 if err <= 0.3 else 6,
                      lat_err=err, lon_err=0.05, tracking_error=err)
        records.append((t, TOPICS["control"], ctl, 0.0))
        records.append((t, TOPICS["decision"], decision(t, i, stop_distance=max(0.5, 1.3 - 0.03 * i)), 0.0))

    expected = {
        "clock": "log_time",
        "anchor": {"topic": TOPICS["control"], "field": "tracking_error",
                   "predicate": "gt 0.3", "last_good": 21.0, "first_bad": 22.0},
        "window_secs": [19.0, 24.5],
        "specialist": "apa_control_analysis",
        "inputs": [{"topic": TOPICS["planning"], "field": "apa_traj", "max_age_ms": 1100},
                   {"topic": TOPICS["vehicle"], "field": "velocity_mps", "max_age_ms": 1100}],
        "question": "tracking_error是从2.0s起以约0.013m/s线性漂移20s后越过0.3，还是存在突变点？",
        "boundary": "gradual drift inside control tracking (no step change)",
        "confidence": "medium",
    }
    return {
        "case_id": "apa-013-gradual-drift",
        "symptom": "跟踪误差无突变，缓慢增大约20秒后越过0.3阈值并退出。",
        "depth": "standard", "recording": "apa-013-gradual-drift.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "gradual-drift",
                   "initiating_cause": "slow accumulating bias from 2.0s (0.013 m/s)",
                   "necessary_condition": "threshold check is instantaneous, no trend alarm",
                   "amplifier": None,
                   "consequence": "threshold crossed at 22.0s, exit_reason=106 at 24.0s",
                   "observability_gap": "no per-cycle bias estimate recorded"},
    }


# ---------------------------------------------------------------- apa-014

def case_014() -> dict[str, Any]:
    duration = 9
    records: list[Record] = []
    for i in range(duration + 1):
        t = float(i)
        if i < 2:
            st = state(t, i, 2)
        elif i == 2:
            st = state(t, i, 3)
        elif i < 7:
            st = state(t, i, 5, mode="PARKING")
        else:
            st = state(t, i, 4, 0, mode="USER_CANCELLED")
            st["user_cancel"] = True
        records.append((t, TOPICS["state"], st, 0.0))
        records.append((t, TOPICS["decision"],
                        decision(t, i, stop_distance=round(1.3 - 0.05 * i, 3)), 0.0))
        records.append((t, TOPICS["planning"],
                        planning(t, i, clearance=round(0.58 - 0.01 * i, 3)), 0.0))
        records.append((t, TOPICS["control"], control(t, i), 0.0))
        records.append((t, TOPICS["obstacle"], obstacle(t, i, x=1.2 + 0.02 * i), 0.0))
        records.append((t, TOPICS["slot"], slot(t, i), 0.0))
        if i < 7:
            records.append((t, TOPICS["vehicle"], vehicle(t, i), 0.0))
        else:
            records.append((t, TOPICS["vehicle"], vehicle(t, i, gear="P", velocity=0.0), 0.0))

    expected = {
        "clock": "log_time",
        "anchor": None,
        "note": "user_cancel_no_fault",
        "route": [],
        "negative_checks": [
            {"topic": TOPICS["decision"], "field": "stop_distance", "predicate": "lt 0.15",
             "expect": "all_good"},
            {"topic": TOPICS["state"], "field": "exit_reason", "predicate": "ne 0",
             "expect": "all_good"},
            {"topic": TOPICS["planning"], "field": "trajectory_num", "predicate": "eq 0",
             "expect": "all_good"},
        ],
        "confidence": "high",
    }
    return {
        "case_id": "apa-014-user-cancel-no-fault",
        "symptom": "泊车中在7.0s用户主动取消（PARKING→USER_CANCELLED），各信号全程正常。",
        "depth": "quick", "recording": "apa-014-user-cancel-no-fault.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "negative-user-cancel",
                   "initiating_cause": "user cancel request (not a fault)",
                   "necessary_condition": None, "amplifier": None,
                   "consequence": "clean transition to USER_CANCELLED, exit_reason stays 0",
                   "observability_gap": None},
    }


# ---------------------------------------------------------------- apa-015

def case_015() -> dict[str, Any]:
    duration = 8
    offset = 0.8  # decision data.header.timestamp = log_time + 0.8s
    records = background(duration, skip={"state", "decision", "obstacle", "planning", "control"})
    for i in range(duration + 1):
        t = float(i)
        if i == 7:
            st = state(t, i, 9, 0)
        elif i == 8:
            st = state(t, i, 7, 22)
        else:
            st = state(t, i, 2 if i < 2 else (3 if i == 2 else 5))
        records.append((t, TOPICS["state"], st, 0.0))

        if t >= 6.0:
            ob = obstacle(t, i, x={6.0: 0.62, 7.0: 0.24, 8.0: 0.16}[t], y=0.08)
        else:
            ob = obstacle(t, i, x=1.2 + 0.02 * i)
        records.append((t, TOPICS["obstacle"], ob, 0.0))

        if t >= 7.0:
            de = decision(t, i, result="Close", stop_distance=0.12 if t == 7.0 else 0.10,
                          stop_reason="OBSTACLE_IN_PATH",
                          debug="collision=front object_id=701",
                          header_offset=offset)
        else:
            de = decision(t, i, stop_distance=round(1.3 - 0.05 * i, 3), header_offset=offset)
        records.append((t, TOPICS["decision"], de, 0.0))

        if t >= 7.0:
            pl = planning(t, i, count=3, clearance=0.12)
        else:
            pl = planning(t, i, clearance=round(0.58 - 0.01 * i, 3))
        records.append((t, TOPICS["planning"], pl, 0.0))

        if t >= 7.0:
            ctl = control(t, i, control_state=1, lat_err=0.05, lon_err=0.02)
        else:
            ctl = control(t, i)
        records.append((t, TOPICS["control"], ctl, 0.0))

    expected = {
        "clock": "log_time",
        "anchor": {"topic": TOPICS["decision"], "field": "stop_distance",
                   "predicate": "lt 0.15", "last_good": 6.0, "first_bad": 7.0},
        "window_secs": [6.0, 8.5],
        "specialist": "apa_decision_analysis",
        "inputs": [{"topic": TOPICS["obstacle"], "field": "pose.x", "max_age_ms": 1100}],
        "clock_note": {"topic": TOPICS["decision"], "header_offset_secs": 0.8,
                       "first_bad_on_header_time": 7.8,
                       "note": "apa_decision的data.header.timestamp相对log_time固定+0.8s；用header_time对时会把first_bad误判为7.8s，应以log_time为准"},
        "question": "决策topic的header时间与log_time存在固定+0.8s偏移，异常时刻应以哪个时钟为准？",
        "boundary": "perception.obstacle_pk -> apa_decision (with decision header clock skew)",
        "confidence": "high",
    }
    return {
        "case_id": "apa-015-clock-skew",
        "symptom": "决策在7.0s(log_time)输出Close停车，但其header时间戳显示7.8s，时间对不上。",
        "depth": "standard", "recording": "apa-015-clock-skew.mcap",
        "records": records, "expected": expected,
        "labels": {"class": "clock-skew",
                   "initiating_cause": "valid obstacle enters path (same as apa-002)",
                   "necessary_condition": "decision header clock offset +0.8s vs log_time",
                   "amplifier": "naive header_time alignment shifts the event by +0.8s",
                   "consequence": "state suspend then exit_reason=22; header_time first_bad=7.8s",
                   "observability_gap": None},
    }


def main() -> None:
    for directory in (RECORDINGS, CASES, EXPECTED):
        directory.mkdir(parents=True, exist_ok=True)
    cases = [case_010(), case_011(), case_012(), case_013(), case_014(), case_015()]
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
