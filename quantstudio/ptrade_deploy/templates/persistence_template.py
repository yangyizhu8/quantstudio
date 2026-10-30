# -*- coding: utf-8 -*-
"""持久化注入模板（对应设计文档 docs/ptrade-deploy-pipeline-design.md §6.1，依据 F1/F2）。

双层持久化：
- g.state 纯数据容器（PTrade 框架自动持久化 g 变量为兜底，F1）；
- 显式 pickle 落盘（{strategy_id}_state.pkl，F2：open()/pickle 读写研究根目录）。

PD-0015：研究根目录**不得写成字面量路径**——部分券商禁止代码中出现该路径（官方
001-page.md:2573），故由注入的 `_PD_NOTEBOOK = get_research_path()` 在运行时取得，
模板侧只做 `_PD_NOTEBOOK + 文件名` 拼接（PTrade 禁用 os 模块，不能用 os.path.join）。

模板为纯源码段，占位符 __STATE_FILE__ / __STATE_VERSION__ 由 transformer 做字面替换
（不用 str.format，避免与模板内字典字面量花括号冲突）；
PERSIST 标记注释由 transformer 的定制 unparse 发射器统一添加（§5.4）。
"""

#: 模块级注入：运行时解析研究根目录（PD-0015；官方 get_research_path 口径）
#: 兜底为空串（退化为相对当前目录），绝不因该函数不可用而中断策略。
PERSIST_NOTEBOOK_TEMPLATE = '''\
try:
    _PD_NOTEBOOK = get_research_path()
except Exception:
    _PD_NOTEBOOK = ''
'''

#: initialize 头部注入：try 恢复 / except 默认 + 版本校验（§6.1：防旧 pickle 炸新代码）
PERSIST_RESTORE_TEMPLATE = '''\
try:
    with open(_PD_NOTEBOOK + __STATE_FILE__, 'rb') as _pf:
        _saved_state = pickle.load(_pf)
    if (not isinstance(_saved_state, dict)) or (_saved_state.get('version') != __STATE_VERSION__):
        log.warning('PTRADE-DEPLOY 持久化状态版本不匹配，回退默认状态')
        _saved_state = None
except Exception:
    _saved_state = None
g.state = _saved_state if _saved_state is not None else {'version': __STATE_VERSION__}
'''

#: after_trading_end 尾部注入：g.state 落盘（save_points 触发，pickle.dump 协议 -1）
PERSIST_SAVE_TEMPLATE = '''\
try:
    with open(_PD_NOTEBOOK + __STATE_FILE__, 'wb') as _pf:
        pickle.dump(g.state, _pf, -1)
except Exception as _pe:
    log.warning('PTRADE-DEPLOY 状态落盘失败: %s' % _pe)
'''
