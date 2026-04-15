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
import subprocess

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
            return f"✅ 已加载技能：{skill_name}\n\n{skill['content']}"
    return f"❌ 未找到技能，可用：{', '.join(s['name'] for s in SKILLS)}"

@tool
def execute_shell(command: str) -> str:
    """
    执行 Shell 命令（危险！建议限制白名单或沙箱）。
    返回 stdout 和 stderr，完整输出所有信息，不隐藏错误。
    """
    # ⚠️ 安全警告：这里应该做严格的命令过滤，禁止 rm -rf / 等
    dangerous = ["rm -rf", "dd if=", "mkfs", ":(){ :|:& };:"]
    for pat in dangerous:
        if pat in command:
            return f"拒绝执行包含危险模式 '{pat}' 的命令"
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            # 固定为你的项目根目录！关键配置
            cwd=r"D:\seki\AI\Langchain\SIS_Agent"
        )
        
        # 🔥 核心修改：拼接所有输出，不省略、不替换
        output = ""
        if result.stdout:
            output += f"[标准输出]\n{result.stdout}\n"
        if result.stderr:
            output += f"[错误输出]\n{result.stderr}\n"
        
        # 如果完全无输出，才返回提示
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
3. 用 execute_shell 运行 ooxml/scripts/ 下的 Python 脚本
4. 用 read_file / write_file 管理 JSON 配置文件
5. 所有文件路径都基于项目根目录

## 可用技能
{self.skills_prompt}
"""
        new_msg = SystemMessage(content=list(request.system_message.content_blocks) + [{"text": addendum, "type": "text"}])
        return handler(request.override(system_message=new_msg))

# ===================== 5. 初始化 Agent =====================
from langchain_openai import ChatOpenAI
DASHSCOPE_API_KEY = "sk-10579025107e412983a48273c2ff7d3f"  # 或者从主程序传入

LLM = ChatOpenAI(
    model="qwen3.5-plus",
    temperature=0.1,
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    extra_body={"enable_search": True},
)

agent = create_agent(
    model=LLM,
    system_prompt="你是PPT自动化助手，严格按照技能说明书执行操作",
    middleware=[SkillMiddleware()],
    checkpointer=InMemorySaver(),
)

# ===================== 测试 =====================
if __name__ == "__main__":
    config = {"configurable": {"thread_id": str(uuid7())}}
    result = agent.invoke({
        "messages": [{"role": "user", "content": "帮我生成一份介绍Anthropic的PPT"}]
    }, config)
    for msg in result["messages"]:
        msg.pretty_print()