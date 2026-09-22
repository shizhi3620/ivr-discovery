# AI 能力通过 Provider 隔离，DeepSeek 作为中国默认实现

转录解析原先直接绑定 Anthropic Claude。中国大陆场景需要默认使用可稳定访问的 DeepSeek，同时保留海外演示所需的 Anthropic。项目通过 `AIProvider` 接口隔离文本补全能力，`transcript_parser.py` 只依赖接口，不直接创建厂商客户端；默认实现为 DeepSeek，`AI_PROVIDER=anthropic` 可切回 Claude。

## Consequences

- 解析器不感知模型厂商；JSON 校验、选项去重和导航项过滤仍由项目代码负责，不能把正确性完全交给模型。
- DeepSeek 使用 OpenAI 兼容的 `/chat/completions`，默认模型名可通过 `DEEPSEEK_MODEL` 覆盖，避免模型命名变化迫使代码修改。
- Anthropic 继续作为可选实现存在，但不进入中国大陆默认部署基线。
- API 密钥只放在 `backend/.env`，不得提交到仓库。

实现记录见 `backend/ai/` 与 `backend/tests/test_ai_providers.py`。
