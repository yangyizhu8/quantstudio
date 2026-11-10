# probes 族登记草案（候⑥一行协调后落 kb-info.yaml）

> 裁定来源：用户 2026-11-09 02:49「probes 族归属 = 新增 probes 族照准——与⑥一行协调后在 kb-info.yaml families 登记」
> 现状：`knowledge/probes/` 目录已存在但 **kb-info.yaml 未登记该族**（`kb_draft.py --kb knowledge/probes` 会报「知识库目录不存在」或校验失败）

## 一、拟登记 YAML 片段（families 段追加）

```yaml
  - id: probes
    dir: "knowledge/probes"
    name: "探针/自检脚本族"
    prefix: "PR"
    description: "探查器、自检脚本、机检 lint 工具的误报/假阴性类教训与判据"
```

## 二、登记后待落条目

| 项 | 内容 |
|---|---|
| 条目 id（拟） | `PR-2026-0001` |
| 现象 | 探查脚本报「产物无机读块/字段缺失」，生产侧解析正常 |
| 真因 | 探查正则标注白名单与生产解析正则不同源（`(?:json)?` 漏 `alias-map`） |
| 修复落点 | `docs/handoff/dispatch/graph-lint.py:335` `RE_ALIAS_FENCE` 复用为唯一实现 |
| 证据 | `docs/handoff/lessons-dev-line.md` L-20261109-01 |
| domain | 待确认（各族 domain_vocab 不同，登记时按 probes 族 schema 定） |

## 三、协调点（需⑥一行确认）

1. `prefix` 是否需要（既有族中 pd/db/qd 的前缀约定是否强制）
2. `domain_vocab`（probes 族是否有独立域枚举，或复用 D1..D5）
3. 是否与既有 `knowledge/playbooks`、`knowledge/contracts` 等未登记目录一并在同一次登记中处理

## 四、纪律

- **不擅改 `kb-info.yaml`**（配置变更，且⑥域可能并发编辑）⇒ 候协调结果再落
- 落库后立即走 `python scripts/kb_draft.py --kb knowledge/probes ...` 正式提交 L-20261109-01
