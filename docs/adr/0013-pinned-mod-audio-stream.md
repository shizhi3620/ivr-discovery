# 使用固定上游版本的 mod_audio_stream

项目选择 `amigniter/mod_audio_stream` 作为 FreeSWITCH 实时音频转发模块。该模块为 MIT 许可、支持 WebSocket 实时音频、8k/16k 采样率和单路通话，足以覆盖当前单 SIM 场景。上游源码不复制进本仓库，构建时固定具体 tag 或 commit，并记录依赖和安装步骤；这样保留许可证和升级边界，也避免把第三方源码混入项目实现。

## Consequences

- macOS 需要按上游构建说明准备 FreeSWITCH 开发头文件和 pkg-config。
- 升级模块必须显式修改固定版本并重新做实时音频回归测试。
- 社区版并发上限为 10 路，当前单 SIM 只使用 1 路。
- 当前固定版本为 `v1.1.0` / commit `3e0f52d2bf25912cf276cc1bd9fa31627f758860`。
- macOS 使用 Debug 构建绕开上游 Release 的 GNU strip 参数，并由构建脚本补齐 SpeexDSP include/link；生成的 Mach-O 文件按 Homebrew FreeSWITCH 约定安装为 `mod_audio_stream.so`。
