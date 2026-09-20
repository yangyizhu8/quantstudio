# 并回提交清单（MERGEBACK_MANIFEST）

> 产出：2026-09-21 收尾轮（总调度指令②，⑤总调度辖）· 副本 feat/rust-hybrid 终态锚 = 收尾 commit（见文末）
> 完备性基线：`git diff --stat 366596d..HEAD`（副本基线锚）逐文件对账；主仓 merge 带入文件（GUI/pipeline/tests/prepush-gate 等主仓自有提交）不在本清单——并回时对主仓为 no-op。
> 红线：并回执行（主仓 fetch/merge）属⑤总调度辖，本清单呈审通过 + 用户确认后另令执行。

## 一、引擎改动（2 件）

| 文件 | 改动 | 引入（里程碑/commit） | 门禁证据 |
|---|---|---|---|
| `quantstudio/backtest/providers/duckdb_data_access.py` | M1 单码回看式聚合：`query_bars_by_count_batch` 拆 wrapper（族键防跨族/深拷贝防变异）+ `_query_bars_by_count_batch_impl`（原实现重命名）；+64 行 | M1 / `e63dab7` | 两态逐位门 U1-U5 全 PASS（gate_m1 序列日志）；回归门 79 passed + 契约门 PASS |
| `quantstudio/backtest/backtest_engine.py` | M2-P 构价向量化：:2432-2435 热路径 `dict(zip)` + 实例级裸码→QMT 映射缓存（`_qmt_map_cache` 懒初始化 :402-404）；病态列缺失保留原 iterrows 绝对等价；+29/−4 行 | M2-P / `ebecd96` | 两态逐位门五 PASS（U2 2.50×）；回归门 102 passed + 契约门 PASS；ISS-001 已实施关闭 |

## 二、工具工件（1 件）

| 文件 | 内容 | 引入 | 行数口径 | 验证 |
|---|---|---|---|---|
| `scripts/double_run_engine.py` | M0 引擎级双跑对比器：全精度双通道（%.17g 主裁决 + CSV 旁证）+ QS_FILL_AUDIT 裸消息体通道 + 四谱负样本（1ulp/单字符/增删行）+ `--rust-kernel` 占位 fail-fast；U1-U5 用例矩阵内置 | M0 / `bb80115` | **299 lines（Get-Content 全行计数口径）/ ~264（非空行 wc 风格口径）——双口径并存注：行数申报统一附统计方法；文件仅 bb80115 一次触碰，M0 后零改动（git log --follow 佐证，席位已核）** | M0 自校验五报告全 PASS + 四谱负样本全 FAIL 检出；M1/M2 两轮作为逐位门裁判复用 |

## 三、文档证据（7 类 13 文件）

| 文件 | 内容 | 引入 |
|---|---|---|
| `docs/RUST_HYBRID_INITIATIVE.md` | 立项卷宗（含勘误 + **终止裁决归档** + §8 状态板终态） | 29b2dbb + 历次簿记 + 收尾 commit |
| `docs/RUST_HYBRID_DESIGN.md` | 方物件 v1.3（六节 + 三轮裁定/修订/定谳/终止链全档） | 72e8c6e → bb80115 → e63dab7 |
| `docs/evidence/phase0_hotspots.md` | Phase 0 底层证据（12 运行清单 + 两轮勘误[4.46%/943s 扫描总量]） | 72e8c6e → e63dab7 |
| `docs/evidence/double_run/`（6 文件） | U1-U5 报告样例 + U4 全精度镜像样例 | bb80115 |
| `docs/PROJECT_STATE.md` | 跨会话状态档案（终态=④确认待呈/⑤并回准备完毕） | 29b2dbb + 历次 + 收尾 commit |
| `docs/ISSUES.md` | Issue 台账（ISS-001 关闭 / ISS-003 R 终止转写 / ISS-004 定谳关闭 / ISS-005 挂档；随并回流向主仓台账） | 29b2dbb + 历次 + 收尾 commit |
| `.gitignore` | +2 行（agent_workspace/phase0/ 与 *.prof，补充指示③批准） | 72e8c6e |

## 四、待决策项（呈总调度并回审时裁，未裁不执行）

**gate_m1.py 转正建议**（审核席意见随清单呈裁）：`agent_workspace/phase0/gate_m1.py`（两态逐位门禁 runner，M1/M2 两轮实战验证的方法论工件；现 gitignore 隔离不入 git）——**建议转正入库**（主仓未来引擎改动的可复用门禁方法论：cp 两态切换 + 子进程新鲜度 + SHA256 席位交叉核对 + 失败即停语义；成本低价值高）；辅助件（runner_minute_clean.py / dump_profile.py / cprofile_segments.py / speedscope_agg.py）留本机存档（其中 runner_minute_clean.py 含 sys.path 主仓陷阱，不宜直接转正——若转正需先修口径）。

## 五、并回后主仓验证建议（属⑥同步门范畴，并回后执行）

1. **U1-U5 复跑门禁一次性确认**：主仓环境跑 `python scripts/double_run_engine.py --all`（A=B 同引擎自校验，五报告应全 PASS——确认对比器与引擎改动在主仓环境自洽）
2. 回归门复跑：M2 同款 pytest 10 文件 + 契约门（主仓 CI 兜底全量矩阵）
3. 同步门：本次并回**触及共享层**（quantstudio/backtest/ 两文件）→ **QuantStudio-trading 同步门适用**（fetch+merge + check-drift 九项 + ci-smoke 共享层回归，铁律 2026-09-09）
4. ISSUES 流向：本仓 ISS-002（候选）/ISS-003（SQL 预筛新题）随并回入主仓台账接管

## 六、副本分支提交链全景（并回 fetch 锚）

```
29b2dbb 交接三工件（前置 C）
72e8c6e Phase 0 四场景实测 + 方案子 v1.1（②审呈报件）
bb80115 M0 双跑对比器 + U1-U5 自校验全 PASS + v1.2（③实施）
02b073f merge origin/main（f2d588f 等 4 提交，engine 零触碰）
e63dab7 M1 段一批量化 + 逐位门五 PASS + v1.3（③实施）
a6361a0 merge origin/main（c3c4cc3 等 2 提交，engine 零触碰）
ebecd96 M2 段二路线 P 构价向量化 + 判定门 2.50×（③实施）
ddaf083 merge origin/main（c1d082e，AGENTS.md）
<收尾 commit> R 终止归档 + 本清单（副本分支最终态锚）
```

---

*本清单为并回审主呈件；执行另令（⑤总调度辖）。*
