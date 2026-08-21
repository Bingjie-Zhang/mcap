#!/usr/bin/env bash
set -euo pipefail

bundle_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
codex_root="${CODEX_HOME:-${HOME}/.codex}"
dry_run=0
pack="apa"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    --pack) pack="$2"; shift 2 ;;
    *) echo "unknown option: $1 (supported: --pack <name>, --dry-run)" >&2; exit 1 ;;
  esac
done

if [[ -d "${bundle_dir}/packs" ]]; then
  # 开发版：从 pack 渲染 agents 并暂存 registry/术语表
  if [[ ! -f "${bundle_dir}/packs/${pack}/pack.json" ]]; then
    echo "unknown pack '${pack}'; available packs:" >&2
    ls "${bundle_dir}/packs" >&2 || true
    exit 1
  fi
  python3 "${bundle_dir}/render_agents.py" "${pack}" --check
  python3 "${bundle_dir}/render_agents.py" "${pack}" >/dev/null
  cp "${bundle_dir}/packs/${pack}/registry.yaml" \
     "${bundle_dir}/skills/mcap-analysis/references/${pack}_topic_registry.yaml"
  if [[ -f "${bundle_dir}/packs/${pack}/glossary.md" ]]; then
    cp "${bundle_dir}/packs/${pack}/glossary.md" \
       "${bundle_dir}/skills/mcap-analysis/references/${pack}_glossary.md"
  fi
else
  # 用户精简包：agents/ 已预渲染，registry 已在 references/，直接安装
  echo "user bundle: installing pre-rendered agents for pack '${pack}'"
fi

if [[ ! -d "${bundle_dir}/skills/mcap-analysis" || ! -d "${bundle_dir}/agents" ]]; then
  echo "invalid bundle: expected skills/mcap-analysis and agents" >&2
  exit 1
fi

# Files this install will place (relative to codex_root)
manifest=()
while IFS= read -r f; do
  manifest+=("skills/mcap-analysis/${f#"${bundle_dir}/skills/mcap-analysis/"}")
done < <(find "${bundle_dir}/skills/mcap-analysis" -type f)
while IFS= read -r f; do
  manifest+=("agents/${f#"${bundle_dir}/agents/"}")
done < <(find "${bundle_dir}/agents" -type f)

overwrites=()
for rel in "${manifest[@]}"; do
  [[ -e "${codex_root}/${rel}" ]] && overwrites+=("${rel}")
done

echo "install target: ${codex_root}"
echo "files to install: ${#manifest[@]}"
if [[ ${#overwrites[@]} -gt 0 ]]; then
  echo "files that will be OVERWRITTEN (${#overwrites[@]}):"
  printf '  %s\n' "${overwrites[@]}"
fi

if [[ ${dry_run} -eq 1 ]]; then
  echo "dry-run: nothing was copied"
  exit 0
fi

# Backup anything we overwrite, then replace the skill directory atomically-ish
if [[ ${#overwrites[@]} -gt 0 ]]; then
  backup_dir="${codex_root}/backups/mcap-analysis-$(date +%Y%m%d-%H%M%S)"
  for rel in "${overwrites[@]}"; do
    mkdir -p "${backup_dir}/$(dirname "${rel}")"
    cp -a "${codex_root}/${rel}" "${backup_dir}/${rel}"
  done
  echo "backup written: ${backup_dir}"
fi

mkdir -p "${codex_root}/skills" "${codex_root}/agents"
rm -rf "${codex_root}/skills/mcap-analysis"
cp -a "${bundle_dir}/skills/mcap-analysis" "${codex_root}/skills/"
cp -a "${bundle_dir}/agents/." "${codex_root}/agents/"

cat "${bundle_dir}/VERSION" 2>/dev/null && cp "${bundle_dir}/VERSION" "${codex_root}/skills/mcap-analysis/VERSION" 2>/dev/null || true
echo "installed skill: ${codex_root}/skills/mcap-analysis"
echo "installed pack: ${pack} (entry agent: ${pack}_problem_analysis)"
echo "installed agents:"; ls "${codex_root}/agents/${pack}_"*.toml 2>/dev/null | sed 's/^/  /'
echo "NOTE: v3 renamed the entry agent mcap_problem_analysis -> <pack>_problem_analysis; remove the old TOML manually if it lingers."
echo "restart Codex or create a new task to reload them"
