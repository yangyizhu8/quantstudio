# PD-2026-0020 期1 · AST／受控 stub 行为验证

对 `_qs_budget_clamp` **函数本体**直调（不是文本计数）：

- 隔离关闭（C1/C2）：`applied=False`，amount 保持 1000（**不折算**）；
- 隔离开启（C3/C4）：注入 `iso_avail=lambda:5000` 后 `applied=True`，avail=5000 / px=10 / lot=100 ⇒ can_buy=500 ⇒ `amount=500`、`orig=1000`；
- **卖出（amount<0）在全部配置下均不钳**（applied=False，amount 保持 -500）；
- 每次调用前打印**桩依赖直读**（`_qs_px_exec`=10.0、`_QS_SPLIT_LOT`=100、`iso_avail is None`），自检不过则结论作废（防「沙箱缺依赖 ⇒ 静默返回值」误读）。

判定：PASS
