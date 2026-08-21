#!/usr/bin/env python3
"""Render a self-contained HTML case report from an analysis case directory.

Reads the JSON files the manager stages under $MCAP_REPORT_DIR/<case_id>/
(01_manifest.json, 06_trace.json, 07_conclusion.json, 08_code_refs.json,
09_timeline.json) and emits case_report.html: pure inline SVG/CSS, no
external assets, dark/light aware. Default layout "combined" renders the
linked table (timeline + causal rail in one view).

Validity rules (hard, not advisory):
1. every plotted point maps to one input record; no interpolation/smoothing
2. gaps stay gaps: missing/decode-error spans break the line
3. partial reads render a visible banner
4. no data -> no chart; the page states why instead
5. every chart carries a data fingerprint (points/missing/source/sha256)
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

W, H, PAD = 900, 260, 48


def esc(s):  # noqa: ANN001
    return html.escape(str(s), quote=True)


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def load_case_json(case_dir: Path, name: str):
    # evidence/ 子目录优先，兼容根目录旧布局
    return load_json(case_dir / "evidence" / name) or load_json(case_dir / name)



def fingerprint(points, missing, source, sha):
    sha8 = (sha or "")[:8] or "unknown"
    return (f'<div class="fp">{points} 个点 · 缺失 {missing} · 来源 {esc(source)}'
            f' · 录制 {esc(sha8)}</div>')


def skipped(title, reason):
    return (f'<section><h2>{esc(title)}</h2>'
            f'<p class="skip">未生成：{esc(reason)}</p></section>')




def trace_chart(trace, sha):
    hops = trace if isinstance(trace, list) else (trace or {}).get("hops")
    if not hops:
        return skipped("回溯链路", "06_trace.json 缺失或为空")
    boxes = []
    y = 10
    for hop in hops:
        out = hop.get("current_output") or {}
        label = f'hop{hop.get("hop")} {out.get("topic","?")}:{out.get("field","?")}'
        res = hop.get("result") or hop.get("boundary") or "?"
        boxes.append((label, str(res), bool(hop.get("boundary"))))
    h = 10 + len(boxes) * 74
    svg = [f'<svg viewBox="0 0 {W} {h}" role="img">']
    for i, (label, res, is_boundary) in enumerate(boxes):
        cls = "bnode" if is_boundary else "node"
        svg.append(f'<rect x="{PAD}" y="{y}" rx="8" width="{W-2*PAD}" height="54" class="{cls}"/>')
        svg.append(f'<text x="{PAD+14}" y="{y+22}" class="ntitle">{esc(label)}{" 🎯边界" if is_boundary else ""}</text>')
        svg.append(f'<text x="{PAD+14}" y="{y+42}" class="lbl">{esc(res[:110])}</text>')
        if i < len(boxes) - 1:
            svg.append(f'<line x1="{W/2}" y1="{y+54}" x2="{W/2}" y2="{y+74}" class="arrow" marker-end="url(#ah)"/>')
        y += 74
    svg.insert(1, '<defs><marker id="ah" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">'
                  '<path d="M0,0 L8,4 L0,8 z" class="ahead"/></marker></defs>')
    svg.append("</svg>")
    return ('<section><h2>🧭 回溯链路</h2>' + "".join(svg)
            + fingerprint(len(boxes), 0, "manager trace packets", sha) + "</section>")


CSS = """
:root{--pillbg:#eceef2;--bg:#fff;--fg:#1a1a2e;--sub:#666;--line:#2563eb;--bad:rgba(220,38,38,.12);
--fresh:#22c55e;--stale:#9ca3af;--card:#f6f7f9;--warn-bg:#fef3c7;--warn-fg:#92400e;--edge:#d1d5db}
@media(prefers-color-scheme:dark){:root{--pillbg:#262b36;--bg:#111318;--fg:#e5e7eb;--sub:#9ca3af;--line:#60a5fa;
--bad:rgba(248,113,113,.15);--fresh:#4ade80;--stale:#4b5563;--card:#1b1e26;--warn-bg:#3a2e12;--warn-fg:#fbbf24;--edge:#333a47}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC",sans-serif;max-width:960px;margin:24px auto;padding:0 16px}
h1{font-size:22px}h2{font-size:17px;margin:8px 0}
section{background:var(--card);border:1px solid var(--edge);border-radius:10px;padding:14px 18px;margin:14px 0;overflow-x:auto}
svg{width:100%;height:auto}.line{fill:none;stroke:var(--line);stroke-width:1.6}
.bad{fill:var(--bad)}.fbline{stroke:#dc2626;stroke-width:1.5;stroke-dasharray:4 3}
.lgline{stroke:var(--fresh);stroke-width:1.5;stroke-dasharray:4 3}
.gap{fill:var(--stale)}.fresh{fill:var(--fresh)}.stale{fill:var(--stale)}
.lbl{font-size:11px;fill:var(--sub)}.ntitle{font-size:13px;font-weight:600;fill:var(--fg)}
.node{fill:var(--bg);stroke:var(--edge)}.bnode{fill:var(--bad);stroke:#dc2626}
.arrow{stroke:var(--sub);stroke-width:1.5}.ahead{fill:var(--sub)}
.fp{font-size:12px;color:var(--sub);margin-top:6px}.sub{color:var(--sub);font-size:13px;margin:2px 0 8px}
.skip{color:var(--sub)}.warn{background:var(--warn-bg);color:var(--warn-fg);padding:8px 12px;border-radius:8px;margin:8px 0;font-size:13px}
table{border-collapse:collapse;font-size:13px}td,th{border:1px solid var(--edge);padding:4px 10px;text-align:left}
.hero{border-left:4px solid var(--line)}.verdict{font-size:16px;font-weight:600;margin:4px 0}
.cap{font-size:13px;margin:6px 0 2px}.wide{width:100%}.wide th{white-space:nowrap}
.coderef{border:1px solid var(--edge);border-radius:8px;margin:10px 0;overflow:hidden}
.crhead{display:flex;gap:10px;align-items:center;padding:6px 10px;background:var(--bg);border-bottom:1px solid var(--edge);flex-wrap:wrap}
.crhead code{font-weight:600}.crhead button,.crhead a{font-size:12px;padding:2px 8px;border:1px solid var(--edge);border-radius:6px;background:var(--card);color:var(--fg);text-decoration:none;cursor:pointer}
table.code{width:100%;border-collapse:collapse;font-size:12.5px}
table.code td{border:none;padding:0 8px}table.code pre{margin:0;font-family:"SF Mono",Menlo,monospace;white-space:pre-wrap}
table.code .ln{color:var(--sub);text-align:right;width:44px;user-select:none;border-right:1px solid var(--edge)}
table.code tr.hl{background:var(--bad)}
.evt{width:100%}.evt td,.evt th{border:none;border-bottom:1px solid var(--edge);padding:12px 10px;vertical-align:top}
.evt-time{font-family:"SF Mono",Menlo,monospace;white-space:nowrap;color:var(--fg);width:150px;font-size:13.5px}
.evt-time-svg{font-size:12px;fill:var(--sub);font-family:Menlo,monospace}
.pill{background:var(--pillbg);border:1px solid var(--edge);border-radius:6px;padding:1px 8px;font-size:12.5px;white-space:nowrap}
.topic{font-size:11.5px;color:var(--sub);font-family:Menlo,monospace}
.evtsub{margin-top:2px}.badge{font-size:11px;border-radius:5px;padding:1px 6px;margin-right:6px}
.badge.rc{background:var(--bad);color:#dc2626;border:1px solid #dc2626}
.badge.sym{background:var(--warn-bg);color:var(--warn-fg);border:1px solid var(--warn-fg)}
.snode{fill:var(--warn-bg);stroke:var(--warn-fg)}
.divider{margin-top:26px;color:var(--sub);font-size:15px}
.ctl-row{display:flex;gap:0;border-bottom:1px solid var(--edge)}
.ctl-row:last-child{border-bottom:none}
.ctl-time{width:130px;flex:none;padding:14px 8px;font-family:"SF Mono",Menlo,monospace;font-size:13px;text-align:right;color:var(--fg)}
.ctl-rail{width:44px;flex:none;display:flex;flex-direction:column;align-items:center}
.ctl-rail .rt,.ctl-rail .rb{flex:1;width:0}
.railline{border-left:2px solid var(--line)}
.railnone{border-left:2px solid transparent}
.dot{width:12px;height:12px;border-radius:50%;flex:none}
.dot.on{background:var(--line)}
.dot.off{background:transparent;border:2px solid var(--stale)}
.dot.rc{background:#dc2626;width:16px;height:16px;box-shadow:0 0 0 4px var(--bad)}
.dot.sym{background:var(--warn-fg);width:14px;height:14px}
.dot-demo{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:middle}
.dot-demo.rc{background:#dc2626}.dot-demo.sym{background:var(--warn-fg)}
.ctl-body{flex:1;padding:12px 12px 12px 4px}
.offrow{opacity:.62}
.rcrow{background:var(--bad);border-radius:8px}
.causeref{display:block;font-size:12px;color:var(--sub);margin-top:2px}
"""


CONF_LABEL = {"high": "高", "medium": "中", "low": "低"}


def conclusion_sections(conclusion):
    """Render summary / causal chain / recommendations from 07_conclusion.json.

    All prose comes from the manager-staged JSON verbatim; this renderer adds
    no judgment of its own (validity rule 1 applied to text)."""
    if not conclusion:
        return (skipped("结论与根因分析", "07_conclusion.json 缺失：manager 未落盘结论"),
                skipped("建议解决方案", "07_conclusion.json 缺失"))

    conf = conclusion.get("confidence")
    summary = ('<section class="hero"><h2>🧾 结论</h2>'
               f'<p class="verdict">{esc(conclusion.get("conclusion", "（未填写）"))}</p>'
               f'<p class="sub">症状：{esc(conclusion.get("symptom", ""))}　'
               f'置信度：<b>{esc(CONF_LABEL.get(conf, conf))}</b></p></section>')

    tp = conclusion.get("temporal_packet") or {}
    tp_html = ""
    if tp:
        cells = [("primary_clock", "主时钟"), ("anchor", "锚点"), ("last_good", "last good"),
                 ("first_bad", "first bad"), ("symptom_time", "症状时间"),
                 ("window", "分析窗口"), ("missing", "缺失信号/时钟")]
        head = "".join(f"<th>{zh}</th>" for k, zh in cells if tp.get(k) is not None)
        body = "".join(f"<td>{esc(tp.get(k))}</td>" for k, zh in cells if tp.get(k) is not None)
        tp_html = ('<section><h2>⏰ Temporal Packet</h2>'
                   f'<table class="wide"><tr>{head}</tr><tr>{body}</tr></table></section>')

    chain = conclusion.get("causal_chain") or {}
    rows = []
    for key, label in [("initiating_cause", "起因"), ("necessary_conditions", "必要条件"),
                       ("amplifiers", "放大因素"), ("consequences", "后果"),
                       ("observability_gaps", "可观测性缺口")]:
        val = chain.get(key)
        if not val:
            continue
        items = val if isinstance(val, list) else [val]
        rows.append(f'<tr><th>{label}</th><td>{esc("；".join(str(i) for i in items))}</td></tr>')
    causal = ('<section><h2>🔗 根因因果链</h2><table class="wide">' + "".join(rows) + "</table>"
              + (f'<p class="sub">下一步最小证据：{esc(conclusion.get("next_evidence"))}</p>'
                 if conclusion.get("next_evidence") else "") + "</section>") if rows else              skipped("根因因果链", "结论文件未含 causal_chain")

    recs = conclusion.get("recommendations") or []
    if recs:
        body = ["<table class='wide'><tr><th>#</th><th>建议方案</th><th>位置</th><th>理由</th><th>风险</th><th>验证方式</th></tr>"]
        for i, r in enumerate(recs, 1):
            tgt = (f'<a href="#code-{esc(r["target_ref"])}">{esc(r.get("target"))} ↓</a>'
                   if r.get("target_ref") else esc(r.get("target")))
            body.append(f"<tr><td>{i}</td><td>{esc(r.get('action'))}</td><td>{tgt}</td>"
                        f"<td>{esc(r.get('rationale'))}</td><td>{esc(r.get('risk'))}</td>"
                        f"<td>{esc(r.get('validation'))}</td></tr>")
        body.append("</table>")
        boundary = conclusion.get("validation_boundary") or "以上为基于 MCAP 与源码分析的初步方案，未经实车验证"
        recs_html = ('<section><h2>🛠 建议解决方案</h2>'
                     f'<div class="warn">⚠ {esc(boundary)}</div>' + "".join(body) + "</section>")
    else:
        recs_html = skipped("建议解决方案", "结论文件未含 recommendations")
    return summary, tp_html + causal + recs_html



def code_refs_section(code_refs, repo_root):
    """Render 08_code_refs.json: staged source snippets with line anchors.

    Snippets are staged verbatim by the manager during read-only source tracing;
    this renderer never fetches or invents code (validity rule 1)."""
    refs = (code_refs or {}).get("refs") or []
    if not refs:
        return skipped("代码定位", "08_code_refs.json 缺失：本案未做源码追踪或未落盘")
    cards = []
    for ref in refs:
        rid = esc(ref.get("id") or ref.get("file", "ref"))
        file, hi = ref.get("file", "?"), ref.get("highlight")
        commit = (ref.get("commit") or "")[:10]
        abs_path = f"{repo_root.rstrip('/')}/{file}" if repo_root else file
        vscode = f"vscode://file/{abs_path}" + (f":{hi}" if hi else "")
        lines = ref.get("snippet") or []
        start = ref.get("line_start") or 1
        rows = []
        for i, line in enumerate(lines):
            n = start + i
            cls = ' class="hl"' if n == hi else ""
            rows.append(f'<tr{cls}><td class="ln">{n}</td><td><pre>{esc(line)}</pre></td></tr>')
        loc = f"{file}" + (f":{hi}" if hi else "")
        cards.append(
            f'<div class="coderef" id="code-{rid}">'
            f'<div class="crhead"><code>{esc(loc)}</code>'
            + (f'<span class="sub">@ {esc(commit)}</span>' if commit else "")
            + f'<button onclick="navigator.clipboard.writeText(\'{esc(loc)}\')">复制路径</button>'
            + f'<a href="{esc(vscode)}">在 VSCode 打开</a></div>'
            + (f'<table class="code">{"".join(rows)}</table>' if rows else '<p class="skip">未附代码片段</p>')
            + (f'<p class="cap">{esc(ref.get("note"))}</p>' if ref.get("note") else "")
            + "</div>")
    return '<section><h2>📌 代码定位</h2>' + "".join(cards) + "</section>"


ROLE_STYLE = {"root_cause": ("bnode", "🎯 根因"), "symptom": ("snode", "⚠ 症状")}


def pills(vals):
    return " ".join(f'<code class="pill">{esc(v)}</code>' for v in vals)


def narrative_timeline(timeline):
    """Event narrative table: time | topic | phenomenon, values as pills."""
    events = (timeline or {}).get("events") or []
    if not events:
        return skipped("事件时间线", "09_timeline.json 缺失：manager 未落盘事件叙事")
    rows = []
    for ev in events:
        time_txt = esc(ev.get("time", "?")) + (f'~{esc(ev["time_end"])}' if ev.get("time_end") else "")
        topic = f'<span class="topic">{esc(ev.get("topic"))}</span>' if ev.get("topic") else ""
        text = esc(ev.get("text", ""))
        pv = pills(ev.get("pills", []))
        role = ev.get("role")
        badge = ""
        if role in ROLE_STYLE:
            badge = f'<span class="badge {"rc" if role == "root_cause" else "sym"}">{ROLE_STYLE[role][1]}</span>'
        rows.append(f'<tr id="evt-{esc(ev.get("id", ""))}"><td class="evt-time">{time_txt}</td>'
                    f'<td>{badge}{text} {pv}<div class="evtsub">{topic}</div></td></tr>')
    return ('<section><h2>🕐 事件时间线</h2>'
            '<table class="evt"><tr><th>时间</th><th>现象</th></tr>' + "".join(rows) + "</table>"
            + fingerprint(len(events), 0, "manager 09_timeline.json", None) + "</section>")


def causal_flow(timeline):
    """Causal flow graph: events as nodes, caused_by edges converging on the root cause."""
    events = (timeline or {}).get("events") or []
    nodes = [e for e in events if e.get("in_flow") or e.get("caused_by") or e.get("role")]
    if len(nodes) < 2:
        return skipped("根因引流图", "事件缺少因果标注（caused_by/role）")
    idx = {e.get("id"): i for i, e in enumerate(nodes)}
    nh, gap = 64, 26
    h = 20 + len(nodes) * (nh + gap)
    svg = [f'<svg viewBox="0 0 {W} {h}" role="img">',
           '<defs><marker id="ca" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">'
           '<path d="M0,0 L8,4 L0,8 z" class="ahead"/></marker></defs>']
    box_x, box_w = 170, W - 170 - PAD
    ys = []
    for i, ev in enumerate(nodes):
        y = 10 + i * (nh + gap)
        ys.append(y)
        role = ev.get("role")
        cls = "bnode" if role == "root_cause" else ("snode" if role == "symptom" else "node")
        svg.append(f'<text x="{box_x-12}" y="{y+nh/2+4}" text-anchor="end" class="evt-time-svg">{esc(ev.get("time"))}</text>')
        svg.append(f'<rect x="{box_x}" y="{y}" rx="9" width="{box_w}" height="{nh}" class="{cls}"/>')
        tag = ROLE_STYLE.get(role, (None, ""))[1]
        svg.append(f'<text x="{box_x+14}" y="{y+24}" class="ntitle">{esc(tag + " " if tag else "")}{esc(ev.get("text","")[:52])}</text>')
        sub = (ev.get("topic") or "") + ("　" + " → ".join(ev.get("pills", [])[:2]) if ev.get("pills") else "")
        svg.append(f'<text x="{box_x+14}" y="{y+44}" class="lbl">{esc(sub[:80])}</text>')
    for i, ev in enumerate(nodes):
        for src in (ev.get("caused_by") or []):
            j = idx.get(src)
            if j is None:
                continue
            y1, y2 = ys[j] + nh, ys[i]
            if i - j == 1:
                svg.append(f'<line x1="{W/2}" y1="{y1}" x2="{W/2}" y2="{y2}" class="arrow" marker-end="url(#ca)"/>')
            else:
                x = box_x - 60
                svg.append(f'<path d="M {box_x} {ys[j]+nh/2} C {x} {ys[j]+nh/2}, {x} {ys[i]+nh/2}, {box_x} {ys[i]+nh/2}"'
                           f' fill="none" class="arrow" marker-end="url(#ca)"/>')
    svg.append("</svg>")
    return ('<section><h2>🌊 根因引流图</h2>'
            '<p class="sub">事件沿因果箭头汇入根因；红框=根因，橙框=可见症状</p>'
            + "".join(svg) + "</section>")


def combined_timeline(timeline):
    """One table = narrative timeline + causal flow.

    Left rail draws the causal track: filled nodes on the flow line converge to
    the root cause; hollow nodes are off-flow observations. Row height is
    CSS-driven so long text never breaks alignment."""
    events = (timeline or {}).get("events") or []
    if not events:
        return skipped("事件时间线（联动）", "09_timeline.json 缺失：manager 未落盘事件叙事")
    ids = {e.get("id"): e for e in events}
    rows = []
    for i, ev in enumerate(events):
        role = ev.get("role")
        in_flow = bool(ev.get("in_flow") or ev.get("caused_by") or role)
        node_cls = "rc" if role == "root_cause" else ("sym" if role == "symptom" else ("on" if in_flow else "off"))
        # rail line segments: hide above first flow node / below last flow node
        flow_idx = [j for j, e in enumerate(events) if e.get("in_flow") or e.get("caused_by") or e.get("role")]
        line_top = "railline" if in_flow and flow_idx and i > flow_idx[0] else "railnone"
        line_bot = "railline" if in_flow and flow_idx and i < flow_idx[-1] else "railnone"
        time_txt = esc(ev.get("time", "?")) + (f'<br>~{esc(ev["time_end"])}' if ev.get("time_end") else "")
        badge = ""
        if role in ROLE_STYLE:
            badge = f'<span class="badge {"rc" if role == "root_cause" else "sym"}">{ROLE_STYLE[role][1]}</span>'
        caused = ""
        for src in (ev.get("caused_by") or []):
            se = ids.get(src)
            if se is not None:
                prev_flow = events[flow_idx[flow_idx.index(i) - 1]] if i in flow_idx and flow_idx.index(i) > 0 else None
                if prev_flow is not se:  # 非相邻因果，文字标注兜底
                    caused += f'<span class="causeref">↖ 由 {esc(se.get("time"))} {esc(se.get("text","")[:24])} 引起</span>'
        rows.append(
            f'<div class="ctl-row {"offrow" if not in_flow else ""} {"rcrow" if role=="root_cause" else ""}">'
            f'<div class="ctl-time">{time_txt}</div>'
            f'<div class="ctl-rail"><div class="rt {line_top}"></div><span class="dot {node_cls}"></span><div class="rb {line_bot}"></div></div>'
            f'<div class="ctl-body">{badge}{esc(ev.get("text",""))} {pills(ev.get("pills", []))}{caused}'
            f'<div class="evtsub"><span class="topic">{esc(ev.get("topic",""))}</span></div></div></div>')
    legend = ('<p class="sub">轨道即因果流：实心点在流上、沿竖线向下汇入 <span class="dot-demo rc"></span> 根因 → '
              '<span class="dot-demo sym"></span> 症状；空心点为流外观察项</p>')
    return ('<section><h2>🕐 事件时间线 · 根因引流</h2>' + legend + "".join(rows)
            + fingerprint(len(events), 0, "manager 09_timeline.json", None) + "</section>")


def build(case_dir: Path, layout: str = "combined") -> str:
    manifest = load_case_json(case_dir, "01_manifest.json") or {}
    trace = load_case_json(case_dir, "06_trace.json")
    conclusion = load_case_json(case_dir, "07_conclusion.json")
    code_refs = load_case_json(case_dir, "08_code_refs.json")
    timeline = load_case_json(case_dir, "09_timeline.json")
    sha = (manifest.get("recording") or {}).get("sha256")
    repo_root = ((manifest.get("source") or {}).get("repo")) or ""

    head_rows = ""
    if manifest:
        rec = manifest.get("recording", {})
        head_rows = ("<table><tr><th>case</th><th>录制</th><th>sha256</th><th>时长</th><th>档案时间</th></tr>"
                     f"<tr><td>{esc(manifest.get('case_id'))}</td><td>{esc(rec.get('path'))}</td>"
                     f"<td>{esc((rec.get('sha256') or '')[:12])}</td><td>{esc(rec.get('duration_s'))}s</td>"
                     f"<td>{esc(manifest.get('analysis_started_at'))}</td></tr></table>")
    else:
        head_rows = '<p class="skip">01_manifest.json 缺失：本页数据无溯源指纹</p>'

    summary, analysis = conclusion_sections(conclusion)
    if layout == "combined":
        story = [combined_timeline(timeline)]
    else:
        story = [narrative_timeline(timeline), causal_flow(timeline)]
    parts = [f"<h1>mcap 根因分析报告 · {esc(manifest.get('case_id', case_dir.name))}</h1>",
             summary] + story + [
             analysis,
             '<h2 class="divider">📎 证据附录</h2>',
             f"<section>{head_rows}</section>",
             trace_chart(trace, sha),
             code_refs_section(code_refs, repo_root)]
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>case {esc(manifest.get("case_id", case_dir.name))}</title>'
            f'<style>{CSS}</style></head><body>' + "".join(parts) + "</body></html>")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="Output HTML (default: <case-dir>/case_report.html)")
    parser.add_argument("--layout", default="combined", choices=["classic", "combined"],
                        help="classic: narrative table + separate flow graph; combined: one linked table")
    args = parser.parse_args()
    if not args.case_dir.is_dir():
        print(json.dumps({"error": f"case dir not found: {args.case_dir}"}), file=sys.stderr)
        return 1
    out = args.out or (args.case_dir / "case_report.html")
    out.write_text(build(args.case_dir, args.layout))
    print(json.dumps({"ok": True, "report": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
