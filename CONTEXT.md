# IVR Discovery

本项目用于发现并可视化语音 IVR（交互式语音应答）的菜单树。当前讨论范围聚焦中国大陆的真实电话网络。

## Language

**IVR**:
通过语音菜单引导来电者完成交互的电话系统。一个 IVR 菜单包含若干可由 DTMF 按键或语音选项进入的路径。
_Avoid_: 语音客服、电话机器人

**IVR 发现**:
通过真实拨打和逐项导航，重建目标号码 IVR 菜单树的过程。
_Avoid_: 爬虫、扫描

**中国场景**:
通过中国大陆运营商网络完成电话外呼，并面向中国大陆 IVR 号码的系统运行环境。
_Avoid_: 国内版、中国兼容

**SIM 网关**:
以实体 SIM 卡、蜂窝语音链路和 Android 设备承载呼入或呼出，并与 SIP 网络互通的电话网关。
_Avoid_: 软电话、VoIP 网关

**DTMF 路径**:
从一次发现的根节点到当前 IVR 节点所依次输入的 DTMF 按键序列。
_Avoid_: 按键记录、菜单路径

**ASR**:
将 IVR 下行电话语音转写为文本的能力。
_Avoid_: 录音识别、语音转文字

**TTS**:
将文本合成为可注入 IVR 上行电话链路的语音的能力。
_Avoid_: 语音播放、语音克隆

**音频 Provider**:
封装录音转写和文本语音合成能力的接口，与承载电话链路的电话 Provider 分离。
_Avoid_: 腾讯云客户端、ASR 工具类

**腾讯云音频 Provider**:
中国大陆场景默认的音频 Provider，使用 `8k_zh_large` 录音文件识别和 8 kHz `TextToVoice` 合成。
_Avoid_: 默认 ASR 客户端、微信语音

**AI Provider**:
封装文本补全能力的接口，使转录解析不依赖具体模型厂商。
_Avoid_: Claude 封装、DeepSeek 客户端

**DeepSeek Provider**:
中国大陆场景默认的 AI Provider，通过 OpenAI 兼容接口完成 IVR 转录解析。
_Avoid_: 默认模型、国产 Claude

**语音决策链路**:
IVR 下行语音依次经过 ASR、文本决策模型和 TTS，形成对 IVR 的下一句话或下一项操作的闭环。
_Avoid_: AI 通话、机器人链路

**授权目标**:
其号码所有者明确允许本项目发起 IVR 发现的被叫号码。
_Avoid_: 目标号码、扫描对象

**人工监督验证**:
由操作者在场、只发起单次呼叫并实时决定是否继续或中止的验证方式。
_Avoid_: 手动测试、调试呼叫

**批量发现**:
在不超过授权目标边界的前提下，以串行、限速且可中止的方式发现多个 IVR 菜单树。
_Avoid_: 扫描、批量拨号

**DTMF 注入**:
将 SIP 侧收到的 DTMF 按键传递到蜂窝通话，使远端 IVR 接收该按键。
_Avoid_: 按键透传、DTMF 转发

**仅外呼**:
SIM 网关只接受 SIP 侧发起并转为蜂窝外呼，不把蜂窝来电接续到 SIP。
_Avoid_: 单向网关、呼入关闭

**软电话人工验证**:
由操作者通过局域网 SIP 软电话听取 IVR 菜单并手动发送 DTMF 的验证方式。
_Avoid_: 手动拨号、端到端测试

**专用测试设备**:
仅承载 SIM 网关与测试用途、不安装个人账号和日常数据的 Android 手机。
_Avoid_: 测试机、备用机

**专用 SIM**:
只用于 SIM 网关和测试呼叫、不承担日常个人通信的移动号码卡。
_Avoid_: 副卡、测试卡

**电话 Provider**:
封装一次真实电话外呼能力的接口，发现流程只依赖该接口，而不依赖具体厂商。
_Avoid_: 电话客户端、Bland 封装

**AndroidSimGatewayProvider**:
中国大陆场景的默认电话 Provider，经 FreeSWITCH 把外呼交给 Android SIM 网关并通过实体 SIM 卡拨号。
_Avoid_: 网关插件、SIM Provider

**Bland Provider**:
保留的非默认电话 Provider，用于维持既有演示与测试，不用于中国大陆真实外呼。
_Avoid_: 默认 Provider、海外方案

**通话录音**:
Android SIM 网关接通后由 FreeSWITCH 保存的单次外呼 WAV，作为腾讯云文件 ASR 的输入。
_Avoid_: 用户录音、通话监听
