# 阶段 5：单键实时导航真实呼叫

日期：`2026-09-22 22:06 CST`

目标：`4006668800`

FreeSWITCH 频道：`528e177e-32f4-4c70-ba2b-bb0587007d78`

录音：`/tmp/ivr-discovery-recordings/528e177e-32f4-4c70-ba2b-bb0587007d78.wav`

## 执行条件

- 自助服务时窗（21:00-09:00）
- 单次真实呼叫，最长 45 秒
- 网关保持单实例，Android WiFi 预检通过
- 第一段实时 ASR 只允许目标键 `1`
- 注入后立即启动第二段只监听实时判断，强关键词人工边界会立即挂断

## 实时链路

腾讯实时 ASR 在通话中依次返回隐私说明的 partial/final，并在
`22:07:06.971 CST` 产生：

```json
{"event_type":"dtmf_ready","key":"1","text":"Apple为了给您提供最好的服务，按照apple隐私政策的规定，与本次通话相关的部分有限个人信息，可能会在中国大陆境外存储和处理。 如果您同意，请按1，如需结束本次通话，请按2。"}
```

FreeSWITCH 随后执行 `uuid_send_dtmf ... 1`。Android 日志确认：

```text
GatewayCall: DTMF: 1
GatewayInCall: Playing DTMF on GSM call: 1
ImsSenderRxr: DTMF_START [SUB0]
ImsSenderRxr: DTMF_STOP [SUB0]
```

第二段实时监听收到下一层菜单：

```text
感谢您致电Apple，普通话按1。
For tech support in English, press 2.
```

文件 ASR 对整通电话给出相同结果。

## 结果

- 录音时长 `43.9` 秒
- 远端 IVR 声道 RMS `2123.15`，最大振幅 `22908`
- 本地声道 RMS `1.0`
- VoLTE 通话期间 BSSID 始终为 `48:5f:08:47:07:6c`
- 手机到 Mac 全程 ping 成功，没有 WiFi 断连日志
- 未出现人工关键词，没有转接人工

阶段 5 通过。下一个可验证节点是 `1` 之后的中文语言菜单，目标键仍为 `1`。
