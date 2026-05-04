# code_agent.py
import subprocess
import sys
import os
from langchain.agents import create_agent
from langchain.tools import tool, ToolRuntime
from langchain_openai import ChatOpenAI
from langgraph.types import Command
from langgraph.checkpoint.memory import InMemorySaver
import platform
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path

# 获取当前脚本所在目录的父级目录（即 SIS_Agent 根目录）
SIS_AGENT_ROOT = Path(__file__).parent.parent
# 复用主程序中的 LLM 配置（建议从环境变量读取）
CODE_AGENT_API_KEY = os.getenv("CODE_AGENT_API_KEY")  # 或者从主程序传入
url = os.getenv("CODE_AGENT_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
model = os.getenv("CODE_AGENT_LLM_MODEL") or "qwen3.5-plus"

checkpointer = InMemorySaver()
# ---------- 工具定义 ----------

def create_file_tools(workspace_dir: Path):
    """创建文件操作工具，使用指定的 workspace_dir"""
    
    @tool
    def read_file(file_path: str) -> str:
        """
        读取文本文件内容（限制大小 1MB，路径限制在workspace目录下）。
        
        参数:
        - file_path: 文件路径，必须在workspace目录下
        
        返回: 文件内容或错误信息
        """
        abs_path = Path(file_path).resolve()
        if not str(abs_path).startswith(str(workspace_dir)):
            return f"拒绝读取 {abs_path}：不在允许的目录 {workspace_dir} 下"
        if not abs_path.exists():
            return f"文件不存在: {file_path}"
        if abs_path.stat().st_size > 1024 * 1024:
            return "文件过大（>1MB），拒绝读取"
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"读取失败: {str(e)}"
    
    @tool
    def write_file(file_path: str, content: str) -> str:
        """
        写入文本文件（覆盖模式），路径限制在workspace目录下。
        
        参数:
        - file_path: 文件路径，必须在workspace目录下
        - content: 要写入的文件内容
        
        返回: 成功消息或错误信息
        """
        abs_path = Path(file_path).resolve()
        if not str(abs_path).startswith(str(workspace_dir)):
            return f"拒绝写入 {abs_path}：不在允许的目录 {workspace_dir} 下"
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"成功写入 {file_path}"
        except Exception as e:
            return f"写入失败: {str(e)}"
    
    @tool
    def delete_file(file_path: str) -> str:
        """
        删除文件（仅限文件，不支持目录），路径限制在workspace目录下。
        
        参数:
        - file_path: 文件路径，必须在workspace目录下
        
        返回: 成功消息或错误信息
        """
        abs_path = Path(file_path).resolve()
        if not str(abs_path).startswith(str(workspace_dir)):
            return f"拒绝删除 {abs_path}：不在允许的目录 {workspace_dir} 下"
        if not abs_path.exists():
            return f"文件不存在: {file_path}"
        if not abs_path.is_file():
            return f"路径不是文件: {file_path}（不支持删除目录）"
        try:
            abs_path.unlink()
            return f"成功删除 {file_path}"
        except Exception as e:
            return f"删除失败: {str(e)}"
    
    return read_file, write_file, delete_file

def create_execute_shell_tool(workspace_dir: Path):
    """创建 shell 执行工具，使用指定的 workspace_dir 作为 cwd"""
    
    @tool
    def execute_shell(command: str) -> str:
        """
        执行 Linux 命令，工作目录限制在workspace目录下。
        
        参数:
        - command: 要执行的shell命令
        
        返回: 命令输出或错误信息
        
        注意: 避免危险命令，如rm -rf等。执行Python代码时先写入文件再执行。
        """
        # 危险模式过滤
        dangerous = ["rm -rf", "dd if=", "mkfs", ":(){ :|:& };:"]

        for pat in dangerous:
            if pat in command:
                return f"拒绝执行包含危险模式 '{pat}' 的命令"

        # 如果命令以 "python" 开头，替换为当前 Python 解释器的完整路径（带引号）
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
                shell_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding='utf-8',
                cwd=str(workspace_dir)
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

    return execute_shell
def create_transfer_to_main_tool(main_agent_name: str = "main_agent"):
    @tool
    def transfer_to_main_agent(runtime: ToolRuntime) -> Command:
        """
        将对话交还给主Agent。
        
        当任务不涉及代码编写、调试或文件操作时，使用此工具切换回主Agent。
        """
        update_dict = {
            "active_agent": main_agent_name,
        }
        return Command(goto=main_agent_name, update=update_dict, graph=Command.PARENT)
    return transfer_to_main_agent

# ---------- 创建代码助手 Agent ----------
def create_code_agent(main_agent_node_name: str = "main_agent", dashscope_api_key: str = None, workspace_dir: str = None):
    """返回代码助手 Agent 实例"""
    if workspace_dir is None:
        workspace_dir = SIS_AGENT_ROOT / "workspace"
    else:
        workspace_dir = Path(workspace_dir)
    
    # 创建文件操作工具
    read_file_tool, write_file_tool, delete_file_tool = create_file_tools(workspace_dir)
    
    # 创建 shell 执行工具
    execute_shell_tool = create_execute_shell_tool(workspace_dir)
    
    tools = [execute_shell_tool, read_file_tool, write_file_tool, delete_file_tool]
    # 添加交接工具（动态生成，依赖主 Agent 节点名）
    transfer_tool = create_transfer_to_main_tool(main_agent_node_name)
    tools.append(transfer_tool)

    LLM = ChatOpenAI(
        model=model,
        temperature=0.1,
        api_key=dashscope_api_key,
        base_url=url,
        extra_body={"enable_search": True},
        stream_options={"include_usage": True},
    )
    
    system_prompt = """你是一个代码助手，擅长编写、执行和调试代码。你的工具包括：
- execute_shell: 执行 Linux 命令，工作目录限制在workspace目录下。
- read_file: 读取文件，路径限制在workspace目录下
- write_file: 写入文件，路径限制在workspace目录下
- delete_file: 删除文件，路径限制在workspace目录下
- transfer_to_main_agent: 将对话交还给主 Agent

规则：
1. 当判断解决用户问题需要编写代码、调试、运行脚本、操作文件等时，使用这些工具。
2. 重要！！！execute_shell工具的目录默认为 workspace，如果遇到找不到文件的情况，先用pwd命令确认当前目录，再用ls命令查看文件列表，确保路径正确。
3. 如果用户问题不需要使用这些工具，且问题与你编写的代码和文件等无关时，必须调用 transfer_to_main_agent 工具交还给主 Agent。
4. 只能在workspace目录下操作文件，禁止访问系统敏感路径。
5. 执行命令时注意安全，避免破坏性操作。
6. 任务完成后，清理自己创建的中间文件，只保留最终结果文件。
7. 回答要简洁，直接给出代码或执行结果。
"""
    agent = create_agent(
        model=LLM,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )
    return agent