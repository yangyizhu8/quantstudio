# A+B 线收口序列与推送时点纪律（2026-09-06 守望审核后登记）

> 状态：A 线验收完成、B 线结项判据达成、C 线备案——全部复核通过，无待执行动作，守望 §21 收口信号。
> 本文件为收口序列的落盘登记（防会话上下文压缩丢失），推送组装时照此执行。

## 基线更新（审核方通报，2026-09-06 实测）
- HEAD：c9a20ab → **8c58bd0**（他方 3 提交：#18 质量编排器 + 两份移交 docs，双远端已同步、与 A/B 在途文件零交集）；
- **推送组装时基线按 8c58bd0 计，三方 HEAD 核对目标 = 推送后新 HEAD**。

## 收口序列（审核确认版）
1. §21 探针所属会话收口（他方 source_import 在途 +42 落地/回滚）；
2. **纪律①：§21 落地后先刷新钉死回归基线再走门③**——现「在途漂移层 5 项失败」系 §21 未提交改动所致，其落地后这 5 项或转绿或转为 §21 自报变更，钉死清单须重钉后再比（防对陈旧基线误判）；
3. 矩阵哈希门 `check_fund_matrix.py --check --reverify` 转绿；
4. **用户确认推送**（六步流水线第 5 步闸门）；
5. 双仓推送：
   - 四方叠加分层提交信息（A2 观测网 +168 / B2 归一消音 / B2' 跨日窗口 / D4 缓存键 ±4）；
   - F-DUCKDB-LOCK / F-LOCAL-MIN 登记更名（保留编号改描述）；
   - README / docs/strategy_toolbox.md / docs/prompt_engineering.md 同步；
   - **纪律②：AGENTS.md 精确 add 前先 diff 复核**——该文件现含多会话改动（stash-store 细则 + 他方修订），随车推送前确认全部改动均为已裁决内容；
   - AGENTS.md stash-store 细则随车；
6. 三方 HEAD 核对（本地 / quantstudio-plus / quantstudio）；
7. 三线收官归档（C 线等真实封板窗口，不在本序列）。

## 随收口一并落地（已确认项）
- 数据形态差异（09:30 开盘竞价根本地有/平台无）追加进双端对齐报告 known-difference 清单（影响一切消费分钟 closes[-1]/[-2] 策略的双端判定，不限于打板）；
- 快照 v2 已被注入测试污染（09:30 bar=99999.0）——后续复核须用新拷贝或区分注入前后状态（门③锚点数字 6.0 取自注入前干净态，报告已分离）；
- F-DUCKDB-LOCK 设计补注（interrupt 对 IO/锁等待无效，fail-loud 上界 2×budget+20s）已在 docs/duckdb-lock-timeout-design.md 落位；
- O4 测试债（7 个 test_source_import 断言）另行排期不混装；O5 dsh-synapse 待 web 重启激活；O6 记忆 63837c2e 待用户审批。

## 证据文档索引（推送包组装清单）
- A 线：docs/evidence/duckdb-lock-a0-verdict-20260905.md、duckdb-lock-regression-baseline-20260905.md、duckdb-lock-a2-implementation-20260905.md（A1 设计 docs/duckdb-lock-timeout-design.md）
- B 线：docs/evidence/f-local-min-b1-verdict-20260905.md（含第二层补录）、f-local-min-b2-implementation-20260905.md、f-local-min-b2p-b3-implementation-20260906.md（B2' 设计 docs/f-local-min-b2p-design.md）
- C 线：docs/evidence/s615-replay-manual-20260905.md（备案）
- 快照：agent_workspace/b3_snapshot/b3_snapshot_v2.db（2449MB，逐码核对 match；**已被注入污染，复核用新拷贝**）
