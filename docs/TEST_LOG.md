# 测试记录

## 2026-08-21 · 9 场景金标准数据集评测（首次全类型覆盖）

- **被测版本**：v3.6.3（工具链脚本层）
- **数据集**：`~/Desktop/apa_manager_training_20260821`（独立生成的合成数据，9 个 mcap + 金标准答案 expected_manager/*.json，基于 luna6 源码接口）
- **评测脚本**：scratchpad `eval_training.py`（定位结果与金标准比对，容差 ±0.15s）

### 结果：定位层 9/9 PASS

| 案例 | 场景 | first_bad 实测/期望 | 判定 |
|---|---|---|---|
| apa-001 | 正常泊入 | all_good（无误报） | PASS |
| apa-002 | 障碍物进入路径停车 | 7.00 / 7.0（last_good 6.0/6.0） | PASS |
| apa-003 | 障碍物停更、决策复用过期帧 | 6.00 / 6.0 | PASS |
| apa-004 | 车位失效 trajectory_num=0 | 4.00 / 4.0 | PASS |
| apa-005 | 控制跟踪误差超 0.3m | 7.00 / 7.0 | PASS |
| apa-006 | EPB 干预 exit_reason=21 | 5.00 / 5.0 | PASS |
| apa-007 | 关键障碍输入缺失 | 4.00 / 4.0 | PASS |
| apa-008 | 障碍 valid 抖动触发重规划 | 5.00 / 5.0 | PASS |
| apa-009 | 开机即坏（相机故障） | 0.00 / 0.0（last_good=None 正确） | PASS |

### 深层能力抽验（README 点名项）

- **stale 识别（apa-003）**：frame_correlation max-age-ms=100 → fresh=6/stale=2，2 次 Close 异常评估**全部**落在 stale 帧、fresh 帧 0 次 → 正确指认"过期输入"而非"上游正常" ✅
- **缺失信号（apa-007）**：locator → status=no_classifiable_samples、missing=4/4 → 保留可观测性缺口，不误报正常 ✅
- **validity 闪烁（apa-008）**：changed 翻转计数=2，与数据真实序列（True×5→False×2→True×2）一致 ✅

### 结论与遗留

- 工具链首次在全故障类型数据集上全绿；此前验证面仅 2 个自建场景
- 本次仅验**脚本定位层**；agent 编排层（模型是否按序执行）仍需真实 Codex 环境验证
- ✅ 已完成（2026-08-21）：数据集收编 cases/training/，评测并入 run_tests 第三重门禁（golden.9scenarios）；registry topic_aliases 已对齐数据集中的 luna6 真实接口名

## 历史测试基线（摘要）

- 66 项自有回归（tests/run_tests.py）：脚本行为 + 渲染层 + CASE-0001 抖动场景
- 40 项独立 AI 对抗测试（tests/adversarial_tests.py）：2026-08-20 由独立 agent 生成，曾抓出 6 类真实 bug（末帧丢失/畸形 JSON 崩溃/changed 捏造与吞跃迁/max-age 浮点边界），修复后全绿
