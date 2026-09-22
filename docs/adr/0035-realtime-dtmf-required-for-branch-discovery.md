# 分支发现必须由实时 ASR 驱动 DTMF

正式 Discover 曾在根节点解析后使用固定 `CALL_DTMF_INITIAL_DELAY` 发送分支
按键。Apple 隐私提示约二十多秒后才要求按 `1`，固定 12 秒发送会被忽略；
系统随后把仍然停留在隐私节点的通话错误解析成叶子，甚至把窗口标记为
已验证。

项目决定：

- Android SIM Provider 新增 `realtime_dtmf` 能力。
- 分支节点在 `realtime_dtmf=False` 时必须在拨号前失败。
- 每个 DTMF 键只能由明确的 `dtmf_ready` 事件触发。
- 键注入后必须启动第二段只监听实时判断；人工边界和实时 ASR 错误立即挂断。
- 分支节点只有在实时导航成功后才写入 `realtime_verified=1`。
- 窗口内任何已完成分支节点只要没有实时验证，就不能通过完整性门禁。

静态录音和通话后文件 ASR 仍用于审计与报告，但不能替代通话内决策。
