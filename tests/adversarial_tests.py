#!/usr/bin/env python3
"""Adversarial tests for the mcap-analysis scripts.

Written by an independent tester from the script sources only (docstrings and
argument contracts). Deliberately attacks boundaries the scripts claim to
handle: window edges, debounce/min-duration exactness, missing-policy
semantics, camel/snake ambiguity, correlation staleness edges, and report
robustness against malformed staged JSON.

Run:  venv/bin/python tests/adversarial_tests.py
Each case prints "ok NAME" or "FAIL NAME -- evidence"; a summary is printed
at the end. Exit code 0 only when everything passes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
BUNDLE = HERE.parents[1]
SCRIPTS = BUNDLE / "skills" / "mcap-analysis" / "scripts"
PY = sys.executable
TMP = Path(tempfile.mkdtemp(prefix="mcap_adv_"))

sys.path.insert(0, str(SCRIPTS))
import mcap_common  # noqa: E402  (unit-level access to dotted_get)

from mcap.writer import Writer  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, evidence: str = "") -> None:
    RESULTS.append((name, bool(cond), evidence))
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name} -- {evidence}")


def run(script: str, *args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPTS)
    return subprocess.run(
        [PY, str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True, env=env, timeout=120,
    )


def ns(ms: int) -> int:
    """Integer milliseconds -> integer nanoseconds (avoids float truncation)."""
    return ms * 1_000_000


def write_mcap(name: str, msgs) -> Path:
    """msgs: iterable of (topic, log_time_ns, payload_dict[, publish_time_ns])."""
    path = TMP / name
    with open(path, "wb") as f:
        w = Writer(f)
        w.start()
        chans: dict[str, int] = {}
        for m in msgs:
            topic, t, payload = m[0], m[1], m[2]
            pt = m[3] if len(m) > 3 else t
            if topic not in chans:
                sid = w.register_schema(topic.strip("/") or "s", "jsonschema", b"{}")
                chans[topic] = w.register_channel(topic, "json", sid)
            w.add_message(chans[topic], t, json.dumps(payload).encode(), pt)
        w.finish()
    return path


def jload(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def approx(a, b, eps=1e-6) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) <= eps


# =========================================================================
# 1. dotted_get: camelCase / snake_case / data.-prefix semantics
# =========================================================================

def test_dotted_get():
    rec = {"topic": "/t", "log_time": 1.0, "data": {"speedMps": 3}}
    check("dg_snake_path_finds_camel_key",
          mcap_common.dotted_get(rec, "speed_mps") == 3,
          f"got {mcap_common.dotted_get(rec, 'speed_mps')!r}")

    rec = {"topic": "/t", "log_time": 1.0, "data": {"speed_mps": 4}}
    check("dg_camel_path_finds_snake_key",
          mcap_common.dotted_get(rec, "speedMps") == 4,
          f"got {mcap_common.dotted_get(rec, 'speedMps')!r}")

    # Ambiguity: both spellings present with DIFFERENT values. The only
    # defensible resolution is exact-spelling-first for each path form.
    rec = {"data": {"speed_mps": 1, "speedMps": 2}}
    got_snake = mcap_common.dotted_get(rec, "speed_mps")
    got_camel = mcap_common.dotted_get(rec, "speedMps")
    check("dg_ambiguous_exact_spelling_wins",
          got_snake == 1 and got_camel == 2,
          f"snake path -> {got_snake!r}, camel path -> {got_camel!r} (expected 1 / 2)")

    # Ambiguity across nesting levels: bare path vs auto data.-prefix.
    # Full-path candidate is tried first, so a top-level key shadows data.<key>.
    rec = {"foo": "top", "data": {"foo": "nested"}}
    got = mcap_common.dotted_get(rec, "foo")
    check("dg_bare_key_prefers_exact_over_data_prefix",
          got == "top", f"got {got!r}")

    rec = {"data": {"foo": {"bar": 7}}}
    check("dg_auto_data_prefix",
          mcap_common.dotted_get(rec, "foo.bar") == 7,
          f"got {mcap_common.dotted_get(rec, 'foo.bar')!r}")

    # Explicit JSON null and truly-missing key are both None -- documented
    # design (missing-policy handles both); must at least be consistent.
    rec = {"data": {"a": None}}
    check("dg_null_and_missing_both_none",
          mcap_common.dotted_get(rec, "a") is None
          and mcap_common.dotted_get(rec, "zzz") is None, "")


# =========================================================================
# 2. Time-window boundaries (mcap_slice, in-process json path)
# =========================================================================

def test_window_boundaries():
    rec = write_mcap("win.mcap", [
        ("/t", ns(1000), {"x": 1}),
        ("/t", ns(2000), {"x": 2}),
        ("/t", ns(3000), {"x": 3}),
    ])
    p = run("mcap_slice.py", rec, "--start-secs", 1.0, "--end-secs", 2.0,
            "--field", "/t:x")
    lines = [jload(l) for l in p.stdout.splitlines() if l.strip()]
    rows = [l for l in lines if l and "time" in l]
    times = sorted(r["time"] for r in rows)
    # read_records' own post-filter keeps log_time == end_secs (`> end: skip`),
    # and the CLI fallback path widens with ceil() then filters inclusively.
    # So the contract is an INCLUSIVE end; the message at exactly 2.0s must
    # appear.
    check("slice_start_boundary_inclusive",
          any(approx(t, 1.0) for t in times), f"times={times}")
    check("slice_end_boundary_inclusive",
          any(approx(t, 2.0) for t in times),
          f"times={times}; message at exactly end-secs was dropped "
          "(python-mcap iter_messages end_time is exclusive, contradicting "
          "the inclusive post-filter and the CLI path's ceil())")

    p = run("mcap_slice.py", rec, "--start-secs", 2.0, "--end-secs", 2.0,
            "--field", "/t:x")
    check("slice_start_eq_end_graceful",
          p.returncode == 1 and "Traceback" not in p.stderr
          and "end-secs" in p.stderr,
          f"rc={p.returncode} stderr={p.stderr[:200]!r}")


# =========================================================================
# 3. Event locator: recording-end boundary
# =========================================================================

def test_locator_recording_end():
    # The abnormal sample is the LAST message of the recording. The locator
    # windows to recording_bounds() = [first, last log_time]; an analysis
    # tool must be able to see the final message of its own default window.
    rec = write_mcap("last.mcap", [
        ("/s", ns(1000), {"v": 0}),
        ("/s", ns(2000), {"v": 0}),
        ("/s", ns(3000), {"v": 0}),
        ("/s", ns(4000), {"v": 0}),
        ("/s", ns(5000), {"v": 9}),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "gt", "--threshold", 5, "--debounce-count", 1)
    out = jload(p.stdout)
    res = out["results"][0] if out else {}
    check("locator_sees_last_message_of_recording",
          res.get("sample_count") == 5
          and res.get("status") == "transition_found",
          f"sample_count={res.get('sample_count')} status={res.get('status')!r}"
          " -- final message excluded (bounds end fed to an exclusive"
          " end_time), so a failure on the last sample is invisible")

    # User-supplied --end-secs landing exactly on a message time.
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "gt", "--threshold", 99,
            "--end-secs", 3.0)
    out = jload(p.stdout)
    res = out["results"][0] if out else {}
    check("locator_user_end_boundary_inclusive",
          res.get("sample_count") == 3,
          f"sample_count={res.get('sample_count')} (expected 3: messages at "
          "1s,2s,3s; the sample at exactly --end-secs was dropped)")


# =========================================================================
# 4. Event locator: debounce / min-duration exact boundaries
# =========================================================================

def pad(t_ms: int):
    # Trailing message on an unrelated topic pushes recording_bounds.end past
    # the samples under test, isolating debounce tests from the end-boundary
    # behavior tested above.
    return ("/pad", ns(t_ms), {"p": 0})


def test_debounce_boundaries():
    # Run length exactly == debounce-count must fire.
    rec = write_mcap("deb3.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"v": 9}),
        ("/s", ns(3000), {"v": 9}),
        ("/s", ns(4000), {"v": 9}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "gt", "--threshold", 5, "--debounce-count", 3)
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("debounce_exactly_at_count_fires",
          res.get("status") == "transition_found"
          and approx((res.get("first_bad") or {}).get("time"), 2.0)
          and res.get("bad_run_count") == 3
          and approx((res.get("last_good") or {}).get("time"), 1.0),
          f"res={res}")

    # Run length == debounce-count - 1 must NOT fire.
    rec = write_mcap("deb3short.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"v": 9}),
        ("/s", ns(3000), {"v": 9}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "gt", "--threshold", 5, "--debounce-count", 3)
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("debounce_one_short_does_not_fire",
          res.get("status") == "bad_but_below_debounce"
          and approx((res.get("first_bad") or {}).get("time"), 2.0),
          f"res={res}")

    # min-duration combined with debounce: count satisfied early, duration
    # satisfied only later; first_bad must stay at the run start.
    rec = write_mcap("mindur.mcap", [
        ("/s", ns(9900), {"v": 1}),
        ("/s", ns(10000), {"v": 9}),
        ("/s", ns(10001), {"v": 9}),
        ("/s", ns(10002), {"v": 9}),
        ("/s", ns(10010), {"v": 9}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "gt", "--threshold", 5,
            "--debounce-count", 2, "--min-duration-ms", 5)
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("min_duration_with_debounce",
          res.get("status") == "transition_found"
          and approx((res.get("first_bad") or {}).get("time"), 10.0, 1e-4)
          and res.get("bad_run_count") == 4
          and res.get("bad_duration_ms", 0) >= 5,
          f"res={res}")

    # Same run but nothing extends past min-duration: must not fire.
    rec = write_mcap("mindur_short.mcap", [
        ("/s", ns(9900), {"v": 1}),
        ("/s", ns(10000), {"v": 9}),
        ("/s", ns(10001), {"v": 9}),
        ("/s", ns(10002), {"v": 9}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "gt", "--threshold", 5,
            "--debounce-count", 2, "--min-duration-ms", 5)
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("min_duration_unmet_does_not_fire",
          res.get("status") in ("bad_but_below_debounce",),
          f"res={res}")

    # gt is strict: value exactly == threshold is good.
    rec = write_mcap("gt_strict.mcap", [
        ("/s", ns(1000), {"v": 4}),
        ("/s", ns(2000), {"v": 5}),
        ("/s", ns(3000), {"v": 5}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "gt", "--threshold", 5, "--debounce-count", 1)
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("gt_strict_at_threshold", res.get("status") == "all_good",
          f"res={res}")

    # eq with a numeric field value against a CLI string --value.
    rec = write_mcap("eqnum.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"v": 5}),
        ("/s", ns(3000), {"v": 5}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "eq", "--value", 5, "--debounce-count", 2)
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("eq_numeric_value_matches_cli_string",
          res.get("status") == "transition_found"
          and approx((res.get("first_bad") or {}).get("time"), 2.0),
          f"res={res}")


# =========================================================================
# 5. missing-policy: the three values must actually differ
# =========================================================================

def test_missing_policy():
    # good(1s), missing(2s), bad(3s), bad(4s); debounce 2, gt 5.
    rec = write_mcap("miss.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"other": 1}),   # field absent
        ("/s", ns(3000), {"v": 9}),
        ("/s", ns(4000), {"v": 9}),
        pad(99000),
    ])
    res = {}
    for policy in ("skip", "unknown", "bad"):
        p = run("mcap_event_locator.py", rec, "--field", "/s:v",
                "--condition", "gt", "--threshold", 5,
                "--debounce-count", 2, "--missing-policy", policy)
        res[policy] = (jload(p.stdout) or {}).get("results", [{}])[0]

    check("policy_skip_transparent",
          res["skip"].get("status") == "transition_found"
          and approx((res["skip"].get("first_bad") or {}).get("time"), 3.0)
          and approx((res["skip"].get("last_good") or {}).get("time"), 1.0)
          and res["skip"].get("missing_or_null_count") == 1,
          f"res={res['skip']}")
    check("policy_bad_counts_missing_as_bad",
          res["bad"].get("status") == "transition_found"
          and approx((res["bad"].get("first_bad") or {}).get("time"), 2.0),
          f"res={res['bad']}")
    check("policy_unknown_neither_good_nor_bad",
          res["unknown"].get("status") == "transition_found"
          and approx((res["unknown"].get("first_bad") or {}).get("time"), 3.0)
          and approx((res["unknown"].get("last_good") or {}).get("time"), 1.0)
          and res["unknown"].get("missing_or_null_count") == 1,
          f"res={res['unknown']}")

    # A missing sample INSIDE a bad run: skip preserves the run; unknown
    # interrupts it (conservative: continuity unconfirmed). These two
    # policies must diverge here -- that divergence is their entire point.
    rec = write_mcap("miss_mid.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"v": 9}),
        ("/s", ns(3000), {"other": 1}),
        ("/s", ns(4000), {"v": 9}),
        ("/s", ns(5000), {"v": 9}),
        pad(99000),
    ])
    got = {}
    for policy in ("skip", "unknown"):
        p = run("mcap_event_locator.py", rec, "--field", "/s:v",
                "--condition", "gt", "--threshold", 5,
                "--debounce-count", 3, "--missing-policy", policy)
        got[policy] = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("policy_skip_bridges_gap_in_bad_run",
          got["skip"].get("status") == "transition_found"
          and approx((got["skip"].get("first_bad") or {}).get("time"), 2.0),
          f"res={got['skip']}")
    check("policy_unknown_interrupts_bad_run",
          got["unknown"].get("status") == "bad_but_below_debounce",
          f"res={got['unknown']} (expected the unknown sample to break the "
          "run so debounce 3 is never reached)")


# =========================================================================
# 6. changed mode
# =========================================================================

def test_changed():
    # Exactly-at-debounce: A B B with debounce 2 -> one transition.
    rec = write_mcap("chg_exact.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"v": 2}),
        ("/s", ns(3000), {"v": 2}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "changed", "--debounce-count", 2)
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    tr = (res.get("transitions") or [{}])[0]
    check("changed_debounce_exact_fires",
          res.get("transition_count") == 1
          and tr.get("last_good_value") == 1
          and tr.get("first_changed_value") == 2
          and approx(tr.get("first_changed_time"), 2.0),
          f"res={res}")

    # One sample short of debounce: no transition.
    rec = write_mcap("chg_short.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"v": 2}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "changed", "--debounce-count", 2)
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("changed_below_debounce_silent",
          res.get("transition_count") == 0, f"res={res}")

    # Two-step change 1 -> 2 -> 3 where 3 then holds stably for 3 samples,
    # debounce 2. The signal indisputably changed (1 at start, 3 stable at
    # end); a change locator that reports NO transition here has lost a real
    # event (the brief 2 resets the tracker and silently adopts 3 as the new
    # baseline without ever reporting it).
    rec = write_mcap("chg_swallow.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"v": 1}),
        ("/s", ns(3000), {"v": 2}),
        ("/s", ns(4000), {"v": 3}),
        ("/s", ns(5000), {"v": 3}),
        ("/s", ns(6000), {"v": 3}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "changed", "--debounce-count", 2)
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("changed_two_step_transition_not_swallowed",
          res.get("transition_count", 0) >= 1,
          f"res={res} (value went 1 -> 3 and held 3 for three samples, yet "
          "no transition reported: candidate reset discards the old baseline)")

    # Missing samples must not FABRICATE transitions. Field disappears for
    # one message then returns with the same value.
    rec = write_mcap("chg_null.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"other": 1}),
        ("/s", ns(3000), {"v": 1}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "changed", "--debounce-count", 1,
            "--missing-policy", "unknown")
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    check("changed_missing_does_not_fabricate_transitions",
          res.get("transition_count") == 0,
          f"res={res} (a dropped field sample produced value->null->value "
          "'transitions' under missing-policy=unknown)")

    # With skip, the same gap is transparent and a real change across it is
    # still caught.
    rec = write_mcap("chg_skip.mcap", [
        ("/s", ns(1000), {"v": 1}),
        ("/s", ns(2000), {"other": 1}),
        ("/s", ns(3000), {"v": 2}),
        ("/s", ns(4000), {"v": 2}),
        pad(99000),
    ])
    p = run("mcap_event_locator.py", rec, "--field", "/s:v",
            "--condition", "changed", "--debounce-count", 2,
            "--missing-policy", "skip")
    res = (jload(p.stdout) or {}).get("results", [{}])[0]
    tr = (res.get("transitions") or [{}])[0]
    check("changed_skip_bridges_gap",
          res.get("transition_count") == 1
          and tr.get("last_good_value") == 1
          and tr.get("first_changed_value") == 2
          and res.get("missing_or_null_count") == 1,
          f"res={res}")


# =========================================================================
# 7. frame correlation
# =========================================================================

def test_correlation():
    # Target earlier than every source -> no_prior_source, not a crash and
    # not a bogus pairing.
    rec = write_mcap("corr_early.mcap", [
        ("/src", ns(2000), {"frame": 1}),
        ("/src", ns(3000), {"frame": 2}),
        ("/tgt", ns(1000), {"state": "OK"}),
        ("/tgt", ns(3500), {"state": "OK"}),
    ])
    p = run("mcap_frame_correlation.py", rec,
            "--source-topic", "/src", "--target-topic", "/tgt",
            "--target-field", "state", "--match", "OK")
    out = jload(p.stdout) or {}
    check("corr_target_before_all_sources",
          out.get("no_prior_source") == 1
          and out.get("matched_target_messages") == 1,
          f"rc={p.returncode} out_keys={sorted(out)[:8]} "
          f"no_prior={out.get('no_prior_source')} "
          f"matched={out.get('matched_target_messages')}")

    # ALL targets earlier than every source -> graceful structured error.
    rec = write_mcap("corr_allearly.mcap", [
        ("/src", ns(2000), {"frame": 1}),
        ("/tgt", ns(1000), {"state": "OK"}),
    ])
    p = run("mcap_frame_correlation.py", rec,
            "--source-topic", "/src", "--target-topic", "/tgt",
            "--target-field", "state", "--match", "OK")
    check("corr_all_targets_early_graceful",
          p.returncode == 1 and "Traceback" not in p.stderr
          and (jload(p.stderr) or {}).get("error"),
          f"rc={p.returncode} stderr={p.stderr[:200]!r}")

    # max-age exactly met, float-exact case (0.5s): must be fresh.
    rec = write_mcap("corr_age_exact.mcap", [
        ("/src", ns(1000), {"frame": 1}),
        ("/tgt", ns(1500), {"state": "OK"}),
    ])
    p = run("mcap_frame_correlation.py", rec,
            "--source-topic", "/src", "--target-topic", "/tgt",
            "--target-field", "state", "--match", "OK", "--max-age-ms", 500)
    out = jload(p.stdout) or {}
    check("corr_max_age_exact_representable_fresh",
          out.get("fresh_matches") == 1 and out.get("stale_matches") == 0,
          f"fresh={out.get('fresh_matches')} stale={out.get('stale_matches')}")

    # max-age exactly met, float-inexact case (0.1s = 100ms): the report
    # prints age_ms == 100.0 -- an age the tool itself displays as equal to
    # max_age_ms=100 must not be classified stale.
    rec = write_mcap("corr_age_float.mcap", [
        ("/src", ns(1000), {"frame": 1}),
        ("/tgt", ns(1100), {"state": "OK"}),
    ])
    p = run("mcap_frame_correlation.py", rec,
            "--source-topic", "/src", "--target-topic", "/tgt",
            "--target-field", "state", "--match", "OK",
            "--max-age-ms", 100, "--rows")
    out = jload(p.stdout) or {}
    row = (out.get("rows") or [{}])[0]
    check("corr_max_age_float_boundary_consistent",
          row.get("age_ms") == 100.0 and row.get("age_status") == "fresh",
          f"age_ms={row.get('age_ms')} age_status={row.get('age_status')!r} "
          "max_age_ms=100 (raw float 1.1-1.0 > 0.1 flips the boundary to "
          "stale while the rounded age says exactly 100.0)")

    # allow-equal-time semantics at an exact tie.
    rec = write_mcap("corr_tie.mcap", [
        ("/src", ns(1000), {"frame": 1}),
        ("/src", ns(2000), {"frame": 2}),
        ("/tgt", ns(2000), {"state": "OK"}),
    ])
    p = run("mcap_frame_correlation.py", rec,
            "--source-topic", "/src", "--target-topic", "/tgt",
            "--target-field", "state", "--match", "OK", "--rows")
    row = ((jload(p.stdout) or {}).get("rows") or [{}])[0]
    check("corr_equal_time_default_pairs_tie",
          approx(row.get("source_time"), 2.0) and row.get("age_ms") == 0.0,
          f"row={row}")
    p = run("mcap_frame_correlation.py", rec,
            "--source-topic", "/src", "--target-topic", "/tgt",
            "--target-field", "state", "--match", "OK", "--rows",
            "--no-allow-equal-time")
    row = ((jload(p.stdout) or {}).get("rows") or [{}])[0]
    check("corr_no_equal_time_steps_back",
          approx(row.get("source_time"), 1.0),
          f"row={row}")


# =========================================================================
# 8. report rendering robustness
# =========================================================================

def case_dir(name: str, files: dict) -> Path:
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        (d / fname).write_text(text)
    return d


def run_report(d: Path) -> subprocess.CompletedProcess:
    return run("mcap_report.py", "--case-dir", d)


def test_report():
    # Missing root_cause: degraded render, banner, exit 2 -- not a crash.
    d = case_dir("case_noroot", {
        "01_manifest.json": {"case_id": "t1", "recording": {"sha256": "ab" * 32}},
        "07_conclusion.json": {"confidence": "low"},
    })
    p = run_report(d)
    report = (d / "report.md").read_text() if (d / "report.md").exists() else ""
    ok_flag = (jload(p.stdout) or {}).get("ok")
    check("report_missing_root_cause_degrades",
          p.returncode == 2 and "Traceback" not in p.stderr
          and "⛔" in report and ok_flag is False,
          f"rc={p.returncode} ok={ok_flag} banner={'⛔' in report} "
          f"stderr={p.stderr[:150]!r}")

    # Timeline event without required topic: placeholder + exit 2.
    d = case_dir("case_notopic", {
        "07_conclusion.json": {"root_cause": "x", "confidence": "high"},
        "09_timeline.json": {"events": [
            {"time": 1.0, "text": "a"},
            {"time": 2.0, "topic": "/t", "text": "b"},
        ]},
    })
    p = run_report(d)
    report = (d / "report.md").read_text() if (d / "report.md").exists() else ""
    check("report_event_missing_topic_flagged",
          p.returncode == 2 and "缺来源" in report
          and "Traceback" not in p.stderr,
          f"rc={p.returncode} stderr={p.stderr[:150]!r}")

    # Claim: renderer degrades instead of crashing. Attack with type-confused
    # staged JSON (valid JSON, wrong shapes).
    d = case_dir("case_conclusion_array", {
        "07_conclusion.json": [1, 2, 3],
    })
    p = run_report(d)
    check("report_conclusion_is_array_no_crash",
          p.returncode in (0, 2) and "Traceback" not in p.stderr,
          f"rc={p.returncode} stderr_tail={p.stderr[-200:]!r}")

    d = case_dir("case_events_bad_types", {
        "07_conclusion.json": {"root_cause": "x"},
        "09_timeline.json": {"events": [
            {"time": 1.0, "topic": "/t", "text": "fine"},
            "stray string",
            42,
        ]},
    })
    p = run_report(d)
    check("report_timeline_bad_item_types_no_crash",
          p.returncode in (0, 2) and "Traceback" not in p.stderr,
          f"rc={p.returncode} stderr_tail={p.stderr[-200:]!r}")

    d = case_dir("case_recs_strings", {
        "07_conclusion.json": {"root_cause": "x",
                               "recommendations": ["just fix it"]},
    })
    p = run_report(d)
    check("report_recommendations_strings_no_crash",
          p.returncode in (0, 2) and "Traceback" not in p.stderr,
          f"rc={p.returncode} stderr_tail={p.stderr[-200:]!r}")

    d = case_dir("case_tp_array", {
        "07_conclusion.json": {"root_cause": "x",
                               "temporal_packet": ["not", "a", "dict"]},
    })
    p = run_report(d)
    check("report_temporal_packet_array_no_crash",
          p.returncode in (0, 2) and "Traceback" not in p.stderr,
          f"rc={p.returncode} stderr_tail={p.stderr[-200:]!r}")

    # Entirely empty case dir: everything missing -> degraded render, exit 2.
    d = case_dir("case_empty", {})
    p = run_report(d)
    check("report_empty_case_dir_degrades",
          p.returncode == 2 and "Traceback" not in p.stderr
          and (d / "report.md").exists(),
          f"rc={p.returncode} stderr={p.stderr[:150]!r}")


# =========================================================================

def main() -> int:
    for fn in (test_dotted_get, test_window_boundaries,
               test_locator_recording_end, test_debounce_boundaries,
               test_missing_policy, test_changed, test_correlation,
               test_report):
        try:
            fn()
        except Exception as exc:  # a crashed test group is itself a failure
            check(f"{fn.__name__}_did_not_raise", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\ntotal: {len(RESULTS)}  passed: {passed}  failed: {failed}")
    print(f"fixtures: {TMP}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
