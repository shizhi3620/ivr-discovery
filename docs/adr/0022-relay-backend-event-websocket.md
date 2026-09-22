# 中继与后端通过 localhost WebSocket JSON 事件流通信

中继只把结构化实时文本事件发送给后端，不传输原始音频。事件包含 channel UUID、探索呼叫 ID、事件类型、文本、sequence 和 timestamp；后端作为 localhost WebSocket 客户端订阅。中继为每个通话保留有界事件缓冲，后端重连后可补发短时间内的 partial/final。后端再通过现有 UI WebSocket 推送 `live_transcript`。

## Consequences

- 事件必须可按 sequence 检测缺口和去重，不能把乱序 partial 当成最新提示。
- 后端断线不改变中继的音频和腾讯 ASR 会话；重连后按缓冲补读。
- 序列缺口、中继错误或事件超时必须作为实时控制故障处理，不能继续盲目 DTMF。
