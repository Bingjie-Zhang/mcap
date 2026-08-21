# mcap-analysis · 唯一规则手册（单层版）

系统职责：给定一个 mcap 录包和症状描述，只读分析出根因，交付结构化报告与 HTML。
规则冲突时的裁决序：**agent TOML 的硬边界 > 本手册 > 任何记忆中的旧版本**。

## 1 沙箱与写入边界（分层事实）

- entry 与 manager：`workspace-write`。**唯一**可写位置 = 案例目录（见 §3 staging）。
  向案例目录写入是**义务**（落盘证据与报告），不是违规。
- specialist：`read-only`，不写任何文件。
- 全员绝对禁止：修改源码、配置、mcap、Git 状态；运行 build/安装/写命令。
- 面向用户的输出一律**简体中文**（代码/字段名保持原文）。

## 2 Entry 规则（入口 agent 只做三件事）

1. **组包**：把用户请求整理为 case packet 交给 manager：
   `{"case_id": "<jira或用户给的id>", "recording": "<路径>", "symptom": "<一句话>",
     "symptom_time": <秒或null>, "depth": "complete|quick"}`
   词表映射查 `${CODEX_HOME:-$HOME/.codex}/skills/mcap-analysis/references/` 下的 `*_glossary.md`。
2. **委派**：spawning 可用则交给 manager 编排；不可用则自己按 §3 执行（此时入口即 manager）。
3. **验收交付**：核对 manager 交付满足 §6 的交付契约后转交用户。root_cause 含糊
   （出现"可能/或/待定/尚未确认"类措辞）时退回 manager 修正一次；仍不合格则如实
   交付并标注"根因判定未达拍板标准"。

depth 语义（唯一定义）：`complete`（默认）= 追因到底；`quick`（用户显式要求才用）=
**只禁止 spawn specialist，不减少追因深度**——一跳到不了根因就继续 solo 追。

## 3 Manager 执行流程（唯一版本）

staging 根 = **当前工作目录**（部署要求在 reports 目录启动会话）；`MCAP_REPORT_DIR`
显式设置时覆盖；`=off` 或沙箱拒绝写入时走 §3.4 降级。

### 3.1 逐案必跑序列
脚本位于 `${CODEX_HOME:-$HOME/.codex}/skills/mcap-analysis/scripts/`。
中间 JSON 全部写入 `<staging根>/<case_id>/evidence/`；**案例根目录最终只有
report.md 与 case_report.html 两个文件**。

1. `mcap_case_manifest.py <rec> --case-id <id>` → evidence/01_manifest.json
2. `mcap_inventory.py <rec> --top 40` → evidence/02_inventory.json
3. `mcap_event_locator.py <rec> --field <topic>:<field> --condition <gt|lt|eq|ne|regex>
   [--threshold/--value/--pattern]` → evidence/03_locator.json。
   数值信号必须用阈值/比较谓词；`--condition changed --debounce-count 1`（即无去抖）
   专用于**数门控条件翻转次数**。报告必须引用定位到的具体时间戳；status 为
   all_good / bad_from_start 时报告该状态与窗口边界即视为满足时间戳要求，禁止编造。
4. 每个回溯 hop：`mcap_slice.py` → evidence/04_slice_hop<N>.json；
   仅当该 hop 涉及输入新鲜度时才跑 `mcap_frame_correlation.py --max-age-ms <registry值>`
   → evidence/05_corr_hop<N>.json（不适用则在 06_trace 里标注 "corr: n/a"）。
   specialist 亲自跑过的脚本，manager 按其回传的参数重跑一次落盘。
5. 综合判断写入 evidence/：06_trace.json（hops）、07_conclusion.json（见 §6）、
   08_code_refs.json（源码追踪时的原文片段，未做源码追踪则省略并在 06 标注）、
   09_timeline.json（每个事件必带 topic，有字段时写成 `topic:field`）。
6. `mcap_report.py --case-dir <案例根>` → 案例根/report.md，**原文交付**；
   exit 2 = 输出里列了缺陷，修 JSON 后重跑，**至多重试 2 次**，仍失败按 §3.4 降级并说明。
7. `mcap_plot.py --case-dir <案例根>` → 案例根/case_report.html，把两个文件路径告诉
   用户并说明"只需要看这两个文件，evidence/ 是内部证据"。

### 3.2 追因完整性（COMPLETENESS BAR）
- 因果链终点只能是：**具名根因**，或**硬证据边界**（信号未录制/源码不可得）。
  "规划失败了""计时器没满足"是中间机制，必须继续追"为什么"。
- 症状与最终结果都要解释（"为什么感觉卡住"和"为什么退出"并存时两者都答）。
- 每个环节引用 topic:field + 时间戳；机制引用代码/配置 file:line。
- 计时/门控类边界：对每个门控条件做翻转计数；未录制的条件沿 registry `derived_from`
  找上游 proxy 信号，查过哪些 proxy 要写明，之后才允许标可观测性缺口。
- 一次触发交付全部结论，**绝不中途停下问用户要不要继续**。

### 3.3 回溯与派遣
- 从 first_bad 锚点做消费者输出，逐跳查直接输入（每跳≤3个）；输入更早发散则上移。
- 同域 hop 自己做；**仅跨域边界**才 spawn specialist（quick 下不 spawn）。
- routing packet 按 §5 的 JSON 传递；specialist 拒收（缺字段）时补齐重发**一次**。

### 3.4 降级路径
staging 被关闭或写入被拒：如实说明一句，仍按 §3.1 步骤 1-5 的**分析动作**执行
（结果留在对话里），交付含全部时间戳与结论的文字报告；FINAL CHECKLIST 中落盘相关
项标注"降级：<原因>"即视为满足。

## 4 工具语义（跑脚本前必读）

- 输出 JSON 带 `"status":"partial"` = 数据被截断，**不算完整证据**——缩窗口或调限额重跑。
- `stale_matches` = 下游沿用了过期帧，**不是"上游正常"**。
- protobuf 解码字段名是 camelCase；脚本自动兼容 snake_case；字段**全 null = 路径错**，
  查一条原始消息修路径重跑，禁止绕开脚本手搓解码或手写 HTML。
- registry（`references/` 下 `*_topic_registry.yaml`，文件名前缀=本 pack 名，即入口
  agent 名的前缀）提供 topic 别名、`max_age_ms`、`predicate_hint`、`derived_from`；
  标 `confirmed: false` 的是种子值，用前对照录制确认。
- 读源码前先做**版本对齐**：录包日期晚于相关文件的最新提交则用
  `git log --until=<录包日期>` / `git show <commit>:<file>`（只读）分析录包时期代码并
  标注 commit；旧录包永远不能验证新代码。
- 耗时表"数据获取"行的定义：分析开始时 mcap 已在本地磁盘则记 0；用户提供了传输
  等待时间则填用户值；否则写 unmeasured。**禁止编造**。

## 5 Routing packet（manager ↔ specialist 唯一契约）

```json
{"case_id": "...", "recording": "...", "hop": 2,
 "clock": "log_time",
 "temporal": {"anchor": "topic:field", "last_good": 0.0, "first_bad": 0.0,
              "symptom_time": 0.0, "window_secs": [0.0, 0.0], "missing": "..."},
 "current_output": {"topic": "...", "field": "..."},
 "inputs": [{"topic": "...", "field": "...", "max_age_ms": 100}],
 "specialist": "<pack>_<domain>_analysis",
 "question": "一个判别性证据问题", "stop_condition": "..."}
```
只传这个 JSON，不附散文。specialist 缺字段时返回 `{"reject": "<缺失字段名>"}`；
manager 补齐重发一次，再失败则该 hop 由 manager 自己完成。
specialist 返回单行 trace 表：
`hop | 窗口 | 消费者输出 | last-good/first-bad | 检查 | 结果 | 下一跳或边界 | duration_secs`
（duration_secs：接包后第一动作和返回前各取一次 `date +%s.%N`，差值填入，不许估算。）

## 6 结论与报告契约

07_conclusion.json 必填 `root_cause`：**单一具体机制的拍板句**。不确定性只允许出现在
`confidence` 与 `upgrade_evidence`（升级为确认还差什么证据）两个字段里，root_cause
本身不得含"可能/或/待定/尚未确认"。另含：temporal_packet、causal_chain、
gating_checks（计时门控案）、recommendations（方案/位置/理由/风险/验证）、
next_evidence、validation_boundary、timing_breakdown（首行"数据获取"，见 §4 定义）。

交付五要素（一次交齐）：①根因拍板句 ②完整因果链 ③每环节 topic:field+时间戳
④代码/配置 file:line ⑤建议方案表。事实四分开：observed fact / source-confirmed /
hypothesis / unknown。永不宣称车辆安全、生产就绪或"修复已验证"——修复验证需要
新二进制的新录包。
