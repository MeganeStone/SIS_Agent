from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from langchain.tools import ToolRuntime
from typing import Literal, NotRequired, TypedDict
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from langchain.agents import AgentState
from code_agent import create_code_agent
from tbox_doc_agent import create_tbox_agent

# 定义全局状态
class MultiAgentState(AgentState):
    active_agent: NotRequired[str]

def build_top_graph():
    # 1. 创建两个 Agent 实例（注意节点名称要与 Command 中的 goto 一致）
    main_agent = create_tbox_agent(code_agent_node_name="code_agent")
    code_agent = create_code_agent(main_agent_node_name="main_agent")
    main_context = []  # 主Agent的会话上下文
    code_context = []  # 代码Agent的会话上下文
    
    # 2. 包装节点函数
    def call_main_agent(state: MultiAgentState):
        nonlocal main_context
        # 只取最新的用户消息作为本轮输入
        last_human = None
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                last_human = msg
                break
        # 3. 构造智能体输入：必须是 字典！！！（核心修复）
        agent_input = {
            "messages": main_context + ([last_human] if last_human else [])
        }
        # 调用主 Agent，传入当前状态
        response = main_agent.invoke(agent_input)
        main_context = response.get("messages", main_context)  # 优先使用主Agent返回的记忆更新
        return response  # 可能是 Command 或普通 state更新
    
    def call_code_agent(state: MultiAgentState):
        nonlocal code_context
        # 只取最新的用户消息作为本轮输入
        last_human = None
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                last_human = msg
                break
        agent_input = {
            "messages": code_context + ([last_human] if last_human else [])
        }
        # 调用代码 Agent，传入当前状态
        response = code_agent.invoke(agent_input)
        code_context = response.get("messages", code_context)  # 优先使用代码Agent返回的记忆更新
        return response
    
    # 3. 构建图
    builder = StateGraph(MultiAgentState)
    builder.add_node("main_agent", call_main_agent)
    builder.add_node("code_agent", call_code_agent)
    
    # 起始边：根据 active_agent 决定从哪个节点开始（默认为 main_agent）
    def route_initial(state: MultiAgentState) -> Literal["main_agent", "code_agent"]:
        return state.get("active_agent", "main_agent")
    builder.add_conditional_edges(START, route_initial, ["main_agent", "code_agent"])
    
    graph = builder.compile(checkpointer=InMemorySaver()) 
    return graph