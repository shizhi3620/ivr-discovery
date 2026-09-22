# 同一发现窗口内重复执行复用未完成的 Run

如果一次发现运行因 WebSocket 断开、服务重启或用户主动停止而留下
`pending`、`calling` 或 `parsing` 节点，下一次 Discover 不能重新创建根节点并
从零拨号。这样会重复经过强制前缀、浪费预算，并让前端树与真实进度分离。

项目决定：同一 Target 和 DiscoveryWindow 内存在未完成工作时，新的连接复用
最近一个未完成 Run：

- 恢复已有节点和边，不删除完成的子树
- 将中断的 `calling`/`parsing` 节点恢复为 `pending`
- 只把探索前沿重新放入队列
- 继续使用该窗口和目标的累计预算

只有窗口内不存在未完成节点时，才创建一个新的 Run。显式 Re-discover 仍只
重建所选子树，不重置整个窗口预算。

## Consequences

- WebSocket 重连和页面刷新不会清空真实进度。
- 同一 Run 可以跨多个连接继续执行，但预算和节点仍属于同一
  Target/DiscoveryWindow。
- 已完成但存在失败分支的窗口需要显式 Re-discover 或人工处理，不会自动
  重拨整个树。
