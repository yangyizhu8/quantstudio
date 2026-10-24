## 【铁律】主仓双端推送后同步 QuantStudio-trading 回测部分（2026-09-09 用户确立）

**背景**：主仓（QuantStudio）推送 GitHub 双仓库后，QuantStudio-trading 副本的
回测部分（共享层）必须同步更新——否则副本与主仓分叉，实盘线的回测能力退化。

1. **触发时机**：主仓每次 `git push origin` 完成且三方一致性核验通过后，
   **同一工作周期内**（推送后至下一主仓推送前）必须在 QuantStudio-trading
   执行同步门：
   - `cd QuantStudio-trading && git fetch origin && git merge origin/main`
   - check-drift 九项 PASS + ci-smoke 共享层回归段 PASS
   - 同步结果登记台账 + 日历（线调度回执）
2. **同步门语义**：同步门是验证步骤（确认共享层零冲突零回归），
   不是"是否同步"的决策点——**必须同步**，同步门仅在检测到冲突/回归时阻断并上报。
3. **前置条件**：副本工作树须干净（无他线在途改动叠加时须协调后清理）。
4. **豁免**：仅当本次主仓推送**未触及共享层文件**（quantstudio/、config/、
   skills/、scripts/、tests/、main_gui.py 全部无改动）时，可豁免同步门
   ——但须在台账登记豁免依据（纯 docs/推送等场景）。
5. 本条跨会话、跨项目、跨智能体共同遵守，长期有效。

> 用户原话要求（2026-09-09）：以后每次双端推送完成需要同步更新
> QuantStudio-trading 项目的回测部分，作为铁律写入跨会话、项目、智能体的记忆。

