# -*- coding: utf-8 -*-
"""生成观察期报告的 commit message"""
import io
import os
import uuid

MSG = """docs(evidence): 复活能力 observe 观察期报告——201 轮零误判，all-pass 29 次全部对应真实死亡期，申请切 active

窗口：实际首行 2026-11-10 10:48:33 → 2026-11-11 20:28:34（跨 33.7h；批准 T0=11-10 20:30，覆盖超出 9.7h）
样本：总 201 行（T0 之后 146 行），≈5.96 轮/h 与 Heartbeat PT10M 吻合

关键判据（全部通过）：
- REVIVE_CALLED = 0 / REVIVED = 0（observe 未真拉，符合设计）
- REVIVE_FAILED / REVIVE_DENIED = 0
- all-pass 29 次**零误判**——交叉核对：
  复活日志 all-pass 首次 2026-11-11 01:08:34（consecutive=3 首达阈值）末次 05:48:33，consecutive 3→31
  exitcode.log: 11-10 22:35:57 START pid=32000 → 11-11 05:56:18 EXIT（UNKNOWN(lost) 对账补记）→ START pid=36372
  daemon.log 05:56:23 清理陈旧 status + 发布 pid=36372
  ⇒ 29 轮完整落在 01:08→05:56 真实死亡窗内（≈4.7h），判定正确
- skip-occupancy 2 次 = R3b 型反向正确拦截（⑳ 21:17:45 声明占用窗内拒绝拉起）→ 加分样本
- gate 分布：skip-threshold 170 / skip-occupancy 2 / all-pass 29 / 其余 0

价值实证：若为 active，01:08:34 首达阈值即会拉起，停摆从 ≈4.7h 压缩到 ≤60s（F6 自证窗）。

附：同批哨兵六步读数（daemon ALIVE pid=36372；index-delete 连续 13 轮 0；F4 PASS 双判据；EOL exit 1 实态）。
如实申报：A9b committed+absent=1（昨日 0，今日新增 1）**明细未取到**（SQL 引用不存在列 asset），仅登记计数，下轮补查。

切 active 申请（呈批，**本会话不自行切换**）：推荐改 QS_DaemonHeartbeat 任务参数追加 -ReviveMode active（不动脚本默认值，回退只需改参数）；前置为节点1 完成 F8 双触发注册；回退 = 加 -NoRevive 或改回 observe。
"""

out = os.path.join(os.environ.get("TEMP", "."), "cm_observe_" + uuid.uuid4().hex[:8] + ".txt")
io.open(out, "w", encoding="utf-8", newline="\n").write(MSG)
print(out)
