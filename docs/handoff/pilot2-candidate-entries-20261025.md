# 试点 2（vibe-coding-cn）候选坑清单 —— 仅候选，不代写条目

> **性质**：KMAP-A2 交付物。以下是**候选**（candidate），供该线挑选/证伪后自写正式条目。
> **本件不落任何 `knowledge/vc/VC-*.md`**（内容真实性只有该线能证），也不代填 domain/复现固件。
> 依据：只读侦察该仓结构（`i18n/` `docs/` `libs/` `scripts/` `.githooks/` `.github/`；family `vc` 枚举
> `translation|structure|tooling`，domain `D1–D5` 含义待该线按 vocab 指定）。
> 每条格式对齐 KMAP 条目 schema v1（`docs/templates/kb-entry-schema-v1.md`）：症状 / trigger（含问法）/ fix_layer / 复现线索。

## 候选一览（8 条）

| # | 症状（拟） | trigger 问法（拟） | fix_layer | 常见根因 | 该线需核实 |
|---|---|---|---|---|---|
| C1 | 改了 `README.md` 正文，`i18n/` 下译文与原文不一致，读者看到两套说法 | 「我改了主文档，翻译没跟上，怎么防」 | `translation` | 缺「原文变更→译文待办」的联动机制（无同步门/无未译计数） | `i18n/` 是否已有同步脚本或 CI 检查 |
| C2 | 译文里的相对链接仍指向原语言目录，点击 404 | 「翻译版链接全 404」 | `structure` | 译文文件被移动到同构目录后，链接未做目标语言路径重写 | 现有译文的链接形态（相对/绝对/锚点） |
| C3 | 中英混排后半角标点与全角标点并存，`grep` 同一句搜不到 | 「检索不到明明存在的句子」 | `structure` | 标点不统一（`，` vs `,`／`：` vs `:`）导致字面检索失效 | 是否已有 lint 规则约束标点 |
| C4 | pre-push / shell 脚本在含中文或空格的路径下失败 | 「钩子在 Git Bash 里跑不起来」 | `tooling` | 未加引号变量、`$VAR` 后紧跟中文被误判变量名（bash 3.2） | 钩子脚本是否 `${VAR}` 花括号、路径是否加引号 |
| C5 | 提交后 diff 显示整文件变更，实际只改了几行 | 「diff 全是红绿，看不出改了什么」 | `tooling` | CRLF/LF 混入使整文件重写 | `.gitattributes` 是否钉 `* text=auto` / `*.md text` |
| C6 | 大文件（如 40 KB README）被整段替换，评审无法定位改动 | 「这么大的改动没法审」 | `structure` | 缺少分块/生成式维护约定（模板段 + 生成器） | README 是否有人工可维护的段落锚点 |
| C7 | GitHub Actions 上「本次改动文件」为空，检查被跳过但看起来是绿 | 「CI 说通过，其实没检查」 | `tooling` | `checkout` 深度不足 / base 计算取错（push 事件的 `before` 为全零） | 现有 workflow 的 fetch-depth 与 base 计算 |
| C8 | 同一术语在不同章节被译成两种说法 | 「同一个词前后翻译不一致」 | `translation` | 缺术语表（glossary）单一事实源 | 是否已有术语表文件；若有，CI 是否校验 |

## 使用建议（给该线）

1. **先证伪再落条**：每条候选都要在本仓找到**至少一处真实复现线索**（文件:行号或提交），否则不写条目——避免把「通用可能性」当成本仓的坑。
2. **domain 由该线指定**（`D1–D5` 含义只有该线掌握）；`fix_layer` 已按 `vc` 族三枚举预填，可改。
3. 落条用 `python scripts/kb_draft.py --kb knowledge/vc --symptom <症状> --domain <D?> [--layer <fix_layer>]`，
   机检不过不落盘；`trigger` 必须写「**复现条件；问法：<口头问法>**」两段式（schema v1 强制）。
4. 首批建议只落 **3–5 条**（覆盖 translation/structure/tooling 各一），够观察窗算 R/B/H 分母，不追求数量。

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-25 | 建件（A2）：8 条候选（translation×2 / structure×3 / tooling×3），全部标注「待该线核实」，未落任何 VC 条目 |
