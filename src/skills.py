# 加载.env文件（必须放在代码最开头！）
from dotenv import load_dotenv
load_dotenv()  # 自动读取 .env 文件里的所有环境变量
from langchain_core.utils.uuid import uuid7
from typing import TypedDict, List, Optional
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ModelResponse, AgentMiddleware
from langchain.messages import SystemMessage
from langchain.tools import tool, ToolRuntime
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import BaseMessage, AIMessage
from typing import List, Optional, Any, Dict
from typing import Callable
import yaml
import os
import sys
import subprocess
import platform
from pathlib import Path

# 获取当前脚本所在目录的父级目录（即 SIS_Agent 根目录）
SIS_AGENT_ROOT = Path(__file__).resolve().parent.parent
BASE_SKILL_PATH = SIS_AGENT_ROOT / "skills"
# ===================== 1. 动态加载本地 Skills =====================
class Skill(TypedDict):
    name: str
    description: str
    content: str


def parse_skill_markdown(skill_path: Path) -> dict:
    with open(skill_path, "r", encoding="utf-8") as f:
        content = f.read()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            front_matter = yaml.safe_load(parts[1]) or {}
            return {
                "name": front_matter.get("name", skill_path.stem),
                "description": front_matter.get("description", ""),
                "content": parts[2].strip(),
            }
    name = skill_path.parent.name if skill_path.name.lower() == "skill.md" else skill_path.stem
    return {"name": name, "description": "", "content": content.strip()}


class SkillManager:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills: List[Skill] = []
        self.refresh()

    def _ensure_directory(self) -> None:
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def _discover_skills(self) -> List[Skill]:
        self._ensure_directory()
        skills: List[Skill] = []
        for entry in sorted(self.skills_dir.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir():
                skill_md = entry / "SKILL.md"
                if skill_md.exists():
                    skills.append(Skill(**parse_skill_markdown(skill_md)))
            elif entry.is_file() and entry.suffix.lower() in {".md", ".markdown", ".txt"}:
                skills.append(Skill(**parse_skill_markdown(entry)))
        return skills

    def refresh(self) -> List[Skill]:
        self.skills = self._discover_skills()
        return self.skills

    def find(self, skill_name: str) -> Optional[Skill]:
        target = skill_name.strip().lower()
        for skill in self.skills:
            if skill["name"].strip().lower() == target:
                return skill
        return None

    def list_names(self) -> List[str]:
        return [skill["name"] for skill in self.skills]

    def prompt(self) -> str:
        if not self.skills:
            return "暂无可用技能。"
        return "\n".join(
            [f"- {s['name']}：{s['description']}" if s['description'] else f"- {s['name']}" for s in self.skills]
        )


SKILL_MANAGER = SkillManager(BASE_SKILL_PATH)

# ===================== 2. 技能加载工具 =====================
@tool
def load_skill(skill_name: str) -> str:
    """加载完整的技能说明书，并实时扫描新增技能。"""
    SKILL_MANAGER.refresh()
    skill = SKILL_MANAGER.find(skill_name)
    if skill:
        return f"✅ 已加载技能：{skill_name}\n\n{skill['content']}"
    return f"❌ 未找到技能：{skill_name}。可用技能：{', '.join(SKILL_MANAGER.list_names()) or '暂无技能'}"

@tool
def list_skills() -> str:
    """返回当前目录下可用技能列表。"""
    SKILL_MANAGER.refresh()
    names = SKILL_MANAGER.list_names()
    if not names:
        return "当前没有检测到技能，请将 skill 文件或技能目录放到 SIS_Agent/skills/ 下。"
    return "当前可用技能：" + ", ".join(names)

@tool
def refresh_skills() -> str:
    """重新扫描 skills 目录，立即更新技能列表。"""
    skills = SKILL_MANAGER.refresh()
    if not skills:
        return "已刷新技能库：当前没有检测到技能。"
    return f"已刷新技能库：共 {len(skills)} 个技能，分别为：{', '.join(s['name'] for s in skills)}"

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
            errors='replace',   # 遇到无法解码的字符替换为 �，不抛异常
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
    """写入文本文件（覆盖模式），路径限制在workspace文件夹下。"""
    # 安全限制：只允许写入当前目录或指定安全目录
    safe_dir = str(SIS_AGENT_ROOT / "workspace")
    abs_path = os.path.abspath(file_path)
    if not abs_path.startswith(safe_dir):
        return f"拒绝写入 {abs_path}：不在允许的目录 {safe_dir} 下"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"成功写入 {file_path}"
    except Exception as e:
        return f"写入文件失败: {e}"

# ===================== 4. 技能中间件（集成所有工具） =====================
class SkillMiddleware(AgentMiddleware):
    # 🔥 所有工具注册在这里：加载技能 + 执行脚本 + 读写文件
    tools = [load_skill, list_skills, refresh_skills, execute_shell, read_file, write_file]

    def __init__(self):
        SKILL_MANAGER.refresh()
        self.skills_prompt = SKILL_MANAGER.prompt()

    def wrap_model_call(self, request: ModelRequest, handler: Callable) -> ModelResponse:
        addendum = f"""
# 系统规则（严格遵守）
1. 先使用 load_skill 加载对应技能说明书
2. 严格按照说明书步骤执行
3. execute_shell工具只能执行Linux命令，命令必须符合Linux命令规范。
4. 只能在workspace文件夹下写入文件

## 可用技能
{self.skills_prompt}
"""
        new_msg = SystemMessage(content=list(request.system_message.content_blocks) + [{"text": addendum, "type": "text"}])
        return handler(request.override(system_message=new_msg))

# ===================== 5. 初始化 Agent =====================
from langchain_openai import ChatOpenAI

# class OpenCodeZenChatOpenAI(ChatOpenAI):
#     """
#     增强版自定义 ChatOpenAI 子类，专门解决 OpenCode Zen + DeepSeek V4 的 reasoning_content 回传问题
#     完美支持 Agent 工具调用场景
#     """
#     def _create_message_dicts(
#         self, messages: List[BaseMessage], stop: Optional[List[str]]
#     ) -> List[Dict[str, Any]]:
#         dicts = super()._create_message_dicts(messages, stop)
        
#         # 遍历所有消息，检查并回传 reasoning_content
#         for i, msg in enumerate(messages):
#             # 处理普通 AI 消息
#             if isinstance(msg, AIMessage) and hasattr(msg, 'additional_kwargs'):
#                 if 'reasoning_content' in msg.additional_kwargs:
#                     dicts[i]['reasoning_content'] = msg.additional_kwargs['reasoning_content']
            
#             # 处理工具调用后的 AI 消息（关键！Agent 场景下主要是这种情况）
#             elif isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls'):
#                 if hasattr(msg, 'additional_kwargs') and 'reasoning_content' in msg.additional_kwargs:
#                     dicts[i]['reasoning_content'] = msg.additional_kwargs['reasoning_content']
        
#         return dicts

# # 使用增强版类初始化，并添加关键参数
# LLM = OpenCodeZenChatOpenAI(
#     model="big-pickle",
#     temperature=0.1,
#     api_key="sk-G7XhP7rgwqBckkFBdDEH64IXapISrFTVYL72u9UrzLx6cFneQQ44kiaKxWaWSJvW",
#     base_url="https://opencode.ai/zen/v1",
#     # 显式指定思考模式参数（与DeepSeek V4协议兼容）
#     extra_body={
#         "thinking": {"type": "enabled"},
#         "reasoning_effort": "medium"  # 可选：low/medium/high/max
#     }
# )
# BigPickle调用配置（完全免费，200K上下文）
LLM = ChatOpenAI(
    model="big-pickle",  # 模型ID
    temperature=0.1,
    api_key="sk-G7XhP7rgwqBckkFBdDEH64IXapISrFTVYL72u9UrzLx6cFneQQ44kiaKxWaWSJvW",  # 免费获取
    base_url="https://opencode.ai/zen/v1",  # OpenCode Zen兼容接口
    extra_body={"thinking": {"type": "disabled"}}
    # 无需AK/SK，无需担心额度，编程Agent专用模型
)

agent = create_agent(
    model=LLM,
    system_prompt="""
    畅星集团（SIS）是一家以车联网、物联网及移动出行服务为核心竞争力的专业国际化公司，主要客户是本田，主要产品是TSU（Telematic System Unit）。
    你是公司的PPT报告制作专家，擅长使用Python脚本处理和生成PPTX文件。公司业务面向的主要是日本车企，尤其是本田，因此你需要熟悉日语和日本文化，能够制作符合日本客户审美和习惯的PPT报告。
    每当我们的TSU产品出现bug时，项目组会提供一个包含bug描述、复现步骤和相关日志分析等内容的文本文件或ppt文件，放在skills/pptx/ref/目录下。
    你的任务是根据这些文件，以及放在skills/pptx/tmp/目录下的以前报告过的模板文件（如有），自动生成一份结构清晰、内容翔实的PPT报告，我们会将报告提交给客户。
    但是以前报告过的模板文件仅供参考PPT格式、报告风格等，生成的报告的页数、内容等应该主要参照skills/pptx/ref/下的参考文档，无需和模板文件一模一样。
    注意：如果任务是生成新的bug报告，你需要阅读skills/pptx/ref/目录下的所有文件，不要遗漏。
    """,
    middleware=[SkillMiddleware()],
    checkpointer=InMemorySaver(),
)

# ===================== 测试 =====================
if __name__ == "__main__":
    # 旧的 agent 调用已关闭，现在仅做技能刷新测试。
    config = {"configurable": {"thread_id": str(uuid7())},"recursion_limit": 50}  # 限制最大循环步数，防止无限调用工具
    result = agent.invoke({
        "messages": [{"role": "user", "content": "根据参考资料和模板文件，帮我生成一份问题报告的PPT。要中文版的报告，内容要翔实，结构要清晰。"}]
    }, config)
    for msg in result["messages"]:
        msg.pretty_print()