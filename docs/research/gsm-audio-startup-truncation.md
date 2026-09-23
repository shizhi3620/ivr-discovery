# GSM 网关录音首字截断

## 现象

用户试听 `cfb1d58a-4c1d-43ae-829d-e71886c0d484.wav` 时，听到欢迎语从
“欢迎致电Apple”的“迎”开始，首个“欢”没有出现。

录音分析：

- 前 `5.0` 秒为静音。
- `5.02–5.33` 秒突然出现高能量语音，没有自然起音。
- 因而文件不是完整从欢迎语起点开始，而是在播放已经开始后进入采集。

## 原因

Android 网关在蜂窝通话接通后才完成以下步骤：

1. SIP 呼叫已由 FreeSWITCH 建立。
2. Android 开始 GSM/VoLTE 外呼。
3. rooted 网关配置 ALSA mixer。
4. `GsmAudioPort` 打开、启动 capture PCM。
5. 音频桥开始向 SIP/RTP 发送下行音频。

运营商 IVR 在 GSM 通话接通时立即开始播放欢迎语，而第 3–5 步存在百毫秒级
启动延迟。FreeSWITCH 录音只能保存 Android 已经送出的 RTP，无法恢复这段
启动前已经丢失的手机下行音频。

因此这是**网关采集起点截断**，不是纯 ASR 漏字；ASR 只是进一步把残缺片段
误识别为“点Apple”等文本。

## 当前处理

- 录音开头标记为 `[录音起点缺失首字]`。
- 不自动把残缺录音补写成完整欢迎语。
- 报告和审计需要注明首个音节缺失。

## 后续修复方向

- 在拨号前预初始化 ALSA capture/mixer，通话接通时立即消费下行 PCM。
- 使用常驻 modem/ALSA 下行采集，而不是通话状态下临时打开。
- 若无法消除启动延迟，在媒体就绪前缓存下行音频，并在建立桥后补发。
- 对检测到突然起音的录音自动标记 `audio_start_truncated=true`。

## 已实施验证

本地 Android 网关工作树已实现拨号前预热：

1. SIP 应答后先调用音频准备流程。
2. 完成 mixer 配置并打开 ALSA capture/playback。
3. 之后才调用 `TelecomManager.placeCall()`。
4. OFFHOOK 时复用已打开的 PCM。

使用无效应答号码做本地 SIP 测试，未拨打 GSM：

```text
GsmAudioPort: Preparing native audio before GSM dial...
AudioBridge: Audio bridge started
GsmAudioPort: Native audio prepared in 4567 ms
CallMgr: Invalid phone number: invalid
```

预热在拨号前完成，测试后无残留 SIP 频道。

## 真实通话复验

在 `2026-09-23 07:58 CST` 仅监听、不发送 DTMF 拨打 `4006668800`：

- channel：`2fc700c8-5cf3-4a91-baf0-bb43df8bad17`
- `SIP call answered` 后开始预热
- `Native audio prepared in 4644 ms`
- 预热完成后才执行 `Placing GSM call`
- 录音时长 `44.54` 秒
- 腾讯 ASR 首句完整返回：`感谢您致电Apple`

因此首个音节截断问题已修复。录音已通过 QuickTime 播放供人工确认。
