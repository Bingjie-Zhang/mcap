#!/usr/bin/env python3
"""Regression tests for the mcap-analysis scripts. Needs the `mcap` python package.

Usage: python3 tests/run_tests.py  (from the bundle root; builds its own fixture)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

BUNDLE = Path(__file__).resolve().parents[1]
SCRIPTS = BUNDLE / "skills" / "mcap-analysis" / "scripts"
PASSED = 0


def run(script: str, *argv: str) -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPTS / script), *argv],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"{script} failed: {proc.stderr.strip()}")
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    return json.loads(lines[-1]) if script == "mcap_slice.py" else json.loads(lines[0])


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if not condition:
        raise AssertionError(f"FAIL {name} {detail}")
    PASSED += 1
    print(f"ok   {name}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        fixture = str(Path(tmp) / "fixture.mcap")
        subprocess.run([sys.executable, str(BUNDLE / "tests" / "make_fixture.py"), fixture],
                       check=True, capture_output=True)

        # 1. predicate locator: threshold transition, glitch rejected by debounce
        out = run("mcap_event_locator.py", fixture,
                  "--field", "/apa/decision:data.mirror_fold_dist",
                  "--condition", "lt", "--threshold", "0.15", "--debounce-count", "3")
        r = out["results"][0]
        check("locator.predicate.status", r["status"] == "transition_found", r["status"])
        check("locator.predicate.first_bad", abs(r["first_bad"]["time"] - 1012.0) < 1e-6,
              str(r["first_bad"]))
        check("locator.predicate.glitch_rejected", r["last_good"]["time"] > 1005.0)

        # 2. changed + tolerance: float jitter is not an event
        out = run("mcap_event_locator.py", fixture,
                  "--field", "/apa/decision:data.mirror_fold_dist",
                  "--condition", "changed", "--tolerance", "0.05", "--debounce-count", "3")
        r = out["results"][0]
        check("locator.changed.single_transition", r["transition_count"] == 1,
              str(r["transition_count"]))

        # 3. regex locator on enum-ish debug string
        out = run("mcap_event_locator.py", fixture,
                  "--field", "/apa/decision:data.decDebugStr",
                  "--condition", "regex", "--pattern", "state=Close", "--debounce-count", "2")
        r = out["results"][0]
        check("locator.regex.transition", r["status"] == "transition_found"
              and abs(r["first_bad"]["time"] - 1012.0) < 1e-6)

        # 4. missing field -> no_classifiable_samples, not a crash
        out = run("mcap_event_locator.py", fixture,
                  "--field", "/apa/decision:data.not_there",
                  "--condition", "lt", "--threshold", "1")
        check("locator.missing_field", out["results"][0]["status"] == "no_classifiable_samples")

        # 5. correlation: stale detection over the 2s upstream gap
        out = run("mcap_frame_correlation.py", fixture,
                  "--source-topic", "/apa/perception", "--target-topic", "/apa/decision",
                  "--source-id-field", "data.seq", "--target-field", "data.decDebugStr",
                  "--match", "state=", "--max-age-ms", "300")
        check("correlation.stale_detected", out["stale_matches"] >= 15, str(out["stale_matches"]))
        check("correlation.fresh_plus_stale", out["fresh_matches"] + out["stale_matches"]
              == out["matched_target_messages"])

        # 6. correlation: missing source-id degrades to a counter, not an exception
        out = run("mcap_frame_correlation.py", fixture,
                  "--source-topic", "/apa/perception", "--target-topic", "/apa/decision",
                  "--source-id-field", "data.nonexistent", "--target-field", "data.decDebugStr",
                  "--match", "state=", "--max-age-ms", "300")
        check("correlation.source_id_missing_tolerated",
              out["quality"]["source_id_missing"] == out["matched_target_messages"])

        # 7. correlation: header_time clock is usable end to end
        out = run("mcap_frame_correlation.py", fixture,
                  "--source-topic", "/apa/perception", "--target-topic", "/apa/decision",
                  "--source-clock", "header_time", "--target-clock", "header_time",
                  "--target-field", "data.decDebugStr", "--match", "state=Close",
                  "--max-age-ms", "300")
        check("correlation.header_clock", out["clock"]["source"] == "header_time"
              and out["matched_target_messages"] > 0)

        # 8. slice: partial reads are reported explicitly
        out = run("mcap_slice.py", fixture, "--start-secs", "1000", "--end-secs", "1020",
                  "--field", "/apa/decision:data.mirror_fold_dist", "--max-records", "50")
        check("slice.partial_reported", out["read"]["status"] == "partial"
              and out["read"]["limit_hit"] == "max_records=50")

        # 9. slice: complete read says complete
        out = run("mcap_slice.py", fixture, "--start-secs", "1000", "--end-secs", "1002",
                  "--field", "/apa/decision:data.mirror_fold_dist")
        check("slice.complete", out["read"]["status"] == "complete")

        # 9b. timing objects present on every tool result
        check("timing.slice", out["timing"]["total_secs"] >= 0
              and out["timing"]["read_secs"] is not None)
        out_t = run("mcap_event_locator.py", fixture,
                    "--field", "/apa/decision:data.decDebugStr",
                    "--condition", "regex", "--pattern", "state=Close")
        check("timing.locator", out_t["timing"]["total_secs"] >= out_t["timing"]["read_secs"] >= 0)
        out_t = run("mcap_frame_correlation.py", fixture,
                    "--source-topic", "/apa/perception", "--target-topic", "/apa/decision",
                    "--target-field", "data.decDebugStr", "--match", "state=", "--max-age-ms", "300")
        check("timing.correlation", out_t["timing"]["total_secs"] >= 0
              and out_t["timing"]["analyze_secs"] is not None)

        # 9c. inventory: 基础输出 / timing / 缓存命中（此前测试盲区）
        import os as _os
        env = dict(_os.environ, MCAP_CACHE_DIR=str(Path(tmp) / "cache"))
        for expected_cache in ("miss-stored", "hit"):
            proc = subprocess.run([sys.executable, str(SCRIPTS / "mcap_inventory.py"), fixture,
                                   "--top", "5"], capture_output=True, text=True, env=env, timeout=120)
            if proc.returncode != 0:
                raise AssertionError(f"inventory failed: {proc.stderr.strip()[:200]}")
            inv = json.loads(proc.stdout.splitlines()[0])
            check(f"inventory.cache_{expected_cache}", inv["timing"].get("cache") == expected_cache
                  and inv["timing"]["total_secs"] >= 0, str(inv.get("timing")))
        check("inventory.topics", inv["channel_count"] >= 2 and inv["message_count"] > 0)

        # 10. case manifest: hash + bounds + versions
        out = run("mcap_case_manifest.py", fixture, "--case-id", "TEST-1")
        check("manifest.sha256", len(out["recording"]["sha256"]) == 64)
        check("manifest.bounds", abs(out["recording"]["start_secs"] - 1000.0) < 1e-6
              and out["recording"]["duration_s"] > 19.0)
        check("manifest.registry_version", bool(out["tools"]["registry_version"]))
        check("timing.manifest", out["timing"]["hash_secs"] >= 0
              and out["timing"]["total_secs"] >= out["timing"]["hash_secs"])

        # 11. CLI streaming path: non-json encoding must route through `mcap cat`
        import os, stat as statmod
        fixture_cli = str(Path(tmp) / "fixture_cli.mcap")
        subprocess.run([sys.executable, str(BUNDLE / "tests" / "make_fixture.py"),
                        fixture_cli, "custom-json"], check=True, capture_output=True)
        shim_dir = Path(tmp) / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "mcap"
        shim.write_text(f"""#!{sys.executable}
import argparse, json
from mcap.reader import make_reader
p = argparse.ArgumentParser()
p.add_argument("cmd"); p.add_argument("recording")
p.add_argument("--json", action="store_true"); p.add_argument("--topics")
p.add_argument("--start-secs", type=float); p.add_argument("--end-secs", type=float)
a = p.parse_args()
topics = a.topics.split(",") if a.topics else None
start = int(a.start_secs*1e9) if a.start_secs is not None else None
end = int(a.end_secs*1e9) if a.end_secs is not None else None
with open(a.recording, "rb") as fh:
    for _, ch, m in make_reader(fh).iter_messages(topics=topics, start_time=start,
                                                  end_time=end, log_time_order=True):
        print(json.dumps({{"topic": ch.topic, "sequence": m.sequence,
                          "log_time": m.log_time/1e9, "publish_time": m.publish_time/1e9,
                          "data": json.loads(m.data)}}))
""")
        shim.chmod(shim.stat().st_mode | statmod.S_IEXEC)
        env = dict(os.environ, PATH=f"{shim_dir}:{os.environ['PATH']}")
        proc = subprocess.run([sys.executable, str(SCRIPTS / "mcap_event_locator.py"), fixture_cli,
                               "--field", "/apa/decision:data.mirror_fold_dist",
                               "--condition", "lt", "--threshold", "0.15", "--debounce-count", "3"],
                              capture_output=True, text=True, env=env, timeout=120)
        check("cli_path.exit", proc.returncode == 0, proc.stderr.strip()[:200])
        out = json.loads(proc.stdout.splitlines()[0])
        check("cli_path.reader", out["read"]["reader"] == "mcap-cli", out["read"]["reader"])
        check("cli_path.same_result", out["results"][0]["status"] == "transition_found"
              and abs(out["results"][0]["first_bad"]["time"] - 1012.0) < 1e-6)

    # ---- render layer (Domain Pack) ----
    import shutil
    proc = subprocess.run([sys.executable, str(BUNDLE / "render_agents.py"), "apa", "--check"],
                          capture_output=True, text=True)
    check("render.apa_check", proc.returncode == 0, proc.stderr.strip()[:200])

    with tempfile.TemporaryDirectory() as tmp:
        # apa 渲染产物：6 个文件、每个 specialist 必带 JSON 路由包契约
        out_dir = Path(tmp) / "agents"
        proc = subprocess.run([sys.executable, str(BUNDLE / "render_agents.py"), "apa",
                               "--out-dir", str(out_dir)], capture_output=True, text=True, cwd=BUNDLE)
        rendered = sorted(f.name for f in (BUNDLE / out_dir).glob("*.toml")) \
            if not out_dir.is_absolute() else sorted(f.name for f in out_dir.glob("*.toml"))
        check("render.apa_count", len(rendered) == 6, str(rendered))
        for name in rendered:
            body = (out_dir / name).read_text()
            check(f"render.balanced.{name}", body.count('\"\"\"') % 2 == 0)
        for dom in ["perception", "decision", "planning", "control"]:
            body = (out_dir / f"apa_{dom}_analysis.toml").read_text()
            check(f"render.packet.{dom}", "JSON routing packet" in body and "duration_secs" in body)
        check("render.reasoning_tiers",
              'model_reasoning_effort = "low"' in (out_dir / "apa_problem_analysis.toml").read_text()
              and 'model_reasoning_effort = "medium"' in (out_dir / "apa_manager.toml").read_text()
              and 'model_reasoning_effort = "high"' in (out_dir / "apa_decision_analysis.toml").read_text())
        check("render.quick_depth", "不减少追因深度" in
              (BUNDLE / "skills" / "mcap-analysis" / "SKILL.md").read_text())
        check("render.thin_shell", len((out_dir / "apa_manager.toml").read_text()) < 6000)

        # 泛化：最小 demo pack（2 个领域）应渲染出 4 个 agent，领域词正确代入
        demo = Path(tmp) / "packs" / "demo"
        demo.mkdir(parents=True)
        # render_agents 以自身所在目录找 packs/，因此把 demo pack 放进 bundle 再清理
        bundle_demo = BUNDLE / "packs" / "demo"
        try:
            bundle_demo.mkdir()
            (bundle_demo / "pack.json").write_text(json.dumps({
                "name": "demo", "title": "Demo pipeline",
                "fetch_instructions": "- recording supplied locally.",
                "routing_hints": ["Ingest: raw frames.", "Fusion: fused output."],
                "domains": [
                    {"id": "ingest", "check_aspects": "frame arrival, sequence gaps",
                     "boundary_locations": "the decoder", "evidence_guidance": "Report frame gaps.",
                     "max_bullets": 10},
                    {"id": "fusion", "check_aspects": "input alignment, weights",
                     "boundary_locations": "the fusion kernel", "evidence_guidance": "Report weight drift.",
                     "max_bullets": 10},
                ]}))
            demo_out = Path(tmp) / "demo_agents"
            proc = subprocess.run([sys.executable, str(BUNDLE / "render_agents.py"), "demo",
                                   "--out-dir", str(demo_out)], capture_output=True, text=True)
            check("render.demo_exit", proc.returncode == 0, proc.stderr.strip()[:200])
            names = sorted(f.name for f in demo_out.glob("*.toml"))
            check("render.demo_names", names == ["demo_fusion_analysis.toml", "demo_ingest_analysis.toml",
                                                 "demo_manager.toml", "demo_problem_analysis.toml"], str(names))
            body = (demo_out / "demo_ingest_analysis.toml").read_text()
            check("render.demo_substitution", "Demo pipeline ingest specialist" in body
                  and "frame arrival, sequence gaps" in body and "fusion" in body
                  and "APA" not in body)
        finally:
            shutil.rmtree(bundle_demo, ignore_errors=True)

    # ---- field-name variants (protobuf camelCase vs snake_case) ----
    sys.path.insert(0, str(SCRIPTS))
    from mcap_common import dotted_get
    payload = {"data": {"decStatus": {"rearMirrorState": 1}, "seg_remaining_dist": 0.5}}
    check("dotted.snake_hits_camel", dotted_get(payload, "dec_status.rear_mirror_state") == 1)
    check("dotted.camel_hits_camel", dotted_get(payload, "decStatus.rearMirrorState") == 1)
    check("dotted.camel_hits_snake", dotted_get(payload, "segRemainingDist") == 0.5)
    check("dotted.missing_still_none", dotted_get(payload, "decStatus.notThere") is None)

    # ---- visualization (mcap_plot) ----
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "case-T1"
        case.mkdir()
        (case / "01_manifest.json").write_text(json.dumps({
            "case_id": "T-1", "analysis_started_at": "2026-08-20T00:00:00+00:00",
            "recording": {"path": "/x.mcap", "sha256": "ab" * 32, "duration_s": 20.0}}))
        (case / "07_conclusion.json").write_text(json.dumps({
            "symptom": "s", "conclusion": "根因结论X", "confidence": "medium",
            "temporal_packet": {"primary_clock": "log_time", "anchor": "a", "last_good": "1",
                                "first_bad": "2", "window": "w"},
            "causal_chain": {"initiating_cause": "起因Y"},
            "recommendations": [{"action": "动作Z", "target": "t", "rationale": "r",
                                 "risk": "低", "validation": "v"}]}, ensure_ascii=False))
        (case / "09_timeline.json").write_text(json.dumps({"events": [
            {"id": "a", "time": "1.0", "topic": "/t1", "text": "事件A", "pills": ["x -> y"],
             "role": "root_cause", "in_flow": True},
            {"id": "b", "time": "2.0", "topic": "/t2", "text": "事件B",
             "caused_by": ["a"], "role": "symptom", "in_flow": True},
            {"id": "c", "time": "1.5", "topic": "/t3", "text": "流外观察", "in_flow": False}]},
            ensure_ascii=False))
        proc = subprocess.run([sys.executable, str(SCRIPTS / "mcap_plot.py"), "--case-dir", str(case)],
                              capture_output=True, text=True, timeout=120)
        check("plot.exit", proc.returncode == 0, proc.stderr.strip()[:200])
        html_out = (case / "case_report.html").read_text()
        check("plot.combined_default", "ctl-rail" in html_out and "根因引流" in html_out)
        check("plot.conclusion", "根因结论X" in html_out and "起因Y" in html_out and "动作Z" in html_out)
        check("plot.roles", 'class="dot rc"' in html_out and 'class="dot sym"' in html_out
              and 'class="dot off"' in html_out)
        check("plot.no_unfilled", "{{" not in html_out)
        check("plot.fingerprint", ("ab" * 32)[:8] in html_out or "3 个点" in html_out)
        # 空目录：无数据不出图，页面写原因（有效数据规则 4）
        empty = Path(tmp) / "case-empty"
        empty.mkdir()
        proc = subprocess.run([sys.executable, str(SCRIPTS / "mcap_plot.py"), "--case-dir", str(empty)],
                              capture_output=True, text=True, timeout=120)
        check("plot.empty_exit", proc.returncode == 0, proc.stderr.strip()[:200])
        html_out = (empty / "case_report.html").read_text()
        check("plot.empty_reasons", html_out.count("未生成") >= 2 and '<div class="ctl-row' not in html_out)
        # classic 布局仍可用
        proc = subprocess.run([sys.executable, str(SCRIPTS / "mcap_plot.py"), "--case-dir", str(case),
                               "--layout", "classic", "--out", str(case / "c.html")],
                              capture_output=True, text=True, timeout=120)
        check("plot.classic", proc.returncode == 0
              and "根因引流图" in (case / "c.html").read_text())

    # ---- CASE-0001 jitter scenario (regression for the real-case miss) ----
    with tempfile.TemporaryDirectory() as tmp:
        jit = str(Path(tmp) / "jitter.mcap")
        subprocess.run([sys.executable, str(BUNDLE / "tests" / "make_fixture.py"), jit, "jitter"],
                       check=True, capture_output=True)
        out = run("mcap_event_locator.py", jit, "--field", "/apa/decision:data.dec_status.mirror_fold_dist",
                  "--condition", "changed", "--tolerance", "0.05", "--debounce-count", "1",
                  "--end-secs", "1030")
        check("jitter.flap_detected", out["results"][0]["transition_count"] > 50,
              str(out["results"][0]["transition_count"]))
        out = run("mcap_event_locator.py", jit, "--field", "/apa/planning_output:data.trajectory_num",
                  "--condition", "eq", "--value", "1")
        check("jitter.gate_time", abs(out["results"][0]["first_bad"]["time"] - 1030.0) < 1e-6)
        out = run("mcap_event_locator.py", jit, "--field", "/apa/decision:data.dec_status.rear_mirror_state",
                  "--condition", "eq", "--value", "1", "--debounce-count", "2")
        check("jitter.symptom_time", abs(out["results"][0]["first_bad"]["time"] - 1036.2) < 1e-6)

    # ---- text report renderer (mcap_report) ----
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "c"
        case.mkdir()
        (case / "09_timeline.json").write_text(json.dumps({"events": [
            {"id": "a", "time": "1.0", "topic": "/t1:data.x", "text": "事件A", "role": "root_cause"}]}, ensure_ascii=False))
        (case / "07_conclusion.json").write_text(json.dumps({"root_cause": "根因R", "confidence": "high",
            "gating_checks": [{"condition": "dist<1.8", "signal": "/t1:data.x", "flips": 7, "first_sustained": "2.0"}],
            "timing_breakdown": [{"step": "数据获取", "tool": "scp", "wall_secs": 10, "notes": ""}]}, ensure_ascii=False))
        proc = subprocess.run([sys.executable, str(SCRIPTS / "mcap_report.py"), "--case-dir", str(case)],
                              capture_output=True, text=True, timeout=60)
        check("report.exit", proc.returncode == 0, proc.stderr.strip()[:200])
        md = (case / "report.md").read_text()
        check("report.topic_present", "/t1:data.x" in md and "## 3 事件时间线" in md)
        check("report.verdict_decisive", "**根因：根因R**" in md and "## 1 根因判定" in md)
        check("report.gating_table", "dist<1.8" in md and "## 3b" in md)
        check("report.timing", "数据获取" in md)
        # 缺 topic 必须拒绝
        (case / "09_timeline.json").write_text(json.dumps({"events": [{"id": "b", "time": "1.0", "text": "无来源"}]}))
        proc = subprocess.run([sys.executable, str(SCRIPTS / "mcap_report.py"), "--case-dir", str(case)],
                              capture_output=True, text=True, timeout=60)
        check("report.degrades_missing_topic", proc.returncode == 2
              and "缺来源" in (case / "report.md").read_text()
              and "不完整" in (case / "report.md").read_text())

    # pack registry 与已暂存副本不得漂移
    pack_reg = (BUNDLE / "packs" / "apa" / "registry.yaml").read_text()
    staged = BUNDLE / "skills" / "mcap-analysis" / "references" / "apa_topic_registry.yaml"
    check("render.registry_no_drift", staged.exists() and staged.read_text() == pack_reg)

    # ---- 第三重门禁：9 场景金标准数据集（cases/training）----
    proc = subprocess.run([sys.executable, str(BUNDLE / "tests" / "eval_training.py")],
                          capture_output=True, text=True, timeout=600)
    check("golden.9scenarios", proc.returncode == 0,
          proc.stdout.strip().splitlines()[-1] if proc.stdout else proc.stderr[:200])

    print(f"\nall {PASSED} checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
