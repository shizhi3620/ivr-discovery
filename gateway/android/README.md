# Android SIM 网关稳定性

Redmi Note 7 通过 WiFi 连接 Mac 上的 FreeSWITCH。`4006668800` 的首次真实
根节点呼叫暴露了以下问题：

- VoLTE 通话期间，Android 的互联网探测会因 DNS 超时判定 WiFi 不可用。
- 手机在多个同名 AP 之间切换，每次重连都会更换随机 MAC；设置 MAC 时
  `wlan0` 会被短暂 down/up。
- 这会让 SIP/RTP 在通话中周期性中断，最终录音只有静音。

当前稳定方案不依赖公网可达性，只要求手机能够始终访问 Mac 的局域网地址。
真实呼叫前运行：

```bash
gateway/android/scripts/stabilize-wifi.sh
gateway/android/scripts/preflight-wifi.sh --duration 75 --interval 5
```

`stabilize-wifi.sh` 会执行以下可逆调整：

- 关闭“互联网探测失败即断开 WiFi”
- 关闭已联网状态下主动切换 AP
- 关闭 WiFi 驱动省电

`preflight-wifi.sh` 只在 Mac 和手机之间做连通性检查，不会发起任何 SIP 或
蜂窝呼叫。只有持续观察期内 BSSID 不变、驱动省电关闭且零丢包，才允许进入
真实呼叫复验。

重启手机或 WiFi 服务后必须重新运行 `stabilize-wifi.sh`。
