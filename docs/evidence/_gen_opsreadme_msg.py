# -*- coding: utf-8 -*-
import io, os, uuid

MSG = """fix(kb): ops 族 README 自纠——模板改为 front-matter 正本格式 + 按机检实测升 3 字段为强制（附三态取证）

发现方式：⑫ 已落 OPS-2026-0001（04:06，git 已跟踪），回读该条目发现用 **YAML front-matter**，
与我 README §3 初稿设计的「body 六要素 + ## 详情」不一致 ⇒ 立即核对全族正本格式。

## 全族正本实测（非读码推断）
- db 43/43、qd 22/22、pd 22/22、ops 1/1 —— **全部**以 `---` front-matter 开头
- kb_hook.py 经 kb_preflight.parse_entry 从 front-matter 解析（单一事实源）
⇒ 我初稿模板**写错**，⑫ 的条目格式才是对的。

## 改动（knowledge/ops/README.md）
1. §3 条目模板：改为 YAML front-matter 形式（含 id/symptom/root_cause/fix_target/fix_layer/
   domain/trigger/recurrence/evidence/fixture/feedback{contract_row,gate_rule,template_patch}/
   status/closed_round），正文 ## 详情 降为可选；明确「机检只读 front-matter」
2. §3.1 字段说明：把 `recurrence`、`feedback.*`、`status` 三项从「建议」升为「**机检强制**」——
   依据是负向对照实测（见下），非文档推断
3. 追加「机检实测取证」小节

## 三态验证（kb_hook --strict）
| 用例 | 结果 |
|---|---|
| 正面：OPS-2026-0001（⑫ 落，族：ops） | 不合规 **0** 件，exit=0 |
| 对照：DB-2026-0002（族：db） | 不合规 **0** 件，exit=0（证明工具本身工作，排除假 PASS） |
| 负向：注入缺字段的 OPS-2026-9999（临时，已删） | 不合规 **1** 件，精确报 7 项：`root_cause 为空; evidence 为空; fixture 为空; trigger 为空; recurrence 非整数或为负; feedback 至少一项非空; status 非法`，**exit=1**（门禁生效） |

⇒ 机检对 `recurrence`（整数≥0）、`feedback`（三项至少一项非空）、`status`（∈open|closed）为**强制**，
与 README 初稿「建议」不符，已据实修正。族 schema 与 ⑫ 条目**兼容**（正面 0 不合规）。

## 影响
- `entry_glob` 命中由 **0 → 1**（OPS-2026-0001 在位）
- E1–E4 条目后续落盘须同时满足上述 3 项强制字段
"""

out = os.path.join(os.environ.get("TEMP", "."), "cm_opsreadme_" + uuid.uuid4().hex[:8] + ".txt")
io.open(out, "w", encoding="utf-8", newline="\n").write(MSG)
print(out)
