# 中国场景采用 Redmi Note 7 Android SIM 网关

当前阶段只解决中国大陆的真实 IVR 外呼。个人可办理且稳定支持大陆 +86 外呼的云电话服务不存在可靠选择，因此采用自建 Android SIM 网关：Redmi Note 7（`lavender`，骁龙 660）、LineageOS、Magisk、`s-xander/Android-sip-gateway` 与 FreeSWITCH。选择该机型是因为它是唯一被目标开源项目公开验证的设备；MTK 平台、Pixel IMS 方案以及未验证的骁龙 632 机型均不进入当前基线。

## Consequences

- 第一阶段以 `lavender` 为设备基线，优先复刻上游验证的 LineageOS 17.1 行为，但不硬锁版本号；实际采用可取得、VoLTE 可用且能正确加载 Qualcomm 音频控件的 LineageOS 构建。
- 该系统组合已停止安全更新，只允许承载专用测试设备上的 SIM 网关。
- 该路线依赖已停止安全更新的 LineageOS、root 和 permissive SELinux，不能视为生产级稳定方案。
- 第一阶段只验证中国联通 VoLTE 下的单条真实呼叫、音频采集/注入与 DTMF 闭环。
