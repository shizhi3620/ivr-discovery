# 真实呼叫前必须通过 Android WiFi 稳定性门禁

首次真实根节点呼叫中，VoLTE 通话触发了 Android 的 WiFi 互联网可达性探测
失败。系统随后在多个同名 AP 之间重连，并在每次重连时切换随机 MAC，导致
`wlan0` 短暂 down/up。SIP/RTP 周期性中断，最终录音为静音。

项目决定：真实呼叫前必须由 `gateway/android/scripts/stabilize-wifi.sh`
关闭互联网探测断连、已联网 AP 主动切换和驱动省电，再通过
`gateway/android/scripts/preflight-wifi.sh` 连续观察至少 60 秒。门禁要求
BSSID 不变、驱动省电关闭且手机到 Mac 零丢包。

该门禁不访问运营商号码，也不发起 SIP 或蜂窝呼叫。WiFi 只作为手机到
FreeSWITCH 的局域网承载，公网探测失败不等于 SIP 链路失败。

## Consequences

- 手机或 WiFi 服务重启后必须重新执行稳定化脚本。
- 真实呼叫前必须重新通过预检，不能复用上一次通话的结果。
- 预检失败时应停止真实呼叫，不消耗运营商通话预算。
