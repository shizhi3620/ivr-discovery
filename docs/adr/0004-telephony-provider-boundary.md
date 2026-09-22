# 电话能力通过 Provider 隔离，Bland 保留为非默认实现

现有发现流程直接依赖 Bland AI，但中国大陆场景必须改用 Android SIM 网关。项目保留 Bland 作为可选 Provider 以维持既有演示和测试能力，中国场景默认使用 `AndroidSimGatewayProvider`。`discovery.py` 不再直接依赖具体厂商，而通过统一电话 Provider 调用；Provider 只负责电话执行能力，ASR、TTS 和文本决策保持独立。

## Consequences

- Provider 接口必须先能表达真实电话能力差异，不能假设所有 Provider 都提供相同转写或音频能力。
- 第一阶段人工验证不经过 Provider；只有真实链路通过后才实现 Provider 抽象和迁移。

## 实现记录（2026-09-22）

真实链路（外呼 + 音频桥接 + DTMF 注入）通过后完成迁移：

- `backend/providers/base.py` 定义 `TelephonyProvider` 协议、`CallResult` 和 `ProviderCapabilities`。能力差异（是否有自有 ASR、是否支持语音、是否支持 DTMF）显式声明，而不是默认所有 Provider 等价。
- `backend/providers/bland_provider.py` 把原有 `bland_client` 包装成 Provider，能力为 `transcript/speech/dtmf = true`。
- `backend/providers/android_sim_provider.py` 通过 FreeSWITCH ESL 直接外呼 Android 网关，用 `X-GSM-Destination` 头传被叫号；解析真实 channel UUID 后，用 `uuid_record` 开始双声道 WAV 录音并转发 DTMF。它把 ASR/TTS 委托给独立 Audio Provider；腾讯云凭证可用时声明 `transcript/speech=True`，不可用时拒绝拨号。
- `backend/providers/esl.py` 是最小 ESL 客户端，只实现 authenticate、`api`、`bgapi`，不引入重量级依赖。
- `discovery.py` 和 `main.py` 不再直接 import `bland_client`；`get_provider()` 按 `TELEPHONY_PROVIDER` 选择，默认 `android_sim`。
- 由于发现流程依赖转写，`explore_node` 遇到无 ASR 能力的 Provider 时仍直接标记失败并**不发起真实呼叫**，避免在音频凭证未配置时白白消耗真实电话。
- 腾讯云音频边界、录音格式和接口事实见 `docs/adr/0007` 与 `docs/research/tencent-cloud-8k-asr-tts.md`。
