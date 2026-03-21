# -*- coding: utf-8 -*-
"""
TBOX智能助手Agent
集成RAG问答、文件翻译、普通对话能力，单对话框自然语言交互
依赖tbox_custom_translator.py实现文件翻译
"""

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

from tools import translate_excel_tool, translate_ppt_tool, create_rag_qa_tool  # 导入翻译工具和RAG工具
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

    ### 工具使用强制规则
    1. 只有公司业务问题或者用户明确说明要使用rag，或者用户说要从本地知识库查询时才调用，其他问题绝不调用；
    2. 使用rag工具回答问题需指出信息来源于哪个文件的哪个章节或页码。
    3. 只有明确要求翻译PPT/Excel时才调用对应工具，默认目标语言为日语，默认文件目录：D:\\seki\\AI\\copilotTest\\input；
    4. 普通问题（如1+1=2）不调用任何工具，直接给出答案；
    5. 工具调用参数必须是合法JSON格式，禁止语法错误；
    6. 工具调用失败时，返回友好提示，不泄露任何技术细节；
    7. 最终回答要简洁、准确，只返回用户需要的结果，不添加额外分析/解释；

    ### 其他规则
    1. 回答语言要和用户问题一致（用户问中文答中文，问日文答日文，问英文答英文）；
    2. 遇到需要上网查询的问题时可以上网查询
    """
    # 步骤1：初始化向量库（主线程执行，有SessionContext）
    vector_db = init_vector_db_in_main()
    # 步骤2：构建RAG Chain（依赖注入：传入vector_db）
    qa_chain = build_qa_chain(vector_db)
    # 步骤3：创建工具（依赖注入：传入qa_chain）
    rag_qa_tool = create_rag_qa_tool(qa_chain)  # RAG工具
    # 步骤4：构建工具列表
    tools = [translate_excel_tool, translate_ppt_tool, rag_qa_tool]  # 工具列表
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

            # 状态变量
            thinking_text = ""          # 思考阶段累积的文本（灰色斜体）
            tool_calls = []             # 工具调用提示列表（如 "🔧 正在调用 rag_qa_chain..."）
            final_text = ""             # 最终回答累积的文本（正常样式）
            tool_encountered = False    # 是否已遇到工具调用（True 后开始累积 final_text）
            temp_status = None          # 临时状态，用于在永久内容出现前显示动态提示

            def update_display(cursor=True):
                """根据永久内容和临时状态构建并更新显示"""
                # 构建永久内容部分（思考文本 + 工具提示 + 最终回答）
                permanent_parts = []
                if tool_encountered:
                    # 有工具调用：思考部分（灰色）和最终回答（正常）分开
                    if thinking_text or tool_calls:
                        think_lines = []
                        if thinking_text:
                            think_lines.append(thinking_text.replace('\n', '<br>'))
                        for tip in tool_calls:
                            think_lines.append(tip)
                        think_html = '<br>'.join(think_lines)
                        permanent_parts.append(f'<div style="color: gray; font-style: italic;">{think_html}</div>')
                    if final_text:
                        final_html = final_text.replace('\n', '<br>')
                        permanent_parts.append(f'<div>{final_html}</div>')
                else:
                    # 无工具调用：所有思考内容都是最终回答（正常样式）
                    if thinking_text:
                        final_html = thinking_text.replace('\n', '<br>')
                        permanent_parts.append(f'<div>{final_html}</div>')

                # 决定最终显示内容
                if permanent_parts:
                    display_html = ''.join(permanent_parts)
                else:
                    # 无永久内容时，显示临时状态或默认提示
                    display_html = temp_status if temp_status else "🤔 思考中..."

                if cursor:
                    display_html += '▌'
                message_placeholder.markdown(display_html, unsafe_allow_html=True)

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
                        # 处理节点状态更新，更新临时状态
                        for node_name, node_output in data.items():
                            if node_name in ["agent", "model"]:
                                temp_status = "🤔 正在思考..."
                            elif node_name == "tools":
                                # 尝试从输出中提取工具名
                                tool_name = "未知工具"
                                if isinstance(node_output, dict) and "messages" in node_output:
                                    last_msg = node_output["messages"][-1]
                                    if hasattr(last_msg, "name"):
                                        tool_name = last_msg.name
                                temp_status = f"🔧 正在调用 {tool_name}..."
                            else:
                                temp_status = f"⏳ 执行步骤: {node_name}"
                        # 立即刷新显示（可能只显示临时状态）
                        update_display(cursor=True)

                    elif mode == "messages":
                        message_chunk, metadata = data

                        # 一旦收到实际消息块，清除临时状态（永久内容即将出现）
                        temp_status = None

                        # 处理工具调用消息
                        if isinstance(message_chunk, ToolMessageChunk):
                            tool_encountered = True
                            tool_name = getattr(message_chunk, "name", None) or metadata.get("tool_name", "未知工具")
                            tool_calls.append(f"🔧 正在调用 {tool_name}...")
                            update_display(cursor=True)
                            continue

                        # 处理 AI 消息块（仅来自最终模型节点的文本）
                        if isinstance(message_chunk, AIMessageChunk) and metadata.get("langgraph_node") == "model":
                            token = message_chunk.content
                            if not token:
                                continue

                            if not tool_encountered:
                                thinking_text += token
                            else:
                                final_text += token

                            update_display(cursor=True)

                # 流式结束，移除光标
                update_display(cursor=False)

                # 存储最终回答到历史（不含思考过程和工具提示）
                if tool_encountered:
                    answer_to_store = final_text
                else:
                    answer_to_store = thinking_text
                st.session_state.messages.append({"role": "assistant", "content": answer_to_store})

            except Exception as e:
                message_placeholder.empty()
                error_msg = f"处理失败：{type(e).__name__} - {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})

# ====================== 运行入口 ======================
if __name__ == "__main__":
    main()