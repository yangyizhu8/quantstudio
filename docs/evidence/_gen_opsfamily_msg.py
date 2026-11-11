# -*- coding: utf-8 -*-
import io, os, uuid

MSG = """feat(kb): 起草 ops 族（运维探针与守护链域）——kb-info.yaml 登记 + 族 README（批六十六 A-4=甲，待⑥复核落定）

依据：2026-11-12 总调度批六十六 —— A-4＝甲（ops 族登记），我方起草 kb-info.yaml＋README，
⑥ 复核族 schema 后落定，E1–E4 逐条走 kb_draft.py 机检门。

## 立族动因（证据）
守护链复活能力实施（737e3d9）+ observe 观察期（15089cc）产出 E1–E4 四条经验，
现有三族均无适配面：
- qd = QMT 转换管线（wrapper/portability_rule/matrix/probe/ledger/engine）
- pd = PTrade 转换与部署
- db = 数据库引擎与数据完整性（适用面明确限定「写入路径/约束/巡检守卫/制品判据/版本升级」，不含进程运维）
且 db 族 README §1 早已预告：「vs ops 族（候选）＝运维探针（daemon 可观测/告警通道）；
数据库引擎≠进程运维，不重叠」——本笔即该候选转正。

## 改动
1. knowledge/kb-info.yaml
   - families 段新增 `id: ops`：dir=knowledge/ops、entry_glob=knowledge/ops/OPS-*.md、
     id_pattern=OPS-\\d{4}-\\d{4}、fix_layer_enum=[probe, guard, lifecycle, recovery, observability]、
     fix_layer_multi=true（典型 guard+recovery）、domain_vocab=D1..D5、
     required_fields 六项（同 qd/pd/db）、判层关键词 38 个（五层各 6~9 个）
   - updated: 2026-10-25 → 2026-11-12
   - changelog 追加 v3 条（批六十六 A-4=甲）
2. knowledge/ops/README.md（族 schema 正本，供⑥复核）
   - 立族动因 / 适用范围 / 与 db·qd·pd·iron-rules 四向边界（含判定口诀：
     改「库里的数据/约束」⇒ db；改「跑着的进程/任务」⇒ ops）
   - 六必填字段 + fix_layer 五枚举语义表 + 38 判层关键词
   - 条目模板（OPS-YYYY-NNNN.md）+ kb_draft 起草命令 + 落位与机检口径
   - 首批待落 E1–E4 映射表（R3b+F11 / 三段式闸+observe / 测点混用 / epoch ms cast）

## 验证
- YAML 可解析：✅（修正首次拼接漏闭合引号后复验通过）
- 族可被 kb_draft 定位：✅ `_family_for` 按 families[].dir 反查命中 ops，
  required_fields 六项、fix_layer_enum 五枚举、38 关键词均正确读出
- 族边界不与既有三族重叠：✅（见 README §1 表 + 口诀）
- ⚠️ entry_glob 命中 = 0（首批 OPS-*.md 待⑥复核后逐条落）——kb_hook 的 entry_glob
  用于「文件→族路由」而非「必须>0」硬判据，不阻塞；但已在 README §7 与上报中标明「待落定」

## 未做（如实）
E1–E4 条目**未落盘**——按裁定顺序须先经⑥复核族 schema，再逐条走 kb_draft 机检门。
本笔只交付「族定义 + schema 文档」，不预埋条目（避免 schema 若被改导致条目重做）。
"""

out = os.path.join(os.environ.get("TEMP", "."), "cm_opsfamily_" + uuid.uuid4().hex[:8] + ".txt")
io.open(out, "w", encoding="utf-8", newline="\n").write(MSG)
print(out)
