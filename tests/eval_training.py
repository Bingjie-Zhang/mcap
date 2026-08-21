#!/usr/bin/env python3
"""对 apa_manager_training 数据集跑工具链定位，与金标准比对。"""
import json, subprocess, sys
from pathlib import Path

D = Path("/Users/bingjiezhang/Desktop/apa_manager_training_20260821")
S = Path("/private/tmp/claude-501/-Users-bingjiezhang/448ab922-9011-4c4b-96b9-9bae9d6a9ac7/scratchpad/mcap/mcap-analysis-bundle/skills/mcap-analysis/scripts")
PY = sys.executable
TOL = 0.15  # 秒

def run_locator(rec, field_spec, cond_args):
    cmd = [PY, str(S/"mcap_event_locator.py"), str(rec), "--field", field_spec] + cond_args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        return {"error": p.stderr.strip()[:150]}
    return json.loads(p.stdout)

def rec_start(rec):
    import struct
    p = subprocess.run([PY, str(S/"mcap_case_manifest.py"), str(rec), "--case-id", "t"],
                       capture_output=True, text=True)
    return json.loads(p.stdout)["recording"]["start_secs"]

def parse_pred(pred):
    parts = pred.split(None, 1)
    op = parts[0]
    if op == "regex":
        return ["--condition", "regex", "--pattern", parts[1]]
    if op in ("lt","gt","lte","gte"):
        return ["--condition", op, "--threshold", parts[1]]
    if op in ("eq","ne"):
        return ["--condition", op, "--value", parts[1]]
    return None

rows = []
for case_file in sorted(D.glob("expected_manager/*.json")):
    name = case_file.stem
    exp = json.loads(case_file.read_text())
    rec = D/"recordings"/f"{name}.mcap"
    anchor = exp.get("anchor") or {}
    verdict, detail = "?", ""
    if not anchor or anchor.get("first_bad") is None:
        # all-good 或 bad_from_start/missing 类：跑常见谓词确认无误报
        out = run_locator(rec, "/functions/parking_pnc/apa_decision:stop_distance",
                          ["--condition","lt","--threshold","0.15","--debounce-count","2"])
        st = out.get("results",[{}])[0].get("status","err") if "error" not in out else out["error"]
        expected_status = exp.get("status") or exp.get("note") or "no-anchor"
        verdict = "PASS" if st in ("all_good","no_classifiable_samples","bad_from_start") else "CHECK"
        detail = f"expected={expected_status} got={st}"
    else:
        t0 = rec_start(rec)
        cond = parse_pred(anchor["predicate"])
        out = run_locator(rec, f'{anchor["topic"]}:{anchor["field"]}', cond + ["--debounce-count","1"])
        if "error" in out:
            verdict, detail = "FAIL", out["error"]
        else:
            r = out["results"][0]
            fb = r.get("first_bad")
            if fb is None:
                verdict, detail = "FAIL", f'status={r["status"]} 未定位到 (期望 {anchor["first_bad"]}s)'
            else:
                rel = fb["time"] - t0
                ok = abs(rel - anchor["first_bad"]) <= TOL
                lg = r.get("last_good")
                lg_rel = (lg["time"] - t0) if lg else None
                lg_ok = anchor.get("last_good") is None or (lg_rel is not None and abs(lg_rel - anchor["last_good"]) <= TOL)
                verdict = "PASS" if ok and lg_ok else "FAIL"
                detail = f'first_bad {rel:.2f}s (期望 {anchor["first_bad"]}) last_good {lg_rel if lg_rel is None else round(lg_rel,2)} (期望 {anchor.get("last_good")})'
    rows.append((name, verdict, detail))

print(f"{'案例':44s} 结果  详情")
for n,v,d in rows:
    print(f"{n:44s} {v:5s} {d}")
p = sum(1 for _,v,_ in rows if v=="PASS")
print(f"\n定位层: {p}/{len(rows)} PASS")
