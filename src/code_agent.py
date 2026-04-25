# code_agent.py
import subprocess
import sys
import os
from langchain.agents import create_agent
from langchain.tools import tool, ToolRuntime
from langchain_openai import ChatOpenAI
from langgraph.types import Command
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import AIMessage, ToolMessage
import platform

# 复用主程序中的 LLM 配置（建议从环境变量读取）
DASHSCOPE_API_KEY = "sk-10579025107e412983a48273c2ff7d3f"  # 或者从主程序传入

LLM = ChatOpenAI(
    model="qwen3.5-plus",
    temperature=0.1,
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    extra_body={"enable_search": True},
    stream_options={"include_usage": True},
)
checkpointer = InMemorySaver()
# ---------- 工具定义 ----------
@tool
def execute_shell(command: str) -> str:
    """
    执行 Linux 命令。
    如需执行多行python代码，必须先调用write_file工具将代码写进临时文件，再执行文件。
    返回 stdout 和 stderr，完整输出所有信息。
    """
    # 危险模式过滤
    dangerous = ["rm -rf", "dd if=", "mkfs", ":(){ :|:& };:"]
    for pat in dangerous:
        if pat in command:
            return f"拒绝执行包含危险模式 '{pat}' 的命令"

    # 如果命令以 "python" 开头，替换为当前 Python 解释器的完整路径（带引号）
    # 注意：在 PowerShell 中，直接替换成带引号的路径是安全的
    if command.strip().startswith("python"):
        command = command.replace("python", f'"{sys.executable}"', 1)

    if platform.system() == "Windows":
        # Windows 下继续用 wsl（如果你需要 Linux 命令）
        shell_cmd = ["wsl", "bash", "-c", command]
    else:
        # Linux / macOS 直接运行 bash
        shell_cmd = ["bash", "-c", command]

    try:
        result = subprocess.run(
            # Windows + WSL 下执行 Linux 命令
            shell_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding='utf-8', 
            # 关键：Windows路径 D:\xxx 会自动被 WSL 识别为 /mnt/d/xxx
            cwd=os.getcwd()
        )
        output = ""
        if result.stdout:
            output += f"[标准输出]\n{result.stdout}\n"
        if result.stderr:
            output += f"[错误输出]\n{result.stderr}\n"
        return output.strip() if output else "[命令执行完成，无任何输出]"

    except subprocess.TimeoutExpired:
        return "[错误] 命令超时（30秒）"
    except Exception as e:
        return f"[执行异常] {str(e)}"

@tool
def read_file(file_path: str) -> str:
    """读取文本文件内容（限制大小 1MB）。"""
    if not os.path.exists(file_path):
        return f"文件不存在: {file_path}"
    if os.path.getsize(file_path) > 1024 * 1024:
        return "文件过大（>1MB），拒绝读取"
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

@tool
def write_file(file_path: str, content: str) -> str:
    """写入文本文件（覆盖模式），路径限制在当前工作目录下。"""
    # 安全限制：只允许写入当前目录或指定安全目录
    safe_dir = os.getcwd()
    abs_path = os.path.abspath(file_path)
    if not abs_path.startswith(safe_dir):
        return f"拒绝写入 {abs_path}：不在允许的目录 {safe_dir} 下"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"成功写入 {file_path}"

# ---------- 交接工具 ----------
def create_transfer_to_main_tool(main_agent_name: str = "main_agent"):
    @tool
    def transfer_to_main_agent(runtime: ToolRuntime) -> Command:
        """将对话交还给主 Agent"""
        update_dict = {
            "active_agent": main_agent_name,
        }
        return Command(goto=main_agent_name, update=update_dict, graph=Command.PARENT)
    return transfer_to_main_agent

# ---------- 创建代码助手 Agent ----------
def create_code_agent(main_agent_node_name: str = "main_agent"):
    """返回代码助手 Agent 实例"""
    tools = [execute_shell, read_file, write_file]
    # 添加交接工具（动态生成，依赖主 Agent 节点名）
    transfer_tool = create_transfer_to_main_tool(main_agent_node_name)
    tools.append(transfer_tool)
    
    system_prompt = """你是一个代码助手，擅长编写、执行和调试代码。你的工具包括：
- execute_shell: 执行 Linux 命令
- read_file: 读取文件
- write_file: 写入文件

规则：
1. 当判断解决用户问题需要编写代码、调试、运行脚本、操作文件等时，使用这些工具。
2. 如果用户问题不需要使用这些工具，且问题与你编写的代码和文件等无关时，必须调用 transfer_to_main_agent 工具交还给主 Agent。
3. 只能在当前工作目录下操作文件，禁止访问系统敏感路径。
4. 执行命令时注意安全，避免破坏性操作。
5. 回答要简洁，直接给出代码或执行结果。
"""
    agent = create_agent(
        model=LLM,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )
    return agent