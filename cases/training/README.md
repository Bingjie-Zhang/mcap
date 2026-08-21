# APA manager synthetic training data

这是一组依据 `luna6` 源码接口生成的**合成**测试数据，不是真实车辆录制。目标是训练/回归 `apa-manager` 的“先定位异常，再按 producer-consumer 边界向后追踪”能力。

## 内容

- `recordings/*.mcap`：JSON 编码的 MCAP，可直接交给 `mcap_inventory.py`、`mcap_event_locator.py`、`mcap_slice.py`。
- `cases.jsonl`：每行一个 case packet、录制路径、标签和期望路由，适合作为监督/回归输入。
- `routing_packets.jsonl`：按 `apa_manager.toml` routing packet 契约生成的完整首跳 JSON，适合作为路由输出监督集。
- `cases/*.json`：单 case 的可读副本。
- `expected_manager/*.json`：期望的时序包、首跳 specialist、边界和判别问题。
- `manifests/*.json`：由 `mcap_case_manifest.py` 生成的逐 case provenance（录制 sha256、时间范围、工具版本、源码 commit）。
- `dataset_manifest.json`：数据集版本、源码 commit、引用的接口/配置文件和样本清单。
- `generate_dataset.py`：可重复生成脚本。

## 覆盖场景

1. 正常泊入完成（all-good）
2. 真实障碍物进入路径，决策输出 `Close` / 小于 0.15 m
3. 障碍物停止更新，决策复用过期帧（freshness / timing）
4. 车位失效导致 `trajectory_num=0`
5. 轨迹仍有效但控制跟踪误差超过 0.3 m
6. EPB 干预导致 `exit_reason=21`
7. 关键障碍物输入缺失，保留 observability gap
8. 障碍物 `valid` 抖动并触发重规划
9. 录制一开始就处于 Disable / 相机故障（`bad_from_start`）

枚举值和 topic 名称来自项目源码；为便于 agent 训练，payload 同时保留了真实 protobuf/ROS 字段（例如 `apa_status.parking_working_status`、`apa_traj`、`control_err.lat_err`）和少量 registry 中的扁平化诊断字段。所有时间单位为秒，距离为米，速度为 m/s，角度为弧度。

## 快速校验

```bash
python3 generate_dataset.py
python3 ../../.codex/skills/mcap-analysis/scripts/mcap_inventory.py recordings/apa-002-obstacle-close-stop.mcap --top 20 --focus-regex 'parking_pnc|obstacle'
python3 ../../.codex/skills/mcap-analysis/scripts/mcap_event_locator.py recordings/apa-002-obstacle-close-stop.mcap \
  --field '/functions/parking_pnc/apa_decision:data.stop_distance' \
  --condition lt --threshold 0.15 --clock log_time
```

期望结果是：`apa-002` 的首个异常输出在 7.0 s 左右，下一跳为障碍物输入；`apa-003` 应被识别为过期输入，而不是“上游正常”；`apa-007` 应保留“输入未录制/不可观测”的未知项。

## 扩展场景 10-15（generate_extension.py 生成）

10. `apa-010-timer-gate-jitter`：收尾计时门控抖动——`stop_distance` 在 0.15 阈值附近 5 个来回进出（`timer_gate_active` 共 11 次翻转），计时反复清零，`work_state=8` 迟至 10.0s 才切换；expected 含 `flap_check`（min_flips=10）。
11. `apa-011-cascade-perception-replan-abort`：级联——障碍物 `valid=false` 间歇异常（4/5/7/8/9s）→ `replan_fail_count` 累计到 5、`trajectory_num` 反复归零 → 10.0s 状态 ABORT（`exit_reason=118`）；expected 含三层 `trace_path`。
12. `apa-012-topic-rate-drop`：`obstacle_pk` 6.0s 起从 10Hz 降到 2Hz（不断流），10Hz 决策消费旧帧比例上升（每 0.5s 内 3/5 帧龄 >150ms，共 36 次 stale）；anchor 为 `obstacle_input_age_ms gt 150`（first_bad 6.2s），expected 含 `stale_check`（min_stale=30）。
13. `apa-013-gradual-drift`：`tracking_error` 自 2.0s 起以 0.013/s 从 0.05 线性漂移，20s 后于 22.0s 首次越过 0.3（无突变），24.0s `exit_reason=106`。
14. `apa-014-user-cancel-no-fault`：负样本——7.0s 用户主动取消（PARKING→USER_CANCELLED，`exit_reason` 保持 0），常见故障谓词（stop_distance lt 0.15 / exit_reason ne 0 / trajectory_num eq 0）全部 all_good；expected `anchor: null` + `note: user_cancel_no_fault`。
15. `apa-015-clock-skew`：`apa_decision` 的 `data.header.timestamp` 相对 log_time 固定 +0.8s；真实异常为 `stop_distance lt 0.15`，log_time 下 first_bad=7.0s、header_time 下会误判为 7.8s；expected 含 `clock_note`。
