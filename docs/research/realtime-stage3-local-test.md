# 实时发现阶段 3 本地测试记录

阶段 3 目标是在不拨打运营商电话的前提下验证：

```text
FreeSWITCH 真实 RTP
  → mod_audio_stream
  → 本地 WebSocket 中继
  → 腾讯 16k_zh
  → 确定性 DTMF/安全挂断
```

## 已验证

- `mod_audio_stream v1.1.0` 可以在 Homebrew FreeSWITCH 1.11.3 上构建、加载、卸载和重新加载。
- `uuid_audio_stream` API 可以连接本地中继并持续发送 PCM 帧。
- 中继可以连接腾讯实时 ASR WebSocket，接收 partial/final 文本。
- 原始 8k stereo WAV 通过本地中继回放时，中继完成 8k→16k 单声道转换，腾讯 `16k_zh` 正确识别“如果您同意，请按1”，并在 800 ms 静音后产生 `dtmf_ready`。
- Linphone 作为真实 RTP 端点时，`mod_audio_stream` 可以捕获实时音频；中继能收到非零 PCM 帧并产生 ASR 文本。
- 非菜单语音会触发 `unknown_boundary`，测试控制器可以执行安全挂断。

## 尚未通过

- 使用 `uuid_broadcast` 向 parked Linphone channel 播放测试 WAV 时，Linphone 没有听到目标音频。
- 使用 `playback` 应用时模块能捕获到部分媒体，但 Linphone 人工端点同时会采集操作者麦克风，测试过程被真人语音污染。
- 因此“确定性的已知提示音频 → final ASR → dtmf_ready → DTMF 写入”还没有在本地稳定复现。

## 结论

阶段 3 仍未放行，不能进入阶段 4 的真实电话。下一步需要一个不依赖人工麦克风的本地音频源，优先选择：

1. 可自动接听并向 FreeSWITCH 发送 WAV 的本地 SIP/RTP 测试端点。
2. `mod_rtp` 配合本地 RTP 音频发生器。
3. 能在拨入后自动播放固定 WAV 的第二台 SIP 测试客户端。
