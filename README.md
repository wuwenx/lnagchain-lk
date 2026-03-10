# LangChain + 飞书（Lark）交互系统

基于 LangChain 与飞书开放平台的对话机器人：接收飞书消息，LangChain 生成回复，再通过飞书 API 发回。支持 **Webhook（请求地址）** 模式，适配当前 Lark 事件配置。

## 功能

- **Webhook 模式**：在 Lark 后台配置「请求地址」，事件推送到你的 HTTP 服务（需公网 URL，如 ngrok）
- **文本消息**：用户发文本 → AI 回复
- **读飞书文档**：用户消息中含飞书文档链接（`feishu.cn/docx/xxx` 或 `larksuite.com/docx/xxx`）或**知识库链接**（`.../wiki/xxx`）时，自动拉取正文并作为上下文交给 AI 回答
- **群聊**：群内仅在被 @ 时回复（可配置 `FEISHU_GROUP_ACCESS`）
- **可扩展**：可在此项目上增加 RAG、Tools、多轮历史等

## 环境要求

- Python 3.10+
- 飞书/Lark 企业自建应用（App ID、App Secret）
- OpenAI 兼容 API（如 OpenAI、国内中转等）
- **Webhook 模式**：公网可访问的 URL（如 ngrok、云服务器）

## 飞书/Lark 应用配置

1. 打开 [飞书开放平台](https://open.feishu.cn/app) 或 [Lark 开发者后台](https://open.larksuite.com)，创建**企业自建应用**
2. **权限**：开启 `im:message`、`im:message.group_at_msg`、`im:resource`、`contact:user.id:readonly`；**若需机器人读飞书文档**，再开启 `docx:document`；**若需读知识库（Wiki）链接**，再开启 `wiki:wiki` 或 `wiki:wiki.readonly`（以飞书/ Lark 文档为准）
3. **事件配置**：选择 **将事件发送至开发者服务器**，**请求地址**填你的公网 URL（见下方「Webhook 模式」）
4. 在应用凭证页复制 **App ID**、**App Secret**；在事件配置页复制 **Verification Token**（若开启加密则还有 **Encrypt Key**）

## 安装与运行

### Webhook 模式（推荐：请求地址）

```bash
cd lnagchain-lk
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env：FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_VERIFICATION_TOKEN、OPENAI_API_KEY 等
```

在 `.env` 中填写 **FEISHU_VERIFICATION_TOKEN**（事件配置里的 Verification Token）。若事件配置里开启了「加密」，再填写 **FEISHU_ENCRYPT_KEY**。

本地启动 Webhook 服务（需让公网能访问，例如用 ngrok 转发 9000 端口）：

```bash
.venv/bin/python main_webhook.py
```

将 Lark 后台「请求地址」设为：`https://你的公网域名/` 或 `https://你的公网域名/webhook`（若 `.env` 里 `WEBHOOK_PATH=/webhook`）。保存后 Lark 会发 `url_verification`，通过即可收到消息事件。

### 若仍支持 WebSocket 长连接

```bash
.venv/bin/python main.py
```

## 配置说明（.env）

| 变量 | 必填 | 说明 |
|------|------|------|
| `FEISHU_APP_ID` | 是 | 飞书应用 App ID（如 cli_xxx） |
| `FEISHU_APP_SECRET` | 是 | 飞书应用 App Secret |
| `FEISHU_VERIFICATION_TOKEN` | Webhook 必填 | 事件配置里的 Verification Token |
| `FEISHU_ENCRYPT_KEY` | 加密时必填 | 事件配置里开启加密时的 Encrypt Key |
| `OPENAI_API_KEY` | 是 | LLM API Key（OpenAI、DeepSeek 或兼容服务） |
| `OPENAI_API_BASE` | 否 | API 地址，默认 `https://api.openai.com/v1`；DeepSeek 填 `https://api.deepseek.com/v1` |
| `OPENAI_MODEL` | 否 | 模型名，默认 `gpt-4o-mini`；DeepSeek 可用 `deepseek-chat`、`deepseek-reasoner` 等 |
| `FEISHU_GROUP_ACCESS` | 否 | 群聊模式：`open`（默认）/ `allowlist` / `disabled` |
| `FEISHU_DOMAIN` | 否 | 国际版 Lark 填 `https://open.larksuite.com`，国内飞书默认 `https://open.feishu.cn` |
| `WEBHOOK_PORT` | 否 | Webhook 监听端口，默认 9000 |
| `WEBHOOK_PATH` | 否 | Webhook 路径，默认 `/`（请求地址 = 公网URL + 此路径） |

## 项目结构

```
lnagchain-lk/
├── main_webhook.py      # Webhook 模式入口（请求地址）
├── main.py              # WebSocket 模式入口（若平台仍支持）
├── handlers.py          # 事件处理（消息解析、LangChain 回复）
├── feishu_doc.py        # 飞书文档/知识库链接解析与正文拉取
├── lark_client.py       # 飞书 HTTP 客户端与发送消息
├── langchain_agent.py   # LangChain 对话链（可改为 Agent/RAG）
├── config.py            # 配置加载与校验
├── requirements.txt
├── .env.example
└── README.md
```

## 扩展建议

- **多轮对话**：在 `langchain_agent.py` 中按 `chat_id` 维护历史，传入 `reply(..., history=...)`
- **流式回复**：先发一条消息，再循环调用飞书「更新消息」接口
- **RAG**：使用 `LarkSuiteDocLoader` 等加载飞书文档，接入当前链
- **Tools**：在 `langchain_agent.py` 中改为 Agent + Tools 即可

## 参考

- [飞书开放平台 - 长连接接收事件](https://open.feishu.cn/document/server-docs/im-v1/message-event/event-list)
- [OpenClaw 飞书集成说明](https://openclaws.io/zh/blog/openclaw-feishu-integration)
- [LangChain 文档](https://python.langchain.com/)
