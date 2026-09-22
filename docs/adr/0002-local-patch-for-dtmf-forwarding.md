# 通过本地补丁转发 SIP DTMF 到 Android 蜂窝通话

`Android-sip-gateway` 收到 SIP DTMF 时目前只记录日志，没有调用 Android Telecom 的 `Call.playDtmfTone()`，因此无法驱动真实 IVR 进入下一级菜单。第一阶段决定对上游代码使用最小本地补丁：FreeSWITCH 通过 RTP 内的 RFC 4733 电话事件发送 DTMF；网关收到 SIP DTMF 后，由 `GatewayInCallService` 持有的当前蜂窝通话播放对应按键，并在短时间后停止。SIP INFO 仅作为该方法失败时的回退，不并列为默认路径。上游仓库未声明许可证，因此不把其源码或构建产物纳入 `ivr-discovery`，也不对外分发修改版；若未来需要发布，必须先解决授权问题。

## Consequences

- 第一阶段的 DTMF 验收依赖本地补丁，不能依赖上游当前版本。
- 第一阶段只验证 RFC 4733 电话事件；SIP INFO 只有在前者失败时才作为回退方案验证。

## 验证记录（2026-09-22）

在一次人工监督的 `10010` 呼叫中实测确认整条 DTMF 链路：FreeSWITCH `uuid_send_dtmf` 分别发送 `1`、`2`；手机日志显示 `GatewayCall: DTMF: 1/2` → `GatewayInCall: Playing DTMF on GSM call` → Telecom `START_DTMF` → ImsService `DTMF_START [SUB0]`，随后 `STOP_DTMF`/`DTMF_STOP`。说明 RFC 4733 DTMF 已被网关接收并注入联通 VoLTE 蜂窝通话。
