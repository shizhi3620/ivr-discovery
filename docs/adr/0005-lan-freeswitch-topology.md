# 第一阶段使用局域网明文 SIP/RTP 的 FreeSWITCH

第一阶段由 Mac 上的 Homebrew 原生 arm64 FreeSWITCH 承载，SIP 软电话与 Redmi Note 7 网关通过同一 Wi-Fi 局域网注册。FreeSWITCH 暴露 UDP 5060 和 RTP 端口，不使用 TLS/SRTP；SIP 凭据独立于其他服务，且服务只面向受信局域网。该选择优先降低联调复杂度，但不把该拓扑外推到公网或生产环境。

选择原生而非 Docker，是因为本机可用的 FreeSWITCH 镜像只有 `linux/amd64`，在 Apple Silicon 上需模拟运行，RTP/音频桥接不稳定；Homebrew 提供原生 arm64 包。两台 SIP 端点（软电话与 Android 网关）都必须使用明文 RTP：Android 网关的 PJSIP 默认强制 SRTP，若不关闭，`originate` 会因没有共同媒体类型返回 `INCOMPATIBLE_DESTINATION`。

## Consequences

- 任何能访问该局域网的设备都可尝试连接 SIP/RTP，必须依赖强密码和网络隔离。
- Android 网关必须关闭 SRTP 和 SIP outbound（`;ob`）才能与明文局域网互通；该改动属于上游代码的本地补丁，不随本仓库分发。
- 若后续需要公网接入，必须重新评估 Tailscale、TLS/SRTP 和防火墙策略，而不是直接转发端口。
