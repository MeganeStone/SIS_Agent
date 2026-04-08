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
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, AIMessageChunk, ToolMessageChunk
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents.middleware import SummarizationMiddleware
from langgraph.errors import GraphRecursionError
from langsmith import traceable
import asyncio
import nest_asyncio

# 注意：如果你的tools/rag_new/vector_db_new文件路径不对，需自行调整
from tools import translate_file_tool, create_rag_qa_tool, web_search  # 导入翻译工具和RAG工具
from rag_new import build_qa_chain  # 导入RAG问答链函数
from vector_db_new import TBOX_DOCS_DIR, diff_update_vector_db, get_local_docs_info, get_vector_db  # 导入文档目录配置

st.set_page_config(
    page_title="畅星TSU开发Agent",
    page_icon="🚗",
    layout="wide"
)
# 应用 nest_asyncio 以允许在已有事件循环中运行新的 asyncio.run()
nest_asyncio.apply()
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
    2. 文件翻译：仅用于翻译文件（.pptx、.xlsx等）
       - 参数格式（必须是合法JSON）：{"file_name":"文件名","target_lang":"目标语言"}
    3. web_search：仅用于搜索网络信息
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
    tools = [translate_file_tool, rag_qa_tool, web_search]  # 工具列表
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
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            stats_placeholder = st.empty()  # 用于显示状态信息
            stats_placeholder.markdown("🤔 思考中...")
            message_placeholder = st.empty()

            # 定义一个异步函数来处理 astream_events
            async def process_with_events():
                # ----- 预算控制变量 -----
                MAX_TURNS = 10                 # 最大循环轮数（LLM 调用次数）
                MAX_BUDGET_TOKENS = 10000     # 最大总 token 预算
                
                total_input_tokens = 0
                total_output_tokens = 0
                total_all_tokens = 0
                
                token_placeholder = st.empty()  # 用于显示 token 统计
                should_stop = False
                final_answer = ""
                current_tool_name = ""
                
                # 配置：限制最大循环步数（LangGraph 原生支持）
                config = {
                    "configurable": {"thread_id": "1"},
                    "recursion_limit": MAX_TURNS
                }
                
                # ⭐ 关键：保存异步生成器对象，以便在 finally 中正确关闭
                event_stream = st.session_state.agent.astream_events(
                    {"messages": [HumanMessage(content=user_input)]},
                    config=config,
                    version="v2"
                )
                try:
                    async for event in event_stream:
                        kind = event["event"]
                        
                        # ------------------- 工具相关 -------------------
                        if kind == "on_tool_start":
                            current_tool_name = event.get("name", "工具")
                            stats_placeholder.markdown(f"🔧 正在调用 {current_tool_name} 工具...")
                                        
                        # ------------------- 检索（可选）-------------------
                        elif kind == "on_retriever_start":
                            stats_placeholder.markdown(f"🔍 正在检索文档...")
                        elif kind == "on_retriever_end":
                            stats_placeholder.markdown(f"🔍 文档检索完成，正在整理答案...")
                        
                        # ------------------- 流式输出（实时显示回答）-------------------
                        elif kind == "on_chat_model_stream":
                            chunk = event["data"]["chunk"]
                            if hasattr(chunk, "content") and chunk.content and event.get("metadata", {}).get("langgraph_node") != "SummarizationMiddleware.before_model":
                                final_answer += chunk.content
                                message_placeholder.markdown(final_answer + "▌")
                        
                        # ------------------- ⭐ 关键：Chat Model 调用结束 -------------------
                        elif kind == "on_chat_model_end":
                            # 获取完整的 AIMessage（不是 Chunk）
                            output_msg = event["data"]["output"]
                            
                            # 提取 token 用量
                            input_toks = 0
                            output_toks = 0
                            try:
                                if hasattr(output_msg, "usage_metadata") and output_msg.usage_metadata:
                                    input_toks = output_msg.usage_metadata.get("input_tokens", 0)
                                    output_toks = output_msg.usage_metadata.get("output_tokens", 0)
                                    
                            except Exception as e:
                                print(f"提取 token 失败: {e}")
                            
                            total_input_tokens += input_toks
                            total_output_tokens += output_toks
                            total_all_tokens = total_input_tokens + total_output_tokens
                            
                            # 更新 UI 统计
                            token_placeholder.markdown(f"""
                            **📊 实时统计**  
                            - 输入 token: {total_input_tokens}  
                            - 输出 token: {total_output_tokens}
                            - **累计总消耗: {total_all_tokens} / {MAX_BUDGET_TOKENS}**  
                            """)
                            
                            # 检查预算超限
                            if total_all_tokens >= MAX_BUDGET_TOKENS:
                                should_stop = True
                                token_placeholder.markdown(f"🚨 **预算超限！累计 {total_all_tokens} token 已达到上限 {MAX_BUDGET_TOKENS}，任务终止。**")
                                await event_stream.aclose()
                                break
                    
                    # 如果因超限终止，返回提示
                    if should_stop:
                        final_answer = f"❌ 任务已终止：达到限制，总 token {total_all_tokens}/{MAX_BUDGET_TOKENS}）。请简化问题或增加限额后重试。"
                        message_placeholder.markdown(final_answer)
                        return final_answer
                    
                    return final_answer
                except GraphRecursionError as gre:
                    error_msg = f"❌ 递归错误：已达到最大循环轮数 {MAX_TURNS}，请简化问题或增加轮数限制后重试。"
                    message_placeholder.markdown(str(gre))
                    return error_msg
                except Exception as e:
                    error_msg = f"❌ 处理过程中发生错误：{str(e)}"
                    message_placeholder.markdown(error_msg)
                    print(error_msg)
                    return error_msg   

            # 同步调用异步函数
            try:
                # 获取当前事件循环，如果没有则创建
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            final_answer = loop.run_until_complete(process_with_events())
            # 清除占位符内容（页面上直接消失）
            stats_placeholder.empty()
            # 最终显示去除光标
            message_placeholder.markdown(final_answer)
            st.session_state.messages.append({"role": "assistant", "content": final_answer})

# ====================== 运行入口 ======================
if __name__ == "__main__":
    main()