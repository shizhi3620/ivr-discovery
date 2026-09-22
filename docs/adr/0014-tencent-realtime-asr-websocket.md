# 实时链路使用腾讯实时语音识别 WebSocket

项目选择腾讯实时语音识别 WebSocket 作为实时 ASR，默认模型 `16k_zh`。中继把 FreeSWITCH 的 8k 远端音频转换为 16k mono PCM 后发送；每次探索呼叫创建一条独立实时 ASR 会话并消费 partial/final 文本。腾讯文件识别 `8k_zh` 继续用于完整录音审计和实时链路失败时的回退。实时语音识别拥有独立免费资源包，不能与录音文件识别额度混用。

## Considered Options

- WebSocket 实时识别：延迟最低，适合等待提示结束、发送 DTMF 和检测人工边界。
- 流式异步识别任务：偏回调批处理，不能可靠支持逐步实时决策。
- 一句话识别：需要自行切音频、判断句尾和重试，边界复杂且延迟更高。

## Consequences

- 每通探索呼叫的实时 ASR 会话必须与 FreeSWITCH channel UUID 绑定。
- partial 文本用于前端直播和边界检测，final 文本用于节点转写和解析。
- 实时 ASR 断线时不能盲目继续按固定时间导航；应停止扩展或降级为只记录文件。
- 当前腾讯账号不支持 SDK 的 `result_mod=1` 句子模式；中继按兼容的实时结果协议处理 `result` 数据。
- 实测同一电话录音在 `8k_zh` 实时模型下只识别出开头英文，`16k_zh` 配合 8k→16k 上采样可识别完整中文菜单和按键提示。
- 当前 macOS 构建的 `mod_audio_stream` 直接发送 native PCM16，本地测试按 little-endian 解码；中继仍支持 L16BE/S16LE 两种声明，但实际接入前必须用已知音频校准字节序和声道。
