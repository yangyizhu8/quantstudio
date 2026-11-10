# -*- coding: utf-8 -*-
"""生成守护复活能力实施的 commit message（避免 PS here-string 引号/美元符转义问题）"""
import io
import os
import uuid

MSG = """feat(guard): 守护链复活能力③实施——脚本段 F1-F11 + observe 默认（负向 6/6 PASS·F9 零漂移）

方案：docs/daemon-guard-revival-design-20261110.md v1.1（②审通过）
分工 Q3：本笔 = ④ 脚本段 + 负向对照；F8 任务定义（QS_DaemonForever 双触发）归节点1

改动：
- scripts/check_daemon_alive.ps1 287→574 行：新增复活段（F1 计数 / F2 阈值 / F3 占用 /
  F4 冷却 / F11 旁证四探 / F5 唯一路径拉起 / F6 自证 / F7 归零 / F10 留痕）+ 观察模式；
  新参数 -ReviveMode（默认 observe）/ -ReviveThreshold(3) / -ReviveCooldownMinutes(30) /
  -NoRevive，以及测试专用 -SimulateVerdict（强制 observe，不可能触发真实拉起）
- .gitignore：新增 data/daemon_revival_state.json（运行态，与 daemon_occupancy.json 同族）
- docs/evidence/revive_negative_tests.ps1（6 组对照）
- docs/evidence/revive_probe_tests.ps1（F11 探②③④单测）
- docs/evidence/daemon-guard-revival-impl-20261110.md（实施证据）

验收读数：
- F9 既有判据零漂移（三态 A/B，两版均显式传同一 -RepoRoot 消除路径解析假阳性）：
  FAIL rc 3=3 / WARN rc 2=2 / OK rc 0=0，verdict 标签全一致
- 负向对照 6/6 PASS：①skip-normal-exit ②skip-threshold(1/3) ③skip-occupancy
  ④skip-cooldown ⑤skip-suspected_activity ⑥all-pass + REVIVE_ARMED
  关键：**拉起调用计数 = 0**（action 全为 NONE 或 REVIVE_ARMED）
- F11 四探各自独立命中：探① WAL活跃（组⑤）/ 探② 主库被独占 / 探③ daemon.log 自停痕迹 /
  探④ .daemon.lock 活跃 → 3/3 单测 PASS + 探① 由组⑤覆盖

实施中暴露并修正三处缺陷（详见证据 §五）：
1. edit 写入去 UTF-8 BOM ⇒ PS 5.1 按 GBK 读中文 ⇒ 37 处语法错误；
   已补 BOM，并固化为本仓操作纪律：.ps1 每次 edit 后必补 BOM 再验语法
2. isSimulated 定义在文件后部而复活段先行引用（此时为 null）⇒ 定义提前至复活段之前
3. Write-ReviveLog 嵌在「四探全过」分支内 ⇒ 5 类 SKIP 决策无痕（违反 F10）；
   6 组负向对照首轮全部 (no-log) 暴露此缺陷 ⇒ 移至评估末尾统一留痕

生效态：默认 observe（只记 REVIVE_ARMED，不实际拉起）；
切 active 需 ①5 组负向全过（已达成）②observe 平稳 >=24h 零误判（待观察）。
未做（如实申报）：正向 REVIVED 验收与真实窗口场景验收——需 active + 真停生产 daemon
（触铁律 19，须窗口授权），本笔不冒充完成。
"""

out = os.path.join(os.environ.get("TEMP", "."), "cm_revive_impl_" + uuid.uuid4().hex[:8] + ".txt")
io.open(out, "w", encoding="utf-8", newline="\n").write(MSG)
print(out)
