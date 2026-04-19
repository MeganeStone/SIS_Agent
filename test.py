# 加载.env文件（必须放在代码最开头！）
from dotenv import load_dotenv
load_dotenv()  # 自动读取 .env 文件里的所有环境变量
from langchain_core.utils.uuid import uuid7
from typing import TypedDict, List
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ModelResponse, AgentMiddleware
from langchain.messages import SystemMessage
from langchain.tools import tool, ToolRuntime
from langgraph.checkpoint.memory import InMemorySaver
from typing import Callable
import yaml
import os
import sys
import subprocess
import base64

BASE_SKILL_PATH = r"D:\AI\SIS agent\skills"
# ===================== 1. 动态加载本地 Skills =====================
SKILLS_FOLDER = "skills"
class Skill(TypedDict):
    name: str
    description: str
    content: str

def parse_skill_markdown(skill_path: str) -> dict:
    with open(skill_path, "r", encoding="utf-8") as f:
        content = f.read()
    if content.startswith("---"):
        parts = content.split("---", 2)
        front_matter = yaml.safe_load(parts[1])
        return {"name": front_matter["name"], "description": front_matter["description"], "content": parts[2].strip()}
    return {"name": os.path.basename(os.path.dirname(skill_path)), "description": "", "content": content}

def load_local_skills() -> List[Skill]:
    skills = []
    if not os.path.exists(SKILLS_FOLDER): os.makedirs(SKILLS_FOLDER)
    for skill_dir in os.listdir(SKILLS_FOLDER):
        dir_path = os.path.join(SKILLS_FOLDER, skill_dir)
        if not os.path.isdir(dir_path): continue
        skill_md = os.path.join(dir_path, "SKILL.md")
        if os.path.exists(skill_md):
            skills.append(Skill(**parse_skill_markdown(skill_md)))
    return skills

SKILLS = load_local_skills()

# ===================== 2. 技能加载工具 =====================
@tool
def load_skill(skill_name: str) -> str:
    """加载完整的技能说明书"""
    for skill in SKILLS:
        if skill["name"] == skill_name:
            target_dir = os.path.join(BASE_SKILL_PATH, skill_name)
            os.chdir(target_dir)
            return f"（✅ 已加载技能：{skill_name}\n\n{skill['content']}"
    return f"❌ 未找到技能，可用：{', '.join(s['name'] for s in SKILLS)}"

# @tool
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

    try:
        result = subprocess.run(
            # Windows + WSL 下执行 Linux 命令
            ["wsl", "bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=30,
            encoding='utf-8', 
            # 关键：Windows路径 D:\xxx 会自动被 WSL 识别为 /mnt/d/xxx
            cwd=os.getcwd()
        )
        print(f"命令执行完成，返回码: {result}")  # 调试用，实际部署时可以注释掉
        output = ""
        if result.stdout:
            output += f"[标准输出]\n{result.stdout}\n"
        if result.stderr:
            output += f"[错误输出]\n{result.stderr}\n"
        print(f"执行命令: {command}\n{output}")  # 调试用，实际部署时可以注释掉
        return output.strip() if output else "[命令执行完成，无任何输出]"

    except subprocess.TimeoutExpired:
        return "[错误] 命令超时（30秒）"
    except Exception as e:
        return f"[执行异常] {str(e)}"

@tool
def read_file(file_path: str) -> str:
    """读取文本文件内容（限制大小 5MB）。"""
    if not os.path.exists(file_path):
        return f"文件不存在: {file_path}"
    if os.path.getsize(file_path) > 5 * 1024 * 1024:
        return "文件过大（>5MB），拒绝读取"
    encodings = ['utf-8', 'gbk', 'utf-16', 'utf-16-le', 'utf-16-be', 'latin-1']
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
            # 成功读取后返回内容
            return content
        except (UnicodeDecodeError, UnicodeError):
            continue
    # 所有编码都失败，返回错误信息
    return f"无法解码文件 {file_path}，尝试的编码: {', '.join(encodings)}"

@tool
def write_file(file_path: str, content: str) -> str:
    """写入文本文件（覆盖模式），路径限制在当前工作目录下。"""
    # 安全限制：只允许写入当前目录或指定安全目录
    safe_dir = os.getcwd()
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
    tools = [load_skill, execute_shell, read_file, write_file]

    def __init__(self):
        self.skills_prompt = "\n".join([f"- {s['name']}：{s['description']}" for s in SKILLS])

    def wrap_model_call(self, request: ModelRequest, handler: Callable) -> ModelResponse:
        addendum = f"""
# 系统规则（严格遵守）
1. 先使用 load_skill 加载对应技能说明书
2. 严格按照说明书步骤执行
3. execute_shell工具只能执行Linux命令，命令必须符合Linux命令规范。
4. 用 execute_shell 运行 ooxml/scripts/ 下的 Python 脚本
5. 用 read_file / write_file 管理 JSON 配置文件
6. 如需执行单行python代码，直接使用execute_shell工具执行。
7. 如需执行多行python代码，必须先使用write_file工具将代码写进本地临时文件（自己创建），再使用execute_shell运行Python文件。

## 可用技能
{self.skills_prompt}
"""
        new_msg = SystemMessage(content=list(request.system_message.content_blocks) + [{"text": addendum, "type": "text"}])
        return handler(request.override(system_message=new_msg))

# ===================== 5. 初始化 Agent =====================
from langchain_openai import ChatOpenAI
DASHSCOPE_API_KEY = "ark-c7bd0c7a-c998-40c5-ae70-ada86b27fec8-c2dfa"  # 或者从主程序传入

# BigPickle调用配置（完全免费，200K上下文）
LLM = ChatOpenAI(
    model="big-pickle",  # 模型ID
    temperature=0.1,
    api_key="sk-G7XhP7rgwqBckkFBdDEH64IXapISrFTVYL72u9UrzLx6cFneQQ44kiaKxWaWSJvW",  # 免费获取
    base_url="https://opencode.ai/zen/v1",  # OpenCode Zen兼容接口
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
    config = {"configurable": {"thread_id": str(uuid7())},"recursion_limit": 20}  # 限制最大循环步数，防止无限调用工具
    result = agent.invoke({
        "messages": [{"role": "user", "content": "根据skills/pptx/ref/目录下的参考资料和skills/pptx/tmp/目录下的模板文件，帮我生成一份问题报告的PPT。要中文版的报告，内容要翔实，结构要清晰。"}]
    }, config)
    for msg in result["messages"]:
        msg.pretty_print()
    # param_str = {'command': 'cd D:\\\\AI\\\\SIS agent\\\\skills\\\\pptx && dir ref'}
    # actual_command = param_str['command']
    # response = execute_shell(actual_command)
    # print(f"响应: {response}")