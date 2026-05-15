# -*- coding: utf-8 -*-
"""
TBOX智能助手Agent
集成RAG问答、文件翻译、普通对话能力，单对话框自然语言交互
依赖tbox_custom_translator.py实现文件翻译
"""
import warnings

warnings.filterwarnings("ignore")  # 屏f蔽新手无关的警告

# 1. 导入LangChain核心模块
import streamlit as st
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents.middleware import SummarizationMiddleware
from langchain.tools import tool, ToolRuntime
from langgraph.types import Command

# 注意：如果你的tools/rag_new/vector_db_new文件路径不对，需自行调整
from tools import create_translate_file_tool, create_rag_qa_tool, create_web_search_tool, create_parse_spi_tool, create_compare_versions_tool  # 导入翻译工具和RAG工具
from rag import build_qa_chain  # 导入RAG问答链函数
from vector_db import get_vector_db  # 导入文档目录配置
from dotenv import load_dotenv
load_dotenv()
import os

MAIN_AGENT_API_KEY = os.getenv("MAIN_AGENT_API_KEY")  # 替换成自己的！
MAIN_AGENT_BASE_URL = os.getenv("MAIN_AGENT_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 替换成自己的！
MAIN_AGENT_LLM_MODEL = os.getenv("MAIN_AGENT_LLM_MODEL") or "qwen3.5-plus" 
# ====================== 第一步：基础配置（大模型/嵌入/文本分割） ======================
# 1. 主线程初始化向量库（有SessionContext，可加st提示）
def init_vector_db_in_main(dashscope_api_key: str):
    """主线程初始化向量库（可加Streamlit提示）"""
    with st.spinner("📦 初始化向量库中..."):
        vector_db = get_vector_db(dashscope_api_key)
        doc_count = len(vector_db.get()['metadatas']) if vector_db else 0
        st.success(f"✅ 向量库初始化完成，当前文档数量：{doc_count}")
    return vector_db

checkpointer = InMemorySaver()

def create_transfer_to_code_tool(code_agent_node_name: str = "code_agent"):
    @tool
    def transfer_to_code_agent(runtime: ToolRuntime) -> Command:
        """将对话交接给代码助手（处理代码编写/执行问题,操作本地文件，回答由代码助手创建或修改的文件相关的问题时）"""
        update_dict = {
            "active_agent": code_agent_node_name,
        }
        return Command(goto=code_agent_node_name, update=update_dict, graph=Command.PARENT)
    return transfer_to_code_agent

# ====================== 创建React Agent（兼容自定义LLM） ======================
def create_tbox_agent(code_agent_node_name: str = "code_agent", dashscope_api_key: str = None, volc_api_key: str = None, workspace_dir: str = None):
    """创建TBOX智能体（改用React Agent，兼容自定义QwenChat）"""
    # 适配create_agent的system_prompt（纯字符串，无动态变量）
    system_prompt = """
    畅星集团（SIS）是一家以车联网、物联网及移动出行服务为核心竞争力的专业国际化公司，主要客户是本田，主要产品是TSU（Telematic System Unit）。
    你是由公司员工seki开发的智能助手，主要职责是翻译文档、回答用户关于公司业务相关的问题、陪用户聊天等，你必须严格遵守以下工具使用规则：

    ### 可用工具列表
    1. rag：仅用于回答公司业务相关问题（如项目体制、详细设计、测试手法等）
       - 参数格式（必须是合法JSON）：{"question":"用户的业务问题"}
    2. 文件翻译：仅用于翻译文件（.pptx、.xlsx等）
       - 参数格式（必须是合法JSON）：{"file_name":"文件名","target_lang":"目标语言"}
    3. web_search：仅用于搜索网络信息
       - 参数格式（必须是合法JSON）：{"query":"搜索查询词"}
    4. transfer_to_code_agent：仅用于将对话交接给代码助手Agent，处理代码相关问题和操作本地文件
       - 无参数
    5. parse_spi_tool：仅用于解析SPI日志或生成SPI报文Excel
    6. compare_versions_tool：仅用于比较两个版本压缩包的差异，返回统一diff文本
       - 参数格式（必须是合法JSON）：{"version_a":"版本A文件名","version_b":"版本B文件名"}

    ### 工具使用强制规则
    1. 只有公司业务问题或者用户明确说明要使用rag，或者用户说要从本地知识库查询时才调用，其他问题绝不调用；
    2. 使用rag工具回答问题需指出信息来源于哪个文件的哪个章节或页码。
    3. 只有明确要求翻译PPT/Excel/Word时才调用对应工具，默认目标语言为日语；
    4. 当用户要求解析 SPI 日志或生成 SPI 报文 Excel 时，调用本地 parse_spi 工具；
    5. 普通问题（如1+1=2）不调用任何工具，直接给出答案；
    6. 工具调用参数必须是合法JSON格式，禁止语法错误；
    7. 工具调用失败时，返回友好提示，不泄露任何技术细节；
    7. 最终回答要简洁、准确，只返回用户需要的结果，不添加额外分析/解释；
    8. 当用户问题需要上网搜索时，必须调用web_search工具，并结合搜索结果给出回答；
    9. 当判断解决用户问题需要编写代码、调试、运行脚本、操作文件等时，必须调用transfer_to_code_agent工具将对话交接给代码助手Agent处理；
    10. 只有当用户明确要求比较两个版本的差异时才调用compare_versions_tool工具，禁止滥用。

    ### 其他规则
    1. 回答语言要和用户问题一致（用户问中文答中文，问日文答日文，问英文答英文）；
    """
    # 步骤1：初始化向量库（主线程执行，有SessionContext）
    vector_db = init_vector_db_in_main(dashscope_api_key)
    # 步骤2：构建RAG Chain（依赖注入：传入vector_db）
    qa_chain = build_qa_chain(vector_db, dashscope_api_key)
    # 步骤3：创建工具（依赖注入：传入qa_chain）
    rag_qa_tool = create_rag_qa_tool(qa_chain)  # RAG工具
    # 创建工具（注入用户密钥和用户翻译目录）
    translate_file_tool = create_translate_file_tool(dashscope_api_key, workspace_dir)
    web_search = create_web_search_tool(volc_api_key)
    parse_spi_tool = create_parse_spi_tool(workspace_dir)
    # 步骤4：构建工具列表
    tools = [translate_file_tool, rag_qa_tool, web_search, parse_spi_tool]
    # 版本比较工具（比较两个上传的版本压缩包）
    compare_tool = create_compare_versions_tool(workspace_dir)
    tools.append(compare_tool)
    # 添加交接工具
    transfer_tool = create_transfer_to_code_tool(code_agent_node_name)
    tools.append(transfer_tool)
    LLM = ChatOpenAI(
        model=MAIN_AGENT_LLM_MODEL,
        temperature=0.1,
        api_key=dashscope_api_key,
        base_url=MAIN_AGENT_BASE_URL,
        timeout=300,
        extra_body={"enable_search": True},
        stream_options={"include_usage": True},
    )

    # 创建Agent
    agent = create_agent(
        model=LLM, 
        tools=tools, 
        middleware=[
        SummarizationMiddleware(
            model=LLM,
            trigger=("tokens", 4000),
            keep=("messages", 20)
            )
        ],
        system_prompt=system_prompt, 
        checkpointer=checkpointer)
    
    return agent