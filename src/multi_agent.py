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
    
    # 2. 包装节点函数
    def call_main_agent(state: MultiAgentState):
        thread_id = "main_thread"  # 可以根据需要动态生成或传入线程ID
        config = {"configurable": {"thread_id": thread_id}}
        # 只取最新的用户消息作为本轮输入
        last_human = None
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                last_human = msg
                break
        # if last_human is None:
        #     return state
        print(f"state: {state}")
        sub_state = dict(state)
        sub_state["messages"] = [last_human] if last_human else []
        print(f"sub_state: {sub_state}")
        # 构造精简输入（不包含任何历史，因为历史会从 checkpointer 中恢复）
        print(f"调用主 Agent，输入消息: {last_human}")
        # 调用主 Agent，传入当前状态
        response = main_agent.invoke(sub_state, config=config)
        print(f"主 Agent 响应: {response}")
        return response  # 可能是 Command 或普通 state更新
    
    def call_code_agent(state: MultiAgentState):
        thread_id = "code_thread"  # 可以根据需要动态生成或传入线程ID
        config = {"configurable": {"thread_id": thread_id}}
        # 只取最新的用户消息作为本轮输入
        last_human = None
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                last_human = msg
                break
        if last_human is None:
            return state
        # 构造精简输入（不包含任何历史，因为历史会从 checkpointer 中恢复）
        sub_state = dict(state)
        sub_state["messages"] = [last_human] if last_human else []
        # 调用代码 Agent，传入当前状态
        response = code_agent.invoke(sub_state, config=config)
        return response
    
    # 3. 构建图
    builder = StateGraph(MultiAgentState)
    builder.add_node("main_agent", call_main_agent)
    builder.add_node("code_agent", call_code_agent)
    
    # 起始边：根据 active_agent 决定从哪个节点开始（默认为 main_agent）
    def route_initial(state: MultiAgentState) -> Literal["main_agent", "code_agent"]:
        print(f"初始active_agent: {state.get('active_agent')}")
        return state.get("active_agent", "main_agent")
    builder.add_conditional_edges(START, route_initial, ["main_agent", "code_agent"])
    
    # 结束边：每个节点后检查是否结束
    def route_after_agent(state: MultiAgentState) -> Literal["main_agent", "code_agent", "__end__"]:
        messages = state.get("messages", [])
        if messages:
            last = messages[-1]
            if isinstance(last, AIMessage) and not last.tool_calls:
                # 没有工具调用，说明 Agent 完成了回答，结束整个图
                return "__end__"
        # 否则根据 active_agent 继续路由（通常 Command 已经处理了跳转，这里作为后备）
        print(f"结束边检查active_agent: {state.get('active_agent')}")
        return state.get("active_agent", "main_agent")
    
    builder.add_conditional_edges("main_agent", route_after_agent, ["main_agent", "code_agent", END])
    builder.add_conditional_edges("code_agent", route_after_agent, ["main_agent", "code_agent", END])
    
    graph = builder.compile() #checkpointer=InMemorySaver()
    return graph