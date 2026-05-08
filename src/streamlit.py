# 加载.env文件（必须放在代码最开头！）
from dotenv import load_dotenv
load_dotenv()  # 自动读取 .env 文件里的所有环境变量
import time
import warnings
import re

warnings.filterwarnings("ignore")  # 屏蔽新手无关的警告

# 1. 导入LangChain核心模块
import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, AIMessageChunk, ToolMessageChunk
from langsmith import traceable
import asyncio
import nest_asyncio
import os
from pathlib import Path

from rag import build_qa_chain  # 导入RAG问答链函数
from vector_db import TBOX_DOCS_DIR, diff_update_vector_db, get_local_docs_info  # 导入文档目录配置
from multi_agent import build_top_graph  # 导入创建Agent的函数
from exceptions import InvalidAPIKeyError
from langgraph.errors import GraphRecursionError
from user_store import ensure_user_store_exists, verify_user

# 应用 nest_asyncio 以允许在已有事件循环中运行新的 asyncio.run()
# 强制使用原生 asyncio 事件循环，避免 uvloop
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
nest_asyncio.apply()

st.set_page_config(
    page_title="畅星TSU开发Agent",
    page_icon="🚗",
    layout="wide"
)
# 获取当前脚本所在目录的父级目录（即 SIS_Agent 根目录）
SIS_AGENT_ROOT = Path(__file__).parent.parent
DEFAULT_INPUT_DIR = os.getenv("TRANSLATE_INPUT_DIR") or str(SIS_AGENT_ROOT / "translate" / "input")
DEFAULT_OUTPUT_DIR = os.getenv("TRANSLATE_OUTPUT_DIR") or str(SIS_AGENT_ROOT / "translate" / "output")
WORKSPACE_DIR = SIS_AGENT_ROOT / "workspace"
# ----- 预算控制变量 -----
MAX_TURNS = int(os.getenv("MAX_TURNS") or 50)  # 最大循环轮数（LLM 调用次数）
MAX_BUDGET_TOKENS = int(os.getenv("MAX_BUDGET_TOKENS") or 800000)  # 最大总 token 预算

def get_translate_files(directory):
    """获取目录下所有文件名及完整路径"""
    if not os.path.exists(directory):
        return []
    return [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]


def get_workspace_files(directory):
    """获取 workspace 目录下所有文件名及完整路径"""
    if not directory.exists():
        return []
    return [f for f in directory.iterdir() if f.is_file()]


def sanitize_username(username: str) -> str:
    """将用户名转换为安全目录名"""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", username.strip())


def get_user_workspace_dir(username: str):
    """获取用户的 workspace 目录，按用户名隔离"""
    return SIS_AGENT_ROOT / "workspace" / sanitize_username(username)


def get_user_translate_input_dir(user_workspace_dir: Path) -> Path:
    """获取用户的翻译输入目录"""
    return user_workspace_dir / "translate" / "input"


def get_user_translate_output_dir(user_workspace_dir: Path) -> Path:
    """获取用户的翻译输出目录"""
    return user_workspace_dir / "translate" / "output"


def ensure_user_translate_dirs(user_workspace_dir: Path):
    """确保用户翻译输入/输出目录存在"""
    get_user_translate_input_dir(user_workspace_dir).mkdir(parents=True, exist_ok=True)
    get_user_translate_output_dir(user_workspace_dir).mkdir(parents=True, exist_ok=True)


def require_login():
    """确保用户登录，未登录则展示登录框并终止后续页面渲染"""
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "username" not in st.session_state:
        st.session_state.username = ""
    if "login_error" not in st.session_state:
        st.session_state.login_error = ""

    with st.sidebar:
        st.header("🔐 内部登录")
        if not st.session_state.logged_in:
            username = st.text_input("用户名", value=st.session_state.username, key="login_username")
            password = st.text_input("密码", type="password", key="login_password")
            submit = st.button("登录")
            if submit:
                if verify_user(username.strip(), password):
                    st.session_state.logged_in = True
                    st.session_state.username = username.strip()
                    st.session_state.login_error = ""
                    st.rerun()
                else:
                    st.session_state.logged_in = False
                    st.session_state.login_error = "用户名或密码错误。请联系管理员后端创建账号。"

            if st.session_state.login_error:
                st.error(st.session_state.login_error)

            st.stop()
        else:
            st.success(f"已登录：{st.session_state.username}")
            if st.button("退出登录"):
                st.session_state.clear()
                st.rerun()

def main():
    ensure_user_store_exists()
    require_login()

    username = st.session_state.username
    safe_username = sanitize_username(username)
    user_workspace_dir = get_user_workspace_dir(safe_username)
    user_workspace_dir.mkdir(parents=True, exist_ok=True)
    ensure_user_translate_dirs(user_workspace_dir)
    user_translate_input_dir = get_user_translate_input_dir(user_workspace_dir)
    user_translate_output_dir = get_user_translate_output_dir(user_workspace_dir)

    st.title("🚗 畅星TSU开发助手Agent（支持文件翻译、本地知识库查询）")

    with st.sidebar:
        st.header("🔑 配置")
        dashscope_key = st.text_input("DashScope API Key", type="password",
                                      help="用于大模型、翻译、重排序等服务")
        volc_key = st.text_input("火山引擎 API Key", type="password",
                                 help="用于联网搜索")

        if not dashscope_key or not volc_key:
            st.warning("请输入所有 API Key 后开始使用")
            st.stop()

        # 保存到 session_state，供其他模块使用
        st.session_state.dashscope_api_key = dashscope_key
        st.session_state.volc_api_key = volc_key

    st.sidebar.markdown("---")
    st.sidebar.header("📂 Workspace 文件管理")
    st.sidebar.caption("上传到 workspace 后，可被 Code Agent 访问处理，生成结果文件后可下载。")

    uploaded_ws_file = st.sidebar.file_uploader("上传到 workspace", type=None)
    if uploaded_ws_file is not None:
        saved_name = uploaded_ws_file.name
        save_path = user_workspace_dir / saved_name
        with open(save_path, "wb") as f:
            f.write(uploaded_ws_file.getbuffer())
        st.sidebar.success(f"✅ 已上传到 workspace：{saved_name}")

    st.sidebar.subheader("📄 Workspace 当前文件")
    workspace_files = get_workspace_files(user_workspace_dir)
    if workspace_files:
        for file_path in workspace_files:
            fname = file_path.name
            col1, col2 = st.sidebar.columns([3, 1])
            col1.write(f"`{fname}`")
            with open(file_path, "rb") as f:
                col2.download_button(
                    label="⬇️",
                    data=f,
                    file_name=fname,
                    key=f"ws_dl_{safe_username}_{fname}",
                    help="下载 workspace 文件"
                )
            if col2.button("🗑️", key=f"ws_del_{safe_username}_{fname}", help="删除此 workspace 文件"):
                file_path.unlink()
                st.rerun()
    else:
        st.sidebar.info("workspace 目录当前无文件")

    st.sidebar.markdown("---")
    st.sidebar.header("📂 翻译文件管理")
    # 上传区域
    uploaded_file = st.sidebar.file_uploader("上传待翻译文件", type=["pptx", "xlsx", "docx"])
    if uploaded_file is not None:
        saved_name = uploaded_file.name
        save_path = user_translate_input_dir / saved_name
        with open(save_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.sidebar.success(f"✅ 已上传到您的翻译输入目录：{saved_name}")

    # 已翻译文件列表
    st.sidebar.subheader("📥 下载翻译文件")
    output_files = get_translate_files(user_translate_output_dir)
    if output_files:
        for fname in output_files:
            file_path = user_translate_output_dir / fname
            col1, col2 = st.sidebar.columns([3, 1])
            col1.write(f"`{fname}`")
            # 下载按钮
            with open(file_path, "rb") as f:
                col2.download_button(
                    label="⬇️",
                    data=f,
                    file_name=fname,
                    key=f"dl_{fname}",
                    help="下载此文件"
                )
            # 删除按钮
            if col2.button("🗑️", key=f"del_{safe_username}_{fname}", help="删除此文件"):
                file_path.unlink()
                st.rerun()
    else:
        st.sidebar.info("暂无翻译完成的文件")

    # 可选：清空输入目录按钮（谨慎使用）
    if st.sidebar.button("刷新"):
        st.rerun()
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
        st.session_state.top_graph = build_top_graph(
            dashscope_api_key=dashscope_key,
            volc_api_key=volc_key,
            workspace_dir=str(user_workspace_dir),
            translate_source_dir=str(user_translate_input_dir),
            translate_output_dir=str(user_translate_output_dir)
        )
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
            new_vector_db = diff_update_vector_db(dashscope_key)
            # 2. 重新构建qa_chain（关键：用最新的向量库）
            new_qa_chain = build_qa_chain(new_vector_db, dashscope_key)
            st.session_state.qa_chain = new_qa_chain  # 更新QA链
            st.info("向量库已更新！")
            print("✅ 向量库已更新！")
            st.session_state.top_graph = build_top_graph(
                dashscope_api_key=dashscope_key,
                volc_api_key=volc_key,
                workspace_dir=str(user_workspace_dir),
                translate_source_dir=str(user_translate_input_dir),
                translate_output_dir=str(user_translate_output_dir)
            )  # 创建新的Agent实例
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

            # 同步调用异步函数
            try:
                # 获取当前事件循环，如果没有则创建
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            try:
                final_answer = loop.run_until_complete(process_with_events())
            except GraphRecursionError:
                final_answer = f"❌ 任务已终止：已达到最大循环轮数 {MAX_TURNS}。请简化问题或增加轮数限制后重试。"
                message_placeholder.markdown(final_answer)
            except InvalidAPIKeyError as e:
                final_answer = str(e)
                message_placeholder.markdown(final_answer)
                return
            except Exception as e:
                final_answer = f"❌ 处理过程中发生错误：{str(e)}。请稍后重试。"
                message_placeholder.markdown(final_answer)
                print(f"Friendly error shown to user: {e}")
            # 清除占位符内容（页面上直接消失）
            stats_placeholder.empty()
            # 最终显示去除光标
            message_placeholder.markdown(final_answer)
            st.session_state.messages.append({"role": "assistant", "content": final_answer})

# ====================== 运行入口 ======================
if __name__ == "__main__":
    main()