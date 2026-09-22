# 中国电话音频使用腾讯云 8k ASR/TTS

Android SIM 网关只承载蜂窝音频，不提供云端转写。中国大陆第一阶段采用腾讯云录音文件识别和语音合成：ASR 使用 `CreateRecTask` / `DescribeTaskStatus`（Version `2019-06-14`），电话模型 `8k_zh_large`，本地 WAV 以 `SourceType=1` 和 Base64 提交；TTS 使用 `TextToVoice`（Version `2019-08-23`），输出 8 kHz WAV。音频能力放在独立 `AudioProvider` 边界，Android 电话 Provider 只负责录音、DTMF 注入和播放合成音频。

## Consequences

- 电话 Provider 直接外呼 Android 网关，解析真实 channel UUID 后执行 `uuid_record start`，并设置 `RECORD_STEREO=true`。FreeSWITCH 必须加载 `mod_sndfile`，否则 WAV 录音不会落盘。ASR 默认按双声道 8k 电话音频识别。
- 腾讯云本地音频单文件上限为 5 MB，因此录音必须按单次外呼拆分并及时提交；当前实现使用文件 ASR，不是实时流式识别。
- ASR 完成前节点保持 `calling`；完整转写通过一次 `live_transcript` 回调送出，不能把该事件理解为逐字流式结果。
- Android 电话能力只有在 `TENCENTCLOUD_SECRET_ID` 和 `TENCENTCLOUD_SECRET_KEY` 存在时才声明可用；缺少凭证时发现流程拒绝拨号。
- `VoiceType` 不做代码内默认绑定，部署时通过 `TENCENT_TTS_VOICE_TYPE` 选择可用音色。
- 通话 WAV 含真实通话内容，只允许保存在受控目录并遵守保留/删除策略，不得提交仓库。
- 腾讯云接口字段核对和来源见 `docs/research/tencent-cloud-8k-asr-tts.md`。
