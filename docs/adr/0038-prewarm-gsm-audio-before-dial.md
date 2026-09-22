# GSM 音频采集必须在拨号前预热

运营商 IVR 在蜂窝语音通话接通时立即播放欢迎语，而 Android 网关在
`CALL_STATE_OFFHOOK` 后才配置 mixer、打开 ALSA PCM，导致首个音节在进入
SIP/RTP 前已经播放并丢失。录音只能在“迎”开始，无法由 ASR 恢复。

项目决定在 Android 网关中增加拨号前音频预热：

- SIP 应答后、调用 `TelecomManager.placeCall()` 前，先完成 mixer 配置和
  ALSA capture/playback 打开。
- 预热完成后仍保持 `isCapturing=true`，PJSIP 音频端一旦连接即可阻塞等待
  第一帧下行 PCM。
- OFFHOOK 后直接复用已预热音频，不再做第二套初始化。
- 预热失败只记录错误并继续拨号，避免因为本地音频设备问题阻止通话。

该修改只保存在本地上游网关工作树，不提交源码、补丁或 APK 到
`ivr-discovery` 仓库。
