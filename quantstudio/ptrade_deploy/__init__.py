# -*- coding: utf-8 -*-
"""qs-deploy 转换内核（PTrade 回测代码 → 模拟盘/实盘部署代码）。

对应设计文档：docs/ptrade-deploy-pipeline-design.md §5/§6（M3 内核段）。
七段流水线：P0 预检 → P1 画像 → P2 配置校验 → P3 AST 变换 → P4 等价验证
→ P5 平台门禁 → P6 交付；统一入口 run_deploy()（单内核 + 双薄壳，§4：
CLI 壳/PyQt 壳均调用本入口，对等判据为产物 SHA256 逐位一致）。
"""
from .runner import DeployReport, StageResult, run_deploy

__all__ = ["run_deploy", "DeployReport", "StageResult"]
