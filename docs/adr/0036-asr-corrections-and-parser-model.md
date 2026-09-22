# 结构化解析使用非推理模型，并允许环境级 ASR 短语纠正

`deepseek-flash` 在当前账号下会把大部分输出预算消耗在 `reasoning_content`。
当 `max_tokens` 不足时 `content` 返回空字符串，导致菜单解析失败；同一转录
使用 `deepseek-chat` 可在约 1 秒内稳定返回正确 JSON。

同时，腾讯实时 ASR 会把 Apple 话术中的：

- 首个音节可能因 GSM 音频桥启动延迟缺失，ASR 将残缺的首句识别成“点Apple”
- “For technical support in English, press two”识别成“Export in English”

项目决定：

- DeepSeek 默认模型改为 `deepseek-chat`。
- DeepSeek 遇到只有 `reasoning_content` 的响应时自动提高 token 上限重试一次。
- 转录解析携带 `dtmf_path`，明确忽略已执行的前置菜单，只解析当前目标节点。
- 提供 `ASR_PHRASE_CORRECTIONS_JSON` 环境级确定性替换表；它同时作用于实时
  决策和通话后解析。
- 对已知音频起点缺失，使用 `[录音起点缺失首字]` 标记，不伪造完整欢迎语。
- 英文数字词 `one` 到 `nine` 与“press/key”组合必须能提取 DTMF。

不把供应商特定话术写死在通用代码中；本地测试目标的具体替换规则保存在
未提交的 `.env`。
