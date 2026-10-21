---
title: "常见问题 | 迅投知识库"
source_url: "https://dict.thinktrader.net/nativeApi/question_function.html"
crawled_at: 2026-10-12T03:03:52+08:00
platform: QMT
category: 03-原生Python-XtQuant
depth: 4
tags: [平台/QMT, 状态/已抓取]
---

### [#](https://dict.thinktrader.net/nativeApi/question_function.html#导入xtquant库时提示-no-module-named-xtquant-ipythonapiclient) 导入xtquant库时提示 `NO module named 'xtquant.IPythonAPiClient'`

1、目前xtquant支持的python版本为 64位python3.6----3.11，请使用支持的python版本重试

### [#](https://dict.thinktrader.net/nativeApi/question_function.html#连接-xtquant-时失败-返回-1及解决方法) 连接 xtquant 时失败，返回-1及解决方法

1. 客户端是否以极简模式登录（登录qmt系统时需要勾选极简模式）

2. 检查路径是否正确

- `miniqmt`：路径指定到安装目录下`\userdata_mini`文件夹

- `投研端`：路径指定到安装目录下`\userdata`文件夹

3. 客户端安装在C盘的话，每次都需要用管理员权限运行策略，才能正常连接，否则有权限问题。

提示

不建议安装在C盘。

可以通过以下测试来验证是否有写入权限

```
file_path = r"d:\qmt\userdata_mini\example.txt"  # 设置文件路径和名称

# 使用open函数创建文件，并指定写入模式("w"表示写入模式)
with open(file_path, "w") as file:
    file.write("123")  # 向文件写入内容
```

如果出现`PermissionError`，则说明存在文件权限问题

4. 路径正确时换个`session`（任意整数即可）

提示

由于机制限制，同一个session的两次python进程 connect之间必须超过3秒钟

5. 如果miniqmt开启后, userdata_mini文件夹内没有`up_queue_xtquant`文件，说明没有用户没有对应函数下单的权限，需要联系券商开启

### [#](https://dict.thinktrader.net/nativeApi/question_function.html#执行xtdatacenter-init时提示监听58609端口失败) 执行xtdatacenter.init时提示监听58609端口失败

说明当前环境的58609端口被其他程序占用,通常是启动了两个xtdc服务导致的

**方法1.** 可用通过指定`xtdc.init(False)`后,使用`xtdc.listen(port)`指定自己需要的端口

```
from xtquant import xtdatacenter as xtdc 
xtdc.set_token("这里输入token")
xtdc.init(False)
port = 58601
xtdc.listen(port=port)
print(f"服务启动,开放端口：{port}")
```

**方法2.** 关闭所有py程序，或重启电脑，再执行xtdc.init

### [#](https://dict.thinktrader.net/nativeApi/question_function.html#下单后-查询委托的投资备注只有前半部分) 下单后，查询委托的投资备注只有前半部分

极简客户端的`order_remark`字段有长度限制，最大 24 个英文字符(一个中文占3个)， 超出的部分会丢弃。大qmt 没有长度限制。

### [#](https://dict.thinktrader.net/nativeApi/question_function.html#userdata-mini目录下-生成大量down-queue文件) userdata_mini目录下，生成大量down_queue文件

该文件是xttrade指定新的session产生的文件，可以参考下方示例来控制session产生的范围，避免该文件大量产生，**该文件可以删除**

[指定session id范围连接交易在新窗口打开](https://dict.thinktrader.net/nativeApi/code_examples.html?id=7zqjlm#%E6%8C%87%E5%AE%9Asession-id%E8%8C%83%E5%9B%B4%E8%BF%9E%E6%8E%A5%E4%BA%A4%E6%98%93)

上次更新:

邀请注册送VIP优惠券

分享下方的内容给好友、QQ群、微信群,好友注册您即可获得VIP优惠券

玩转qmt,上迅投qmt知识库

登录后获取

[完整实例](https://dict.thinktrader.net/nativeApi/code_examples.html)[xtquant版本下载](https://dict.thinktrader.net/nativeApi/download_xtquant.html)
