# LangChain + 飞书（Lark）交互系统

基于 LangChain 与飞书开放平台的对话机器人：接收飞书消息，LangChain 生成回复，再通过飞书 API 发回。支持 **Webhook（请求地址）** 模式，适配当前 Lark 事件配置。

## 功能

- **Webhook 模式**：在 Lark 后台配置「请求地址」，事件推送到你的 HTTP 服务（需公网 URL，如 ngrok）
- **文本消息**：用户发文本 → AI 回复
- **图片消息（截图识别）**：用户在会话中发送图片（含纯图、或「文字+图」），机器人通过飞书「获取消息中的资源文件」下载图片，再以**支持多模态**的模型识别内容并回复（需应用开通消息资源相关权限，如 `im:resource`；建议在 `.env` 配置 `OPENAI_VISION_MODEL` 为支持 vision 的模型，与纯文本模型可分开）
- **读飞书文档**：用户消息中含飞书文档链接（`feishu.cn/docx/xxx` 或 `larksuite.com/docx/xxx`）或**知识库链接**（`.../wiki/xxx`）时，自动拉取正文并作为上下文交给 AI 回答
- **网页抓取**：消息中同时包含「获取」或「抓取」且包含一个网址时，用 **Playwright** 抓取页面正文，再由大模型总结/分析并回复到 Lark（需安装 `playwright` 并执行 `playwright install chromium`）
- **直接创建 Lark 文档**：说「新建 lark 文档」「帮我新建一个 xxx 文档」或发 `/新建文档` 时，机器人会**直接调用飞书 API 创建云文档**并返回链接（需应用有云文档创建权限；可选配置 `FEISHU_DOC_BASE_URL` 以返回可点击链接）
- **群聊**：群内仅在被 @ 时回复（可配置 `FEISHU_GROUP_ACCESS`）
- **处理中表情**：确定会回复时，在用户该条消息上自动添加表情回应（默认 SMILE😊，可配置 `FEISHU_REACTION_EMOJI`，须为飞书支持的 emoji_type）；需应用具备「发送、删除消息表情回复」权限（im:message.reactions:write_only）
- **流式回复**：默认对话路径下，先发一条「思考中…」占位消息，再边生成边调用飞书「更新消息」接口更新同一条消息（约 0.4 秒节流，避免限频）
- **交易所资金费率**：通过 **ccxt** 请求交易所 API，提供 `get_funding_rate` 工具（LangChain Tool）及 `/资金费率` skill；**默认对话已接入 Agent**，自然语言问「Binance 今日 BTC 资金费率是多少？」等会由 Agent 自动调工具并整理回复；**资金费率结果支持飞书卡片展示**（标题 + 各交易所费率块 + 下一结算时间）
- **多交易所流动性深度对比**：Agent 工具 `get_liquidity_depth_multi_tool`，一次传入多所（如 "okx,binance"）与标的（如 ETH），按**多档**（默认 12 档：0.01%～1%）返回深度（USDT），拿到多少订单簿数据就按多少档汇总分析
- **多群流水线**：在 A 群 @ 机器人发消息时，自动走「A=需求分析 → B=方案生成 → C=总结输出」三阶段，结果依次发到 A、B、C 群，最终总结在 C 群输出（需在 `.env` 配置三个群的 `FEISHU_PIPELINE_STAGE_*_CHAT_ID`）
- **文档/知识库搜索**：发 `/search 关键词` 或「搜索 xxx」时，调用飞书开放平台 [search v2 doc_wiki](https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/search-v2/doc_wiki/search) 搜索企业内文档与知识库，再由大模型总结汇总；需应用具备文档与知识库读权限
- **可扩展**：可在此项目上增加 RAG、Agent+Tools 等
- **本地代码助手**：基于 LangGraph ReAct 智能体，提供读/写/精准替换/执行命令等工具，实现类似 OpenClaude/Cline 的本地代码修改能力（见「本地代码助手」小节）
- **生成前端**：在指定群发「生成前端」并附带飞书需求文档链接，自动拉取 Lark 文档 + Apifox 接口文档（开放 API），由代码助手生成路由、菜单、页面并写入 `CODE_WORKSPACE_ROOT`（需配置 Apifox 令牌与项目 ID）
- **Metabase 看板页**：在与 `/code` 相同白名单群发 `/metabase <slug> <DashboardID> <现货|合约>`（或 `id <ID> <slug> …`），按固定流程在 `CODE_WORKSPACE_ROOT`（如 mm-admin）中增 `metabase.js`、新建 `pages/metabase/<slug>.vue`、改侧栏导航；流程全文见本仓库 `docs/metabase-add-page.md`，亦可放在工作区 `docs/` 下或配置 `METABASE_ADD_PAGE_DOC_PATH`
- **Apifox 接口查询**：发 `/api` 或 `/api 帮助` 查看说明；`/api` 列出当前模块全部接口，`/api 做市` 等关键词按目录/tag 或路径筛选，`/api <模块ID>` 指定 Apifox 模块（可选配置 `APIFOX_MODULE_MAP` 用中文别名）

## 环境要求

- Python 3.10+
- 飞书/Lark 企业自建应用（App ID、App Secret）
- OpenAI 兼容 API（如 OpenAI、国内中转等）
- **Webhook 模式**：公网可访问的 URL（如 ngrok、云服务器）

## 飞书/Lark 应用配置

1. 打开 [飞书开放平台](https://open.feishu.cn/app) 或 [Lark 开发者后台](https://open.larksuite.com)，创建**企业自建应用**
2. **权限**：开启 `im:message`、`im:message.group_at_msg`、`im:resource`、`contact:user.id:readonly`；**若需机器人读飞书文档**，再开启 `docx:document`；**若需读知识库（Wiki）链接**，再开启 `wiki:wiki` 或 `wiki:wiki.readonly`；**若需机器人直接创建云文档**，需确保应用有云文档创建/写入权限（以飞书/ Lark 文档为准）；**若需识别云文档内截图/内嵌图并送入多模态模型**，再开启 **下载云文档中的图片和附件**（`docs:document.media:download` 等，以开放平台权限名为准）
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
# 若使用网页抓取（在 Lark 发「获取」/「抓取」+ 网址）：需安装 Chromium
# .venv/bin/playwright install chromium
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

### 本地代码助手（Code Agent）

在项目根目录下运行，让 AI 查看和修改本地代码（需已配置 `OPENAI_API_KEY`）。**使用前请确保代码已提交到 Git。**

```bash
# 单条指令
.venv/bin/python code_agent.py "请查看 ./config.py 并告诉我 OPENAI_MODEL 的默认值"

# 交互式（多行输入后 Ctrl+D 结束）
.venv/bin/python code_agent.py
```

工具包括：`read_local_file`、`write_local_file`、`replace_code_block`（精准替换，适合大文件）、`run_command`（执行 shell 命令如 pytest）。工作区目录由 `CODE_WORKSPACE_ROOT` 配置（默认 `mm-admin` 项目路径），可在 `.env` 中修改。

### 生成前端（Lark 需求 + Apifox 接口文档）

在**与 /code 相同的白名单群**内，发送「生成前端」并附带**飞书文档链接**（需求文档），机器人会：

1. 拉取该飞书文档正文作为需求说明  
2. 通过 Apifox 开放 API 导出当前项目的 OpenAPI 规范  
3. 将「需求 + 接口文档」交给代码助手，在工作区中生成或补充路由、菜单、页面

需在 `.env` 配置 `APIFOX_ACCESS_TOKEN`、`APIFOX_PROJECT_ID`（可选 `APIFOX_MODULE_ID`）。示例消息：`生成前端 https://xxx.feishu.cn/docx/xxxxx`。

### Metabase 看板页（/metabase）

在**与 /code 相同的白名单群**内发送命令，由代码助手按 `docs/metabase-add-page.md` 修改 mm-admin（或你的 `CODE_WORKSPACE_ROOT`）：

- `/metabase <slug> <Dashboard数字ID> <现货|合约>` — 例：`/metabase data-hedge-profit 115 合约`
- `/metabase id <Dashboard数字ID> <slug> <现货|合约>` — 例：`/metabase id 115 data-hedge-profit 现货`
- 可选：`标题「侧栏菜单中文名」`（直角引号），指定导航 `title`
- **现货** → 导航项 `showOnlyInSpot: true`；**合约** → `showOnlyInFuture: true`

文档查找顺序：`METABASE_ADD_PAGE_DOC_PATH`（若配置）→ `CODE_WORKSPACE_ROOT/docs/metabase-add-page.md` → 本仓库 `docs/metabase-add-page.md`。

### Apifox 接口列表（/api）

与「生成前端」共用同一套 Apifox 配置。发 **`/api 帮助`** 查看完整用法。典型用法：`/api`（默认/配置的模块）、`/api 关键词`（按 Apifox 目录写入的 tag、路径或摘要筛选）、`/api 123`（数字为项目内模块 ID）。若需在对话里用中文切换模块，可在 `.env` 设置 **`APIFOX_MODULE_MAP`**（JSON，如 `{"做市":123}`）。

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
| `OPENAI_VISION_MODEL` | 否 | 含**图片**时的多模态模型；不填则与 `OPENAI_MODEL` 相同（需 API 支持 vision） |
| `VISION_MULTIMODAL` | 否 | 是否发送含 `image_url` 的多模态请求。留空则**自动**：`OPENAI_API_BASE` 含 `deepseek` 时为 `false`（避免 400）；设为 `true` 时需接口真支持 vision |
| `VISION_MAX_IMAGES` | 否 | 每条消息最多处理几张图，默认 `3` |
| `VISION_MAX_IMAGE_BYTES` | 否 | 单张图最大字节，默认 `4194304`（4MB） |
| `FEISHU_DOC_FETCH_IMAGES` | 否 | 是否拉取 **飞书 docx 内嵌图片** 与正文一并送多模态模型，默认 `true` |
| `DOCX_MAX_IMAGES` | 否 | 每条用户消息从文档中最多拉几张内嵌图（与聊天发图共享 `VISION_MAX_IMAGES` 总上限），默认 `12` |
| `FEISHU_GROUP_ACCESS` | 否 | 群聊模式：`open`（默认）/ `allowlist` / `disabled` |
| `FEISHU_DOMAIN` | 否 | 国际版 Lark 填 `https://open.larksuite.com`，国内飞书默认 `https://open.feishu.cn` |
| `FEISHU_DOC_BASE_URL` | 否 | 创建文档后返回的可点击链接根地址，如 `https://你的企业.larksuite.com`，不设则只返回 document_id |
| `WEBHOOK_PORT` | 否 | Webhook 监听端口，默认 9000 |
| `WEBHOOK_PATH` | 否 | Webhook 路径，默认 `/`（请求地址 = 公网URL + 此路径） |
| `FEISHU_PIPELINE_STAGE_A_CHAT_ID` | 否 | 多群流水线 A 群（需求分析）chat_id，不填则不启用流水线 |
| `FEISHU_PIPELINE_STAGE_B_CHAT_ID` | 否 | 多群流水线 B 群（方案生成）chat_id |
| `FEISHU_PIPELINE_STAGE_C_CHAT_ID` | 否 | 多群流水线 C 群（总结输出）chat_id |
| `FEISHU_CODE_AGENT_CHAT_ID` | 否 | 代码修改（/code）、「生成前端」与 `/metabase` 仅在此群可触发，不填则不在 Lark 开放该功能 |
| `CODE_WORKSPACE_ROOT` | 否 | 代码助手操作目录（读/写/替换/执行命令均基于此目录），不设则使用本项目根目录，默认 `mm-admin` 路径 |
| `METABASE_ADD_PAGE_DOC_PATH` | 否 | `/metabase` 流程文档路径；不设则依次尝试工作区 `docs/metabase-add-page.md`、本仓库 `docs/metabase-add-page.md` |
| `APIFOX_ACCESS_TOKEN` | 生成前端必填 | Apifox 开放 API 系统级访问令牌 |
| `APIFOX_PROJECT_ID` | 生成前端必填 | Apifox 项目 ID（导出 OpenAPI 用） |
| `APIFOX_MODULE_ID` | 否 | Apifox 模块 ID，不填则导出默认模块 |
| `APIFOX_MODULE_MAP` | 否 | JSON，模块别名 → 数字 ID，供 `/api` 按名称切换模块，如 `{"用户":456}` |
| `APIFOX_API_BASE` | 否 | Apifox API 根地址，默认 `https://api.apifox.com` |

## 项目结构

```
lnagchain-lk/
├── main_webhook.py      # Webhook 模式入口（请求地址）
├── main.py              # WebSocket 模式入口（若平台仍支持）
├── handlers.py          # 事件处理（消息解析、LangChain 回复）
├── feishu_doc.py        # 飞书文档/知识库链接解析与正文拉取
├── skills/              # 技能：/btc、/rank、/资金费率、网页抓取等
├── lark_client.py       # 飞书 HTTP 客户端与发送消息
├── langchain_agent.py   # LangChain 对话链（可改为 Agent/RAG）
├── code_agent.py        # 本地代码修改助手（LangGraph ReAct + 文件/命令工具）
├── tools/               # LangChain 工具：资金费率、流动性深度、code_tools、apifox_client（导出 OpenAPI）
├── config.py            # 配置加载与校验
├── requirements.txt
├── .env.example
└── README.md
```

## 扩展建议

- **多轮对话**：按 `chat_id` 维护最近 10 轮历史，默认对话路径会传入 `reply(..., history=...)`；skill 路径不写入历史
- **流式回复**：已实现——先发占位消息，再 `reply_stream` + 飞书「更新消息」接口（见 `handlers.py`、`langchain_agent.reply_stream`、`lark_client.update_text_message`）
- **RAG**：使用 `LarkSuiteDocLoader` 等加载飞书文档，接入当前链
- **Tools**：在 `langchain_agent.py` 中改为 Agent + Tools 即可

## 参考

- [飞书开放平台 - 长连接接收事件](https://open.feishu.cn/document/server-docs/im-v1/message-event/event-list)
- [OpenClaw 飞书集成说明](https://openclaws.io/zh/blog/openclaw-feishu-integration)
- [LangChain 文档](https://python.langchain.com/)
