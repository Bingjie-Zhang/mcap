# APA MCAP Analysis Bundle

本压缩包包含当前的 `mcap-analysis` skill 以及配套的 6 个只读 APA/MCAP agent。
安装后，skill 放在 Codex 的 `skills/mcap-analysis`，agent 放在 `agents/`。

## 内容

- `skills/mcap-analysis/SKILL.md`：时间点优先、按 producer-consumer 边界逐跳回溯的 MCAP 分析流程。
- `skills/mcap-analysis/scripts/`：inventory、事件定位、窄窗口切片和帧关联脚本。
- `skills/mcap-analysis/references/apa_topic_registry.yaml`：APA topic/field 路由提示。
- `agents/`：`apa_manager`、`mcap_problem_analysis` 以及 perception/planning/decision/control 专项 agent。

## 安装

在解压后的目录执行：

```bash
./install.sh
```

默认安装到 `${CODEX_HOME:-$HOME/.codex}`。如果使用了自定义 Codex 目录：

```bash
CODEX_HOME=/path/to/codex ./install.sh
```

安装后重启或新建 Codex 任务，使 skill 和 agent 配置重新加载。

## 运行依赖

- Python 3.10+；
- Python 包 `mcap`；
- `mcap` CLI（`mcap_frame_correlation.py` 和 `mcap_slice.py` 会调用 `mcap cat`）；
- 若使用 ROS/自定义 schema，目标环境仍需提供对应解码能力。

例如先检查：

```bash
python3 -c 'import mcap; print("python mcap ok")'
mcap --help
```

所有 agent 都是只读分析配置，不会修改 MCAP、源码、配置或 Git，也不包含任何本机录包和凭据。


## 用户安装与更新（v3.6+）

安装（每次拿到新包重复这三步即可，约 1 分钟）：

    tar -xzf mcap-analysis-bundle-vX.Y.Z-日期.tar.gz
    cd mcap-analysis-bundle && ./install.sh --pack apa
    # 重启 Codex（或新建任务）

    # 重要：请在报告目录下启动 Codex 会话（不要在源码目录启动）：
    mkdir -p ~/mcap-reports && cd ~/mcap-reports && codex
    # 原因：入口/编排 agent 需要写报告文件（workspace-write 沙箱只放开当前目录），
    # 在报告目录启动 = 只有报告目录可写，源码依然只读

日常使用：@apa_problem_analysis 分析 /路径/xxx.mcap，症状：……
报告输出：~/mcap-reports/<案例号>/（report.md + case_report.html）
出问题反馈：把 ~/mcap-reports/<案例号>/ 文件夹发给张冰洁。

薄壳架构说明：agent 配置极小且稳定，规则都在 skill 文件里——因此安装新包
通常无需删旧 agent，直接覆盖安装即可。

## Domain Pack architecture (v3)

- `core/templates/` — agent prompt skeletons (methodology lives here, business-free)
- `packs/<name>/` — all business knowledge: `pack.json` (domains, routing, fetch), `registry.yaml`, `glossary.md`
- `render_agents.py <pack> [--check]` — pack + templates -> `agents/<pack>_*.toml`
- `./install.sh --pack <name> [--dry-run]` — render, stage registry/glossary, install

Onboard a new business (no code, no prompt writing):

    cp -r packs/apa packs/pnc
    vim packs/pnc/pack.json packs/pnc/registry.yaml   # domains + interface contracts
    python3 render_agents.py pnc --check
    ./install.sh --pack pnc

Functional testing: `python3 tests/run_tests.py` (needs the `mcap` python package; 38 checks
covering scripts, timing, CLI path, manifest, and the render layer incl. a synthetic demo pack).
