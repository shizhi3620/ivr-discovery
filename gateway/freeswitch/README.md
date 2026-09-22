# FreeSWITCH 局域网网关配置

用于第一阶段验证：Mac 软电话 → FreeSWITCH → Android SIM 网关 → 联通 VoLTE → 目标 IVR。

## 前置条件

- macOS + Homebrew 原生 arm64 FreeSWITCH
  ```bash
  brew install freeswitch
  ```
  不使用 Docker 镜像：可用的 `safarov/freeswitch` 只有 `linux/amd64`，在 Apple Silicon 上需要模拟运行，RTP/音频桥接不稳定。
- Mac 有线或 Wi-Fi 局域网 IPv4 地址。**不要使用 VPN/虚拟网卡地址**（如 `198.18.0.1`、`10.147.17.x`）。

## 配置

```bash
cd gateway/freeswitch
cp .env.example .env.local
```

编辑 `.env.local`：

- `LAN_IP`：Mac 的局域网 IPv4，例如 `192.168.10.112`
- `SIP_DEFAULT_PASSWORD`：`gateway1` 与 `softphone` 共用的 SIP 密码
- `ESL_PASSWORD`：本机 `fs_cli` 密码
- `ESL_PORT`：默认 `18021`。避开 8021，因为本机代理常占用 8021

## 启动

```bash
./scripts/start.sh   # 渲染配置到 .runtime/（含真实密码，已 gitignore）
./scripts/run.sh     # 前台启动
```

另开一个终端验证：

```bash
./scripts/status.sh -x "status"
./scripts/status.sh -x "sofia status"
./scripts/status.sh -x "sofia status profile internal reg"
```

预期：

- `internal` profile 为 `RUNNING`，监听 `<LAN_IP>:5060`
- `PCMU,PCMA`、`rfc2833`
- 手机与软电话注册后，`reg` 中出现 `gateway1` 与 `softphone`

## macOS 防火墙

应用防火墙开启时，需在「系统设置 → 网络 → 防火墙 → 选项」中允许
`/opt/homebrew/opt/freeswitch/bin/freeswitch` 接受传入连接，否则 5060 绑定成功但收不到 SIP 包。

## SIP 账号

| 账号 | 用途 | 密码 |
| --- | --- | --- |
| `softphone` | Mac 软电话（人工验证） | `SIP_DEFAULT_PASSWORD` |
| `gateway1` | Android SIM 网关 | `SIP_DEFAULT_PASSWORD` |

软电话配置：服务器 `<LAN_IP>:5060`，UDP，明文，域 `<LAN_IP>`。

Android 网关使用 PJSIP，必须关闭 SRTP 才能与本机的明文 RTP 互通，并关闭 SIP outbound（`;ob`），否则局域网内没有共同媒体类型。这些改动位于上游仓库的本地补丁中，未提交到本仓库（见 `docs/adr/0001`、`docs/adr/0002`）。

## 拨号规则

当前拨号计划只白名单允许 `10010`（见 `docs/adr/0003`）。软电话直接拨打 `10010`：

1. FreeSWITCH 匹配 `softphone-to-gateway`
2. 以 `X-GSM-Destination` SIP 头携带被叫号码
3. 桥接到已注册的 `gateway1`
4. Android 网关从 `X-GSM-Destination` 头读取被叫号码并发起 GSM/VoLTE 外呼
5. 上行音频桥接到软电话；软电话发送的 RFC 4733 DTMF 经网关注入蜂窝通话

`10010` 仅用于人工监督下的单次冒烟测试。当前 `+86` 长号尚未加入拨号计划。要扩展号码范围，需同时修改
`conf/dialplan/default.xml` 的匹配表达式和 `docs/adr/0003` 中的白名单边界。

## 停止

```bash
./scripts/stop.sh
```
