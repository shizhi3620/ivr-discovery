# 发现状态分为目标、发现窗口和发现运行三层

单个 Session 已无法同时表达授权目标、时段路由、累计预算、探索前沿和最终报告范围。项目决定使用三层语义：Target 保存授权号码、业务上下文和必需时段路由；DiscoveryWindow 保存某个时段的累计预算、状态和探索前沿；Run/Session 只表示一次实际执行尝试。最终报告绑定 Target，并只有在全部必需 DiscoveryWindow 完成后才能成为完整报告。

## Considered Options

- 继续扩展单一 Session：迁移较小，但会把目标、预算、时段和执行连接混成一个状态机。
- 使用电话号码作为隐式目标键：实现简单，但无法可靠承载不同授权范围或业务上下文。
- 引入 Target、DiscoveryWindow、Run 三层：模型更复杂，但能准确表达跨时段续跑和报告门禁。

## Consequences

- 现有 Session 语义收缩为一次发现运行，不再代表完整目标。
- 报告、预算、探索前沿和端点状态需要迁移到 Target 与 DiscoveryWindow。
- 单窗口运行只能生成草稿报告，跨窗口聚合由 Target 负责。
