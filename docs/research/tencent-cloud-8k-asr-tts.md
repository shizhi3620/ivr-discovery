# 腾讯云 8k 电话 ASR / TTS API 核对

核对日期：2026-09-22。以下字段以腾讯云官方 Python SDK 的生成模型和请求签名实现为准；官方文档链接同时列出，便于后续再次核对。

## 录音文件识别 ASR

- 产品接口：`CreateRecTask`，API Version `2019-06-14`，endpoint `asr.tencentcloudapi.com`，service `asr`。
- 电话场景应使用 8k 引擎；当前实现默认 `EngineModelType=8k_zh`，该标准引擎由录音文件识别免费包覆盖。`8k_zh_large` 可作为可选大模型引擎，但需要单独额度。
- 本地音频 Base64 提交时必须使用 `SourceType=1`，并提供 `Data`（Base64 字符串）和 `DataLen`（未编码前的字节长度）。
- 本地音频大小限制为 5 MB（含）。URL 提交使用 `SourceType=0` 和 `Url`。
- `ResTextFormat=0` 返回基础识别文本；本实现不需要词级时间戳。
- 8k 电话音频建议 `ChannelNum=2`，双声道分别对应通话双方；当前 FreeSWITCH 录音同时设置 `RECORD_STEREO=true`。
- 结果轮询接口为 `DescribeTaskStatus`，请求字段为 `TaskId`（uint64）。
- `TaskStatus.Status`：`0` 等待、`1` 执行中、`2` 成功、`3` 失败。
- 成功文本在 `Data.Result`；失败原因在 `Data.ErrorMsg`；任务 ID 有效期为 24 小时。

官方 API 页面：

- https://cloud.tencent.com/document/api/1093/37823
- https://cloud.tencent.com/document/api/1093/37822

官方 SDK 生成模型（本次字段核对来源）：

- https://github.com/TencentCloud/tencentcloud-sdk-python/blob/master/tencentcloud/asr/v20190614/models.py
- https://github.com/TencentCloud/tencentcloud-sdk-python/blob/master/tencentcloud/asr/v20190614/asr_client.py

## 语音合成 TTS

- 产品接口：`TextToVoice`，API Version `2019-08-23`，endpoint `tts.tencentcloudapi.com`，service `tts`。
- 请求文本字段为 `Text`；每次请求建议提供唯一 `SessionId`。
- `SampleRate=8000` 可获得 8 kHz 电话采样率音频；`Codec=wav` 可直接交给 FreeSWITCH playback/broadcast。
- `VoiceType` 是音色 ID，官方接口未在此模型层声明固定默认值；本项目把它做成可选环境变量，未设置时由腾讯云侧决定默认音色。
- 成功响应中的 `Audio` 是 Base64 编码的 wav/mp3 音频；本实现按 WAV 解码后写入本地文件。
- 单次 `TextToVoice` 的中文文本上限为 150 个汉字，适合短语音导航，不适合长文本播报。

官方 API 页面：

- https://cloud.tencent.com/document/api/1073/37995

官方 SDK 生成模型（本次字段核对来源）：

- https://github.com/TencentCloud/tencentcloud-sdk-python/blob/master/tencentcloud/tts/v20190823/models.py
- https://github.com/TencentCloud/tencentcloud-sdk-python/blob/master/tencentcloud/tts/v20190823/tts_client.py

## TC3-HMAC-SHA256 签名

- 使用 HTTPS POST JSON；官方 SDK 的签名 canonical headers 只签 `content-type;host`。
- Header 使用官方 SDK 的 `Content-Type: application/json`，并携带 `Host`、`X-TC-Action`、`X-TC-Timestamp`、`X-TC-Version`，区域可用时携带 `X-TC-Region`。
- Authorization 形式为：
  `TC3-HMAC-SHA256 Credential=<SecretId>/<date>/<service>/tc3_request, SignedHeaders=content-type;host, Signature=<hex>`
- `X-TC-Action` 不需要进入 SignedHeaders；它由公共请求头传递。
- 请求 payload 必须与实际签名的 JSON 字节完全一致，本项目先序列化一次，再将该字节作为请求体发送。

官方签名说明：

- https://cloud.tencent.com/document/api/1093/35640

官方 SDK 签名实现：

- https://github.com/TencentCloud/tencentcloud-sdk-python/blob/master/tencentcloud/common/abstract_client.py
- https://github.com/TencentCloud/tencentcloud-sdk-python/blob/master/tencentcloud/common/sign.py

## 当前未验证项

- 未使用真实腾讯云账号执行线上请求；当前验证覆盖请求结构、Base64/DataLen、轮询状态、TC3 Authorization 格式和 TTS 音频落盘。
- 未指定默认 `VoiceType`。首次上线前应通过腾讯云控制台选择可用音色，并设置 `TENCENT_TTS_VOICE_TYPE`。
