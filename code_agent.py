"""
本地代码修改助手：基于 LangGraph ReAct 智能体，将 read/write/replace/run_command 等工具交给大模型，
实现类似 OpenClaude / Cline 的本地代码查看与修改能力。

使用前请确保代码已提交到 Git，避免误覆盖造成损失。
运行方式：
  python code_agent.py "请查看 ./hello.py 并添加一个斐波那契函数"
  python code_agent.py   # 进入交互式，输入多行后 Ctrl+D 结束
"""
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from config import CODE_WORKSPACE_ROOT, OPENAI_API_BASE, OPENAI_API_KEY, OPENAI_MODEL
from tools.code_tools import get_code_tools

# 工作区根目录（与 code_tools 一致，用于系统提示）
WORKSPACE_ROOT = Path(CODE_WORKSPACE_ROOT) if CODE_WORKSPACE_ROOT else Path(__file__).resolve().parent

CODE_AGENT_SYSTEM = f"""你是一个高级本地 AI 编程助手，工作区根目录为：{WORKSPACE_ROOT}。

你的任务是帮助用户查看和修改本地项目代码。请遵守以下原则：

1. **先读后写**：修改任何文件前，务必先用 read_local_file 查看当前完整内容，再决定如何修改。
2. **小范围用 replace_code_block**：当文件较大或只需改几行时，优先使用 replace_code_block(old_text, new_text) 做精准替换，不要用 write_local_file 全量覆盖，以免遗漏逻辑或浪费 token。
3. **全量用 write_local_file**：仅当文件很小、或新增文件、或用户明确要求整体重写时，才使用 write_local_file，且必须输出完整、可运行的代码，不要遗漏已有重要逻辑。
4. **验证可运行**：若用户要求「改完跑一下测试」或类似，在修改后用 run_command 执行相应命令（如 pytest、python xxx.py、npm run test），并根据输出判断是否成功。
5. **路径**：用户提到的相对路径（如 ./hello.py、src/main.py）均相对于上述工作区根目录；你传给工具的 file_path 使用相对路径即可。

请用中文与用户交流，执行工具时传入的参数保持准确、完整。"""


def _get_agent():
    llm = ChatOpenAI(
        model=OPENAI_MODEL or "gpt-4o-mini",
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_API_BASE or None,
        temperature=0,
    )
    tools = get_code_tools()
    return create_react_agent(
        llm,
        tools=tools,
        prompt=CODE_AGENT_SYSTEM,
    )


def run(user_input: str) -> str:
    """
    运行代码修改智能体并返回最终 AI 回复文本（不打印过程）。
    供 Lark skill 等外部调用。
    """
    agent = _get_agent()
    final_content = ""
    for event in agent.stream(
        {"messages": [HumanMessage(content=user_input)]},
        stream_mode="values",
    ):
        messages = event.get("messages") or []
        if not messages:
            continue
        latest = messages[-1]
        if getattr(latest, "type", None) == "ai" and getattr(latest, "content", None) and latest.content:
            final_content = latest.content
    return final_content.strip()


def _run_stream(user_input: str) -> str:
    """运行智能体并流式打印过程，返回最终 AI 回复文本。"""
    agent = _get_agent()
    final_content = ""

    for event in agent.stream(
        {"messages": [HumanMessage(content=user_input)]},
        stream_mode="values",
    ):
        messages = event.get("messages") or []
        if not messages:
            continue
        latest = messages[-1]

        if getattr(latest, "type", None) == "ai":
            if getattr(latest, "tool_calls", None):
                for tc in latest.tool_calls:
                    name = tc.get("name") or getattr(tc, "name", "")
                    args = tc.get("args") or getattr(tc, "args", {})
                    print(f"🛠️  AI 调用工具: {name}")
                    print(f"    参数: {args}")
            if getattr(latest, "content", None) and latest.content:
                print(f"🤖 AI: {latest.content}")
                final_content = latest.content

        elif getattr(latest, "type", None) == "tool":
            content = getattr(latest, "content", "") or ""
            preview = (content[:200] + "…") if len(content) > 200 else content
            print(f"✅ 工具返回: {preview}")

    return final_content.strip()


def main():
    if not OPENAI_API_KEY:
        print("请设置 OPENAI_API_KEY（.env 或环境变量）后再运行。", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
    else:
        print("本地代码助手已启动，输入你的需求（多行以 Ctrl+D 结束）：")
        user_input = sys.stdin.read().strip()

    if not user_input:
        print("未输入任何内容，退出。", file=sys.stderr)
        sys.exit(0)

    print(f"用户请求: {user_input}\n" + "-" * 40)
    _run_stream(user_input)


if __name__ == "__main__":
    main()
