# 每个运行时目录只允许一个 FreeSWITCH 实例

FreeSWITCH 的 `show channels`、`show calls` 和频道 UUID 解析依赖运行时
`core.db`。两个 FreeSWITCH 进程不能同时使用同一个 `.runtime`：
后启动的进程会重建 `channels` 等表，使先启动的进程无法登记活动通道。

这曾导致一次真实呼叫已经拨出，但后端连续 10 秒看不到 FreeSWITCH
通道，最终只能通过手机侧挂断。项目决定在 `start.sh` 和 `run.sh` 中
加入进程门禁，并将当前实例 PID 保存到 `.run/freeswitch.pid`。

如果已有实例正在运行，渲染或启动脚本必须立即失败，不能覆盖
`.runtime`。停止操作统一使用 `scripts/stop.sh`。

## Consequences

- 重复启动 FreeSWITCH 不再可能静默破坏频道状态。
- 修改配置前必须先执行 `scripts/stop.sh`，再执行 `scripts/start.sh`。
- 真实呼叫前除 SIP 注册外，还必须确认只有一个 FreeSWITCH 实例。
