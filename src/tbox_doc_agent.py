# -*- coding: utf-8 -*-
"""
TBOX智能助手Agent
集成RAG问答、文件翻译、普通对话能力，单对话框自然语言交互
依赖tbox_custom_translator.py实现文件翻译
"""
# 加载.env文件（必须放在代码最开头！）
from dotenv import load_dotenv
load_dotenv()  # 自动读取 .env 文件里的所有环境变量
import time
import warnings

warnings.filterwarnings("ignore")  # 屏蔽新手无关的警告

# ====================== 全局配置（新手只需改这里） ======================
# 1. 自动检测文件变更的间隔（秒，0=关闭自动检测）
AUTO_CHECK_INTERVAL = 10  # 每10秒检查一次，建议新手先设为0（手动更新）
# 2. 大模型配置（请替换为自己的API Key）
DASHSCOPE_API_KEY = "sk-10579025107e412983a48273c2ff7d3f"  # 替换成自己的！

# 1. 导入LangChain核心模块
import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk, ToolMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents.middleware import SummarizationMiddleware
from langsmith import traceable

# 注意：如果你的tools/rag_new/vector_db_new文件路径不对，需自行调整
from tools import translate_excel_tool, translate_ppt_tool, create_rag_qa_tool, web_search  # 导入翻译工具和RAG工具
from rag_new import build_qa_chain  # 导入RAG问答链函数
from vector_db_new import TBOX_DOCS_DIR, diff_update_vector_db, get_local_docs_info, get_vector_db  # 导入文档目录配置

st.set_page_config(
    page_title="畅星TSU开发Agent",
    page_icon="🚗",
    layout="wide"
)
# ====================== 第一步：基础配置（大模型/嵌入/文本分割） ======================
# 1. 通义千问大模型配置
if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY == "sk-10579025107e412983a48273c2ff7d3f":
    st.warning("⚠️ 请替换为自己的DashScope API Key！")
LLM = ChatOpenAI(
    model="qwen3.5-plus",
    temperature=0.1,
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=300,
    extra_body={"enable_search": True}
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
# ====================== 创建React Agent（兼容自定义LLM） ======================
def create_tbox_agent():
    """创建TBOX智能体（改用React Agent，兼容自定义QwenChat）"""
    # 适配create_agent的system_prompt（纯字符串，无动态变量）
    system_prompt = """
    畅星集团（SIS）是一家以车联网、物联网及移动出行服务为核心竞争力的专业国际化公司，主要客户是本田，主要产品是TSU（Telematic System Unit）。
    你是由公司员工seki开发的智能助手，主要职责是翻译文档、回答用户关于公司业务相关的问题、陪用户聊天等，你必须严格遵守以下工具使用规则：

    ### 可用工具列表
    1. rag：仅用于回答公司业务相关问题（如项目体制、详细设计、测试手法等）
       - 参数格式（必须是合法JSON）：{"question":"用户的业务问题"}
    2. ppt翻译：仅用于翻译PPT文件
       - 参数格式（必须是合法JSON）：{"file_name":"文件名.pptx","target_lang":"目标语言"}
    3. excel翻译：仅用于翻译Excel文件
       - 参数格式（必须是合法JSON）：{"file_name":"文件名.xlsx","target_lang":"目标语言"}
    4. web_search：仅用于搜索网络信息
       - 参数格式（必须是合法JSON）：{"query":"搜索查询词"}


    ### 工具使用强制规则
    1. 只有公司业务问题或者用户明确说明要使用rag，或者用户说要从本地知识库查询时才调用，其他问题绝不调用；
    2. 使用rag工具回答问题需指出信息来源于哪个文件的哪个章节或页码。
    3. 只有明确要求翻译PPT/Excel时才调用对应工具，默认目标语言为日语，默认文件目录：D:\\seki\\AI\\copilotTest\\input；
    4. 普通问题（如1+1=2）不调用任何工具，直接给出答案；
    5. 工具调用参数必须是合法JSON格式，禁止语法错误；
    6. 工具调用失败时，返回友好提示，不泄露任何技术细节；
    7. 最终回答要简洁、准确，只返回用户需要的结果，不添加额外分析/解释；
    8. 当用户问题需要上网搜索时，必须调用web_search工具，并结合搜索结果给出回答；

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
    tools = [translate_excel_tool, translate_ppt_tool, rag_qa_tool, web_search]  # 工具列表
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

# ====================== 第六步：Streamlit UI ======================
def main():
    st.title("🚗 畅星TSU开发助手Agent（支持文件翻译、本地知识库查询）")
    st.markdown(f"""
    📂 本地文档目录：{TBOX_DOCS_DIR}
    📌 支持格式：PDF/TXT（中日英）
    🎯 核心能力：本地文档库管理 + 向量库差分更新 + RAG问答 + 文档整体翻译
    """)

    if "last_check_time" not in st.session_state:
        st.session_state.last_check_time = 0
    if "auto_check_lock" not in st.session_state:
        st.session_state.auto_check_lock = False

    # 初始化Agent（会话级缓存）
    if "agent" not in st.session_state:
        st.session_state.agent = create_tbox_agent()
        st.info(f"成功创建TBOX智能助手Agent！")
        print("✅ 成功创建TBOX智能助手Agent！")

    # 初始化聊天历史
    if "messages" not in st.session_state:
        st.session_state.messages = []
        st.info("聊天历史已初始化！")
        print("✅ 聊天历史已初始化！")

    # 显示聊天历史
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    # 侧边栏：本地文档管理 + 更新按钮
    with st.sidebar:
        st.header("📁 本地文档库管理")
        st.info(f"文档目录：{TBOX_DOCS_DIR}")
        
        # 显示本地文档列表
        local_docs = get_local_docs_info()
        if local_docs:
            st.subheader("当前文档列表：")
            for file_name, info in local_docs.items():
                st.write(f"📄 {file_name}（修改时间：{info['mtime_str']}）")
        else:
            st.warning("⚠️ 本地文档目录为空，请放入PDF/TXT文件")
        
        # 手动更新向量库按钮
        if st.button("🔄 手动更新向量库", type="primary"):
            new_vector_db = diff_update_vector_db()
            # 2. 重新构建qa_chain（关键：用最新的向量库）
            new_qa_chain = build_qa_chain(new_vector_db)
            st.session_state.qa_chain = new_qa_chain  # 更新QA链
            st.info("向量库已更新！")
            print("✅ 向量库已更新！")
            st.session_state.agent = create_tbox_agent()  # 创建新的Agent实例
            print("✅ 已创建新的Agent实例，已切换到最新向量库！")
        
        # 自动检测开关（优化防抖）
        if AUTO_CHECK_INTERVAL > 0:
            auto_check = st.checkbox("开启自动检测文件变更", value=False)
            current_time = time.time()
            if auto_check and not st.session_state.auto_check_lock and current_time - st.session_state.last_check_time > AUTO_CHECK_INTERVAL:
                st.session_state.auto_check_lock = True  # 加锁防止重复执行
                st.session_state.last_check_time = current_time
                try:
                    diff_update_vector_db()
                finally:
                    st.session_state.auto_check_lock = False  # 解锁
    
    # 核心：单个对话框
    user_input = st.chat_input("请输入您的指令（例如：把test.pptx翻译成日语、TBOX项目的PM是谁？）")
    if user_input:
        # 记录用户输入
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Agent处理指令
        with st.chat_message("assistant"):
            # 单个占位符，实时更新整个消息内容
            message_placeholder = st.empty()
            # 初始状态，覆盖模型首字延迟
            message_placeholder.markdown("🤔 思考中...")

            # ========== 核心修改1：重构状态变量 ==========
            current_status = "🤔 思考中..."  # 当前动态状态提示
            final_answer = ""               # 最终要显示的回答内容
            is_tool_running = False         # 是否正在执行工具调用
            current_tool_name = ""          # 当前执行的工具名

            def update_display(cursor=True):
                """更新显示：优先显示动态状态，最终只显示回答"""
                display_content = ""
                # 1. 如果工具正在运行，显示工具状态
                if is_tool_running and current_tool_name:
                    display_content = f"🔧 正在调用 {current_tool_name}..."
                # 2. 否则显示思考状态（如果还没生成回答）
                elif not final_answer:
                    display_content = current_status
                # 3. 有回答内容时，显示回答
                else:
                    display_content = final_answer.replace('\n', '<br>')
                
                # 添加光标（仅在未完成时）
                if cursor and (is_tool_running or not final_answer):
                    display_content += '▌'
                
                message_placeholder.markdown(display_content, unsafe_allow_html=True)

            try:
                # 关键：转换为Agent要求的入参格式
                invoke_input = {
                    "messages": [HumanMessage(content=user_input)]
                }
                for mode, data in st.session_state.agent.stream(
                    invoke_input,
                    stream_mode=["messages", "updates"],
                    config = {"configurable": {"thread_id": "1"}}
                ):
                    if mode == "updates":
                        # ========== 核心修改2：修正节点状态判断 ==========
                        for node_name, node_output in data.items():
                            # 情况1：model节点 + 工具调用标记 = 正在调用工具
                            if node_name == "model":
                                try:
                                    # 获取模型返回的元数据：finish_reason
                                    last_msg = node_output["messages"][-1]
                                    finish_reason = last_msg.response_metadata.get("finish_reason", "")
                                    tool_calls = last_msg.tool_calls

                                    # 关键判断：模型决定调用工具
                                    if finish_reason == "tool_calls" and tool_calls:
                                        is_tool_running = True
                                        # 提取工具名
                                        current_tool_name = tool_calls[0]["name"]
                                    else:
                                        is_tool_running = False
                                except:
                                    print("1.无法解析模型输出的工具调用信息，默认不显示工具状态")
                                    is_tool_running = False
                            
                            # 情况2：tools节点 = 工具执行中
                            elif node_name == "tools":
                                is_tool_running = True
                                try:
                                    last_msg = node_output["messages"][-1]
                                    current_tool_name = last_msg.name
                                except:
                                    print("2.无法解析工具节点的工具名，默认显示通用工具状态")
                                    current_tool_name = "工具"
                            elif node_name in ["agent"]:
                                # 模型/思考节点：标记工具已结束，显示思考状态
                                is_tool_running = False
                                current_status = "🤔 正在思考..."
                            else:
                                # 其他节点：通用处理
                                is_tool_running = False
                                current_status = f"⏳ 执行步骤: {node_name}"
                        # 更新显示（工具/思考状态）
                        update_display(cursor=True)

                    elif mode == "messages":
                        message_chunk, metadata = data

                        # 处理AI回答消息（累积最终回答）
                        if isinstance(message_chunk, AIMessageChunk) and metadata.get("langgraph_node") == "model":
                            token = message_chunk.content
                            if token:
                                final_answer += token
                            # 工具已结束，显示回答内容
                            is_tool_running = False
                            update_display(cursor=True)

                # ========== 核心修改4：最终显示（仅保留回答，移除所有状态提示） ==========
                message_placeholder.markdown(final_answer, unsafe_allow_html=True)

                # 存储最终回答到历史
                st.session_state.messages.append({"role": "assistant", "content": final_answer})

            except Exception as e:
                message_placeholder.empty()
                error_msg = f"处理失败：{type(e).__name__} - {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})

# ====================== 运行入口 ======================
if __name__ == "__main__":
    main()