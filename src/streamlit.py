# 加载.env文件（必须放在代码最开头！）
from dotenv import load_dotenv
load_dotenv()  # 自动读取 .env 文件里的所有环境变量
import time
import warnings

warnings.filterwarnings("ignore")  # 屏蔽新手无关的警告

# 1. 导入LangChain核心模块
import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, AIMessageChunk, ToolMessageChunk
from langgraph.errors import GraphRecursionError
from langsmith import traceable
import asyncio
import nest_asyncio
import os

from rag import build_qa_chain  # 导入RAG问答链函数
from vector_db import TBOX_DOCS_DIR, diff_update_vector_db, get_local_docs_info  # 导入文档目录配置
from multi_agent import build_top_graph  # 导入创建Agent的函数

# 应用 nest_asyncio 以允许在已有事件循环中运行新的 asyncio.run()
# 强制使用原生 asyncio 事件循环，避免 uvloop
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
nest_asyncio.apply()

st.set_page_config(
    page_title="畅星TSU开发Agent",
    page_icon="🚗",
    layout="wide"
)

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
    if "top_graph" not in st.session_state:
        st.session_state.top_graph = build_top_graph()
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
            st.session_state.top_graph = build_top_graph()  # 创建新的Agent实例
            print("✅ 已创建新的Agent实例，已切换到最新向量库！")
    
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
                MAX_TURNS = int(os.getenv("MAX_TURNS") or 50)  # 最大循环轮数（LLM 调用次数）
                MAX_BUDGET_TOKENS = int(os.getenv("MAX_BUDGET_TOKENS") or 800000)  # 最大总 token 预算

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
                
                inputs = {
                    "messages": [HumanMessage(content=user_input)]
                }
                
                # ⭐ 关键：保存异步生成器对象，以便在 finally 中正确关闭
                event_stream = st.session_state.top_graph.astream_events(
                    inputs,
                    config=config,
                    version="v2"
                )
                # try:
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
                        if hasattr(chunk, "content") and chunk.content and event.get("metadata", {}).get("langgraph_node") == "model":
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
                # except GraphRecursionError as gre:
                #     error_msg = f"❌ 递归错误：已达到最大循环轮数 {MAX_TURNS}，请简化问题或增加轮数限制后重试。"
                #     message_placeholder.markdown(str(gre))
                #     return error_msg
                # except Exception as e:
                #     error_msg = f"❌ 处理过程中发生错误：{str(e)}"
                #     message_placeholder.markdown(error_msg)
                #     print(error_msg)
                #     return error_msg   

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