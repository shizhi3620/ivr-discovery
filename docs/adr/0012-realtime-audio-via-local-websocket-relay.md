# 实时通话音频通过本地 WebSocket 中继接入实时 ASR

FreeSWITCH 需要把通话音频实时交给腾讯实时语音识别，但直接把供应商协议和凭据耦合进 FreeSWITCH 模块会增加构建、重连和测试难度。项目决定：FreeSWITCH 通过实时音频转发模块把 L16/PCM 音频发送到本地 WebSocket 中继；中继负责维护腾讯实时 ASR 会话，并向发现流程输出 partial/final transcript。文件 ASR 保留为审计和故障回退。

## Considered Options

- 模块直接连接腾讯实时 ASR：链路更短，但供应商协议、鉴权、重连和测试都进入 FreeSWITCH 模块。
- 录音切块后反复调用文件 ASR：实现简单，但有明显延迟，不是实时决策链路。
- Android 网关直接推流：需要修改无许可证的上游项目，扩大定制和分发风险。

## Consequences

- 本地中继成为实时导航的关键路径，必须具备断线处理、背压、时间戳和会话清理。
- FreeSWITCH 侧只需要稳定的实时音频转发协议，不携带腾讯云密钥。
- 实时文本必须同时驱动 DTMF 时序、人工边界停止和前端 `live_transcript`。
