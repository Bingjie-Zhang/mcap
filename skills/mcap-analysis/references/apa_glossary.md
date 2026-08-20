# APA symptom glossary (seed — extend per confirmed case)

| 用户语言 | 候选 topic.field | 备注 |
|---|---|---|
| 镜像折叠距离 / mirror fold | decision.stop_distance 或 data.mirror_fold_dist | 阈值见 registry predicate_hint |
| 碰撞检测 / 误刹 / 不停车 | decision.collision_check: data.collision_result | Open/Close 枚举 |
| 找不到车位 / 车位丢失 | planning.trajectory: data.target_slot | |
| 轨迹为空 / 不规划 | planning.trajectory: data.trajectory_num == 0 | |
| 画龙 / 跟踪差 | control.command: data.tracking_error | |
| 障碍物闪烁 / 误检 | perception.obstacle_list: data.valid / object_id 连续性 | |
