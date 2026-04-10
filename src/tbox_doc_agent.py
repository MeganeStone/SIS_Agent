# -*- coding: utf-8 -*-
"""
TBOX智能助手Agent
集成RAG问答、文件翻译、普通对话能力，单对话框自然语言交互
依赖tbox_custom_translator.py实现文件翻译
"""
import warnings

warnings.filterwarnings("ignore")  # 屏蔽新手无关的警告

# 2. 大模型配置（请替换为自己的API Key）
DASHSCOPE_API_KEY = "sk-10579025107e412983a48273c2ff7d3f"  # 替换成自己的！

# 1. 导入LangChain核心模块
import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, AIMessageChunk, ToolMessage
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents.middleware import SummarizationMiddleware
from langchain.tools import tool, ToolRuntime
from langgraph.types import Command

# 注意：如果你的tools/rag_new/vector_db_new文件路径不对，需自行调整
from tools import translate_file_tool, create_rag_qa_tool, web_search  # 导入翻译工具和RAG工具
from rag_new import build_qa_chain  # 导入RAG问答链函数
from vector_db_new import get_vector_db  # 导入文档目录配置

# ====================== 第一步：基础配置（大模型/嵌入/文本分割） ======================
LLM = ChatOpenAI(
    model="qwen3.5-plus",
    temperature=0.1,
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=300,
    extra_body={"enable_search": True},
    stream_options={"include_usage": True},
)

# 1. 主线程初始化向量库（有SessionContext，可加st提示）
def init_vector_db_in_main():
    """主线程初始化向量库（可加Streamlit提示）"""
    with st.spinner("📦 初始化向量库中..."):
        vector_db = get_vector_db()
        doc_count = len(vector_db.get()['metadatas']) if vector_db else 0
        st.success(f"✅ 向量库初始化完成，当前文档数量：{doc_count}")
    return vector_db

checkpointer = InMemorySaver()

def create_transfer_to_code_tool(code_agent_node_name: str = "code_agent"):
    @tool
    def transfer_to_code_agent(runtime: ToolRuntime) -> Command:
        """将对话交接给代码助手（处理代码编写/执行问题,操作本地文件，回答由代码助手创建或修改的文件相关的问题时）"""
        # state = runtime.state
        # messages = state["messages"]
        # last_ai = next((msg for msg in reversed(messages) if isinstance(msg, AIMessage)), None)
        # transfer_msg = ToolMessage(
        #     content="已从主 Agent 交接给代码助手",
        #     tool_call_id=runtime.tool_call_id,
        # )
        update_dict = {
            "active_agent": code_agent_node_name,
            # "messages": [last_ai, transfer_msg] if last_ai else [transfer_msg],
        }
        return Command(goto=code_agent_node_name, update=update_dict, graph=Command.PARENT)
    return transfer_to_code_agent

# ====================== 创建React Agent（兼容自定义LLM） ======================
def create_tbox_agent(code_agent_node_name: str = "code_agent"):
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


    ### 工具使用强制规则
    1. 只有公司业务问题或者用户明确说明要使用rag，或者用户说要从本地知识库查询时才调用，其他问题绝不调用；
    2. 使用rag工具回答问题需指出信息来源于哪个文件的哪个章节或页码。
    3. 只有明确要求翻译PPT/Excel时才调用对应工具，默认目标语言为日语，默认文件目录：D:\\seki\\AI\\copilotTest\\input；
    4. 普通问题（如1+1=2）不调用任何工具，直接给出答案；
    5. 工具调用参数必须是合法JSON格式，禁止语法错误；
    6. 工具调用失败时，返回友好提示，不泄露任何技术细节；
    7. 最终回答要简洁、准确，只返回用户需要的结果，不添加额外分析/解释；
    8. 当用户问题需要上网搜索时，必须调用web_search工具，并结合搜索结果给出回答；
    9. 当判断解决用户问题需要编写代码、调试、运行脚本、操作文件等时，必须调用transfer_to_code_agent工具将对话交接给代码助手Agent处理；

    ### 其他规则
    1. 回答语言要和用户问题一致（用户问中文答中文，问日文答日文，问英文答英文）；
    """
    # 步骤1：初始化向量库（主线程执行，有SessionContext）
    vector_db = init_vector_db_in_main()
    # 步骤2：构建RAG Chain（依赖注入：传入vector_db）
    qa_chain = build_qa_chain(vector_db)
    # 步骤3：创建工具（依赖注入：传入qa_chain）
    rag_qa_tool = create_rag_qa_tool(qa_chain)  # RAG工具
    # 步骤4：构建工具列表
    tools = [translate_file_tool, rag_qa_tool, web_search]  # 工具列表
    # 添加交接工具
    transfer_tool = create_transfer_to_code_tool(code_agent_node_name)
    tools.append(transfer_tool)
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