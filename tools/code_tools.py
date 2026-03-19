"""
本地代码助手工具：读文件、写文件、精准替换代码块、执行命令。
供 LangGraph ReAct 智能体调用，实现类似 Cline/Aider 的本地代码修改能力。
"""
import os
import subprocess
from pathlib import Path

from langchain_core.tools import tool

# 工作区根目录：优先 config.CODE_WORKSPACE_ROOT，否则环境变量，否则本项目根
def _get_workspace_root() -> str:
    try:
        from config import CODE_WORKSPACE_ROOT
        if CODE_WORKSPACE_ROOT:
            return CODE_WORKSPACE_ROOT.strip()
    except Exception:
        pass
    return os.environ.get("CODE_WORKSPACE_ROOT") or str(Path(__file__).resolve().parent.parent)


_CODE_WORKSPACE_ROOT = _get_workspace_root()


def _resolve_path(file_path: str) -> Path:
    """将用户提供的路径解析为绝对路径，相对路径基于工作区根目录。"""
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = Path(_CODE_WORKSPACE_ROOT) / p
    return p.resolve()


@tool
def read_local_file(file_path: str) -> str:
    """读取本地项目中的文件内容。可传入绝对路径或相对于项目根目录的相对路径（如 ./src/main.py）。"""
    try:
        path = _resolve_path(file_path)
        if not path.exists():
            return f"Error: 文件 {path} 不存在。"
        if not path.is_file():
            return f"Error: {path} 不是文件。"
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"读取文件失败: {str(e)}"


@tool
def write_local_file(file_path: str, content: str) -> str:
    """
    将内容写入本地文件；若文件已存在则覆盖。
    请确保传入的是完整、可运行的代码，不要遗漏文件中已有的重要逻辑。适用于新文件或小文件整体替换。
    """
    try:
        path = _resolve_path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Success: 已成功将内容写入 {path}"
    except Exception as e:
        return f"写入文件失败: {str(e)}"


@tool
def replace_code_block(file_path: str, old_text: str, new_text: str) -> str:
    """
    在指定文件中精准替换一段代码：找到 old_text 的第一次出现并替换为 new_text。
    适用于大文件的小范围修改，避免全量覆盖。old_text 必须与文件中内容完全一致（包括缩进和换行）。
    若文件中存在多处相同片段，只会替换第一处；若未找到 old_text 则返回错误。
    """
    try:
        path = _resolve_path(file_path)
        if not path.exists() or not path.is_file():
            return f"Error: 文件 {path} 不存在或不是文件。"
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            full = f.read()
        if old_text not in full:
            return (
                f"Error: 在文件中未找到要替换的片段。请确保 old_text 与文件内容完全一致（包括缩进、换行）。"
                f" 你可以先用 read_local_file 查看文件内容，再复制要替换的整段作为 old_text。"
            )
        new_full = full.replace(old_text, new_text, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_full)
        return f"Success: 已在 {path} 中完成一次精准替换。"
    except Exception as e:
        return f"替换失败: {str(e)}"


@tool
def run_command(command: str, cwd: str | None = None) -> str:
    """
    在项目目录（或指定的 cwd）下执行一条 shell 命令，如 python xxx.py、npm run test、pytest 等。
    command 为完整命令字符串；cwd 为可选的工作目录，不传则使用项目根目录。
    执行超时 60 秒；返回标准输出与标准错误的合并结果。
    """
    try:
        work_dir = _resolve_path(cwd) if cwd else Path(_CODE_WORKSPACE_ROOT)
        if not work_dir.is_dir():
            return f"Error: 工作目录不存在: {work_dir}"
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = "\n".join(filter(None, [out, err])) or "(无输出)"
        if result.returncode != 0:
            return f"命令退出码: {result.returncode}\n{combined}"
        return combined
    except subprocess.TimeoutExpired:
        return "Error: 命令执行超时（60 秒）。"
    except Exception as e:
        return f"执行失败: {str(e)}"


def get_code_tools():
    """返回供智能体使用的本地代码工具列表。"""
    return [read_local_file, write_local_file, replace_code_block, run_command]
