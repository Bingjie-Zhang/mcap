#!/usr/bin/env python3
"""Render agent TOMLs for one domain pack: pack knowledge + core templates -> agents/.

Zero dependencies: pack config is JSON, templates use {{KEY}} substitution only.
Usage:
    python3 render_agents.py <pack-name> [--out-dir agents] [--check]
--check renders to memory and verifies contract invariants without writing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "core" / "templates"

# Every rendered agent must still carry these method/contract markers.
# If a template edit loses one, rendering fails loudly instead of shipping a
# degraded prompt.
# 薄壳 agent 的不变量（身份/红线/必读 SKILL）；执行规则的不变量在 SKILL_INVARIANTS 里检查
CONTRACT_INVARIANTS = {
    "entry": ["read-only", "SKILL.md", "根因判定", "FINAL CHECKLIST", "Never claim vehicle safety"],
    "manager": ["read-only", "SKILL.md", "FINAL CHECKLIST", "MANDATORY EXECUTION SEQUENCE", "COMPLETENESS BAR",
                "mcap_event_locator.py", "mcap_report.py", "timestamps"],
    "specialist": ["JSON routing packet", "duration_secs", "observability gap", "proxy",
                   "read-only", "Tool-output semantics", "date +"],
}

# SKILL.md 是规则唯一来源，渲染时同步校验关键规则未丢失
SKILL_INVARIANTS = [
    "mcap_case_manifest.py", "mcap_event_locator.py", "max_age_ms", "Routing packet",
    "duration_secs", "仅跨域边界", "MCAP_REPORT_DIR", "09_timeline.json",
    "mcap_report.py", "翻转计数", "proxy", "版本对齐", "禁止绕开脚本", "root_cause",
    "拍板", "evidence/", "裁决序", "数据获取",
]


def render(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{{" + key + "}}", str(value))
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", result)
    if leftover:
        raise ValueError(f"unfilled template slots: {sorted(set(leftover))}")
    return result


def check_invariants(kind: str, text: str, name: str) -> None:
    missing = [marker for marker in CONTRACT_INVARIANTS[kind] if marker not in text]
    if missing:
        raise ValueError(f"{name}: rendered output lost contract markers: {missing}")
    if text.count('"""') % 2 != 0:
        raise ValueError(f"{name}: unbalanced triple quotes")


def build(pack_name: str) -> dict[str, str]:
    skill = (ROOT / "skills" / "mcap-analysis" / "SKILL.md").read_text()
    missing = [m for m in SKILL_INVARIANTS if m not in skill]
    if missing:
        raise ValueError(f"SKILL.md lost rule markers: {missing}")
    pack_dir = ROOT / "packs" / pack_name
    config = json.loads((pack_dir / "pack.json").read_text())
    reasoning = config.get("reasoning", {})
    base = {
        "REASONING_ENTRY": reasoning.get("entry", "low"),
        "REASONING_MANAGER": reasoning.get("manager", "medium"),
        "REASONING_SPECIALIST": reasoning.get("specialist", "high"),
        "PACK": config["name"],
        "PACK_TITLE": config["title"],
        "FETCH_INSTRUCTIONS": config["fetch_instructions"],
        "ROUTING_HINTS": "\n".join(f"- {hint}" for hint in config["routing_hints"]),
    }
    outputs: dict[str, str] = {}

    text = render((TEMPLATES / "entry.toml.tpl").read_text(), base)
    check_invariants("entry", text, f"{pack_name} entry")
    outputs[f"{config['name']}_problem_analysis.toml"] = text

    text = render((TEMPLATES / "manager.toml.tpl").read_text(), base)
    check_invariants("manager", text, f"{pack_name} manager")
    outputs[f"{config['name']}_manager.toml"] = text

    specialist_tpl = (TEMPLATES / "specialist.toml.tpl").read_text()
    domain_ids = [domain["id"] for domain in config["domains"]]
    for domain in config["domains"]:
        siblings = ", ".join(d for d in domain_ids if d != domain["id"]) or "other domains"
        values = dict(base)
        values.update({
            "DOMAIN": domain["id"],
            "SIBLING_DOMAINS": siblings,
            "CHECK_ASPECTS": domain["check_aspects"],
            "BOUNDARY_LOCATIONS": domain["boundary_locations"],
            "DOMAIN_EXTRA_METHOD": domain.get("domain_extra_method", ""),
            "EVIDENCE_GUIDANCE": domain["evidence_guidance"],
            "MAX_BULLETS": domain.get("max_bullets", 14),
        })
        name = f"{config['name']}_{domain['id']}_analysis.toml"
        text = render(specialist_tpl, values)
        check_invariants("specialist", text, name)
        outputs[name] = text
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack")
    parser.add_argument("--out-dir", default="agents")
    parser.add_argument("--check", action="store_true", help="Render and verify only; write nothing")
    args = parser.parse_args()
    try:
        outputs = build(args.pack)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"render failed: {exc}", file=sys.stderr)
        return 1
    if args.check:
        print(f"check ok: {len(outputs)} agents render cleanly for pack '{args.pack}'")
        return 0
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in outputs.items():
        (out_dir / name).write_text(text)
        print(f"rendered {out_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
