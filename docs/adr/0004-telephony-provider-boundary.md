# 电话能力通过 Provider 隔离，Bland 保留为非默认实现

现有发现流程直接依赖 Bland AI，但中国大陆场景必须改用 Android SIM 网关。项目保留 Bland 作为可选 Provider 以维持既有演示和测试能力，中国场景默认使用 `AndroidSimGatewayProvider`。`discovery.py` 不再直接依赖具体厂商，而通过统一电话 Provider 调用；Provider 只负责电话执行能力，ASR、TTS 和文本决策保持独立。

## Consequences

- Provider 接口必须先能表达真实电话能力差异，不能假设所有 Provider 都提供相同转写或音频能力。
- 第一阶段人工验证不经过 Provider；只有真实链路通过后才实现 Provider 抽象和迁移。
