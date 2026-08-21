#!/usr/bin/env python3
"""Render the structured text report (8-section skeleton) from staged case JSONs.

The agent's job is to stage complete JSONs (07_conclusion, 09_timeline, 06_trace);
this script renders them into the canonical markdown report. Because `topic` is a
required field in 09_timeline events, every timeline row carries its source by
construction — prompt compliance is no longer involved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROLE = {"root_cause": "🎯 根因", "symptom": "⚠ 症状"}


def load(case_dir: Path, name: str):
    # 中间 JSON 收纳在 evidence/ 子目录（案例根目录只留两个报告）；兼容旧的根目录布局
    for p in (case_dir / "evidence" / name, case_dir / name):
        try:
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return None


def row(cells):
    return "| " + " | ".join(str(c) if c not in (None, "") else "-" for c in cells) + " |"


def _dict(value, defects, name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        defects.append(f"{name} 类型错误（应为对象，实为 {type(value).__name__}），已忽略")
        return {}
    return value


def _dict_list(value, defects, name):
    if not isinstance(value, list):
        if value:
            defects.append(f"{name} 类型错误（应为数组），已忽略")
        return []
    bad = [x for x in value if not isinstance(x, dict)]
    if bad:
        defects.append(f"{name} 含 {len(bad)} 个非对象元素，已跳过")
    return [x for x in value if isinstance(x, dict)]


def build(case_dir: Path) -> str:
    defects: list = []
    manifest = _dict(load(case_dir, "01_manifest.json"), defects, "01_manifest.json")
    conclusion = _dict(load(case_dir, "07_conclusion.json"), defects, "07_conclusion.json")
    timeline = _dict(load(case_dir, "09_timeline.json"), defects, "09_timeline.json")
    trace = _dict(load(case_dir, "06_trace.json"), defects, "06_trace.json")
    rec = manifest.get("recording")
    sha = ((rec.get("sha256") if isinstance(rec, dict) else "") or "")[:12]
    out = [f"> 报告样式：structured | case: {manifest.get('case_id', case_dir.name)} | 录制 sha256 前12位: {sha or '未知'}", ""]

    root_cause = conclusion.get("root_cause")
    if not root_cause:
        defects.append("07_conclusion.json 缺 root_cause（必填）：需给出明确根因判定单句后重跑")
        root_cause = "❌ 缺失 — agent 未给出根因判定，需补 07_conclusion.json 的 root_cause 字段"
    out += ["## 1 根因判定",
            f"**根因：{root_cause}**（置信度：{conclusion.get('confidence', '-')}）",
            f"- 边界：{conclusion.get('boundary', '-')}",
            f"- 升级为确认还需：{conclusion.get('upgrade_evidence', conclusion.get('next_evidence', '-'))}", ""]

    tp = _dict(conclusion.get("temporal_packet"), defects, "temporal_packet")
    out += ["## 2 Temporal Packet",
            row(["主时钟", "锚点 topic.field", "last_good", "first_bad", "症状时间", "分析窗口", "缺失信号"]),
            row(["---"] * 7),
            row([tp.get("primary_clock"), tp.get("anchor"), tp.get("last_good"), tp.get("first_bad"),
                 tp.get("symptom_time"), tp.get("window"), tp.get("missing")]) if tp else "无", ""]

    events = _dict_list(timeline.get("events"), defects, "timeline.events")
    out += ["## 3 事件时间线", row(["时间", "topic:field", "现象", "角色"]), row(["---"] * 4)]
    if events:
        missing_topic = [e for e in events if not e.get("topic")]
        if missing_topic:
            defects.append(f"09_timeline.json 有 {len(missing_topic)} 个事件缺 topic（必填），已用占位标出")
        for e in events:
            if not e.get("topic"):
                e = dict(e, topic="❌ 缺来源")
            t = str(e.get("time", "?")) + (f"~{e['time_end']}" if e.get("time_end") else "")
            phen = e.get("text", "") + ("　" + " ".join(f"`{p}`" for p in e.get("pills", [])) if e.get("pills") else "")
            out.append(row([t, e.get("topic"), phen, ROLE.get(e.get("role"), "-")]))
    else:
        out.append("无")
    out.append("")

    gating = _dict_list(conclusion.get("gating_checks"), defects, "gating_checks")
    out += ["## 3b 门控条件核查表", row(["门控条件", "录制字段或 proxy", "窗口内翻转次数", "首次持续满足时间"]), row(["---"] * 4)]
    out += [row([g.get("condition"), g.get("signal"), g.get("flips"), g.get("first_sustained")]) for g in gating] or ["不适用"]
    out.append("")

    hops = _dict_list(trace.get("hops"), defects, "trace.hops")
    out += ["## 4 回溯轨迹", row(["hop", "窗口", "消费者输出", "last-good/first-bad", "检查", "结果", "耗时 s"]), row(["---"] * 7)]
    for h in hops:
        o = h.get("current_output") or {}
        out.append(row([h.get("hop"), h.get("window"), f"{o.get('topic', '?')}:{o.get('field', '?')}",
                        h.get("last_good_first_bad"), h.get("check"), h.get("result") or h.get("boundary"),
                        h.get("duration_secs")]))
    if not hops:
        out.append("无")
    out.append("")

    chain = _dict(conclusion.get("causal_chain"), defects, "causal_chain")
    out += ["## 5 根因因果链", row(["起因", "必要条件", "放大因素", "后果", "可观测性缺口"]), row(["---"] * 5),
            row([chain.get(k) if not isinstance(chain.get(k), list) else "；".join(chain.get(k))
                 for k in ("initiating_cause", "necessary_conditions", "amplifiers", "consequences", "observability_gaps")])
            if chain else "无", ""]

    recs = _dict_list(conclusion.get("recommendations"), defects, "recommendations")
    out += ["## 6 建议方案",
            f"> ⚠ {conclusion.get('validation_boundary', '基于 MCAP 与源码的初步方案，未经实车验证')}", "",
            row(["#", "方案", "代码/配置位置", "理由", "风险", "验证方式"]), row(["---"] * 6)]
    out += [row([i, r.get("action"), r.get("target"), r.get("rationale"), r.get("risk"), r.get("validation")])
            for i, r in enumerate(recs, 1)] or ["无"]
    out.append("")

    out += ["## 7 下一步最小证据", conclusion.get("next_evidence", "无"), ""]

    timing = _dict_list(conclusion.get("timing_breakdown"), defects, "timing_breakdown")
    out += ["## 8 耗时分解", row(["step", "tool/agent", "wall_secs", "notes"]), row(["---"] * 4)]
    out += [row([t_.get("step"), t_.get("tool"), t_.get("wall_secs"), t_.get("notes")]) for t_ in timing] \
        or [row(["数据获取", "unmeasured", "unmeasured", "07_conclusion.json 未提供 timing_breakdown"])]
    if defects:
        out = ["> ⛔ **本报告不完整** — " + "；".join(defects), ""] + out
    return ("\n".join(out) + "\n", defects)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="default: <case-dir>/report.md")
    args = parser.parse_args()
    text, defects = build(args.case_dir)
    out = args.out or (args.case_dir / "report.md")
    out.write_text(text)
    print(json.dumps({"ok": not defects, "report": str(out), "defects": defects}, ensure_ascii=False))
    return 2 if defects else 0


if __name__ == "__main__":
    raise SystemExit(main())
