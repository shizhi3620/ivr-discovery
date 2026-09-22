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

## 最终通过

人工 Linphone 端点不适合作为确定性媒体源：`uuid_broadcast` 没有把测试 WAV 可靠播放到 Linphone，而 `playback` 测试会被操作者麦克风污染。

最终使用 `pjsua 2.17` 作为自动接听测试端点：

- FreeSWITCH 以 `user/testclient` 呼出。
- pjsua 通过 `--auto-answer=200` 自动接听。
- pjsua 通过 `--play-file` 和 `--auto-play` 将 45 秒测试 WAV 发送给 FreeSWITCH。
- `mod_audio_stream` 以 `mono + s16le` 捕获 testclient leg 的 PCM。
- 中继完成 8k→16k 转换并送腾讯 `16k_zh`。
- 腾讯返回多个 partial/final，完整识别隐私说明和“如果您同意，请按1”。
- 800 ms 静音后产生 `dtmf_ready`。
- 控制器执行 `uuid_send_dtmf`，FreeSWITCH 返回成功。

阶段 3 已通过。该测试仍完全位于局域网，没有经过运营商。

## 结论

可以用 `gateway/freeswitch/scripts/run-pjsua-testclient.sh <wav>` 启动确定性测试端点，再用 `backend/realtime/softphone_test.py --extension testclient` 做端到端回归。进入阶段 4 前仍需人工确认本轮输出和授权目标范围。
