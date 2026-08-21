# mcap 根因分析包 · 协作者验证指南

这个包是一套只读的 MCAP 根因分析 Agent（Codex 环境），核心思路：先定位异常时间，再沿 producer-consumer 逐跳回溯到故障边界。开发侧的自动化测试已经全过（38 项），但**分析准确率必须用真实案例校准**，这一步只能由手里有 mcap 和业务背景的你来做。

## 环境要求

| 项 | 要求 |
|---|---|
| Python | 3.9+，装 `mcap` 包（`pip install mcap`） |
| mcap CLI | 可选。录制里的 topic 若不是 json 编码则必须装（[github.com/foxglove/mcap](https://github.com/foxglove/mcap/releases)） |
| Codex | 任意支持自定义 agent 的版本 |

## 第一步：装包 + 离线自测（约 5 分钟）

```bash
tar -xzf mcap-analysis-bundle-v3.3.3-20260820.tar.gz
cd mcap-analysis-bundle
python3 tests/run_tests.py        # 期望输出：all 38 checks passed
./install.sh --pack apa           # 装完提示重启 Codex
```

自测不过就不用往下走了，把报错截图发回来。

## 第二步：真实案例验证（核心）

挑一个**已经人工定位过根因**的历史案例（知道正确答案才能打分），对入口 agent `apa_problem_analysis` 发起分析：

> 分析 /path/to/xxx.mcap，症状：泊车过程中在镜像折叠阶段异常停车，Jira：ASTRA62DEV-xxxxx

### 验收点逐项打勾

| # | 检查项 | 通过标准 |
|---|---|---|
| 1 | 事件定位 | last_good / first_bad 时间与你人工在 Hviz 里看到的异常时间一致（±1 帧） |
| 2 | 回溯路径 | 逐跳链路和真实数据流向一致，没有跳到无关模块 |
| 3 | 根因结论 | 与人工结论一致，或边界定位正确但停在"需要补充证据" |
| 4 | 只读纪律 | 全程无任何文件修改、无 git 操作 |
| 5 | 报告格式 | 含 temporal packet、每跳 trace packet（带 duration_secs）、结尾耗时分解表 |
| 6 | 不越权 | 报告没有宣称"修复已验证"或"车辆安全" |

分析明显走偏时，把 agent 的完整输出存下来，不用现场纠正它。

## 第三步：回传材料

验证完把这些发回来，缺哪项标注原因即可：

1. **每个案例的最终报告**（含耗时分解表，这是速率优化的输入）
2. **registry 校准表**：`skills/mcap-analysis/references/apa_topic_registry.yaml` 里 5 个接口是按领域知识预填的（都标着 `confirmed: false`）。对照真实录制，把正确的 topic 名、字段路径、时钟字段、合理的 max_age_ms 批注出来，不用改文件，文字描述就行
3. **症状词典勘误**：`apa_glossary.md` 里的用户语言到字段映射，错的划掉、缺的补上
4. **打分**：上面 6 个验收点每案例一行，通过/不通过 + 一句原因

## 正确用法（重要）

- **必须在 `~/mcap-reports` 目录下启动 Codex**（`mkdir -p ~/mcap-reports && cd ~/mcap-reports && codex`）——否则沙箱拦截报告写入，只有纯文字输出、没有表格报告和 HTML
- **必须调入口 agent `apa_problem_analysis`**，不要直接调 `apa_manager`——直接调编排器会绕过报告契约（就会出现"证据没有 topic 来源"这类问题）
- protobuf 编码的录包必须装 `mcap` CLI（脚本靠它解码）。v3.3.3 起字段名 snake_case/camelCase 自动兼容；若仍取不到，把 `mcap cat` 输出的一条原始消息发回来

## 常见问题

- **`No module named 'mcap'`**：`pip install mcap`，注意装到 Codex 实际调用的那个 Python
- **`topics are not json-encoded and the mcap CLI is not installed`**：装 mcap CLI，或确认 topic 编码
- **agent 报"routing packet 缺字段"**：这是设计行为（拒绝不完整的上下文），把它报的缺失字段发回来
- 脚本可以脱离 agent 单独跑，排查时直接调：
  ```bash
  python3 skills/mcap-analysis/scripts/mcap_event_locator.py xxx.mcap \
    --field "/topic:data.field" --condition lt --threshold 0.15 --debounce-count 3
  ```

## 联系

问题找张冰洁（AI 效能部），随包问题请附：VERSION 文件内容、报错原文、脚本的 JSON 输出。
