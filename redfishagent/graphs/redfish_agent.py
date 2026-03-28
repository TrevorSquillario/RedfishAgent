import asyncio
import logging
import os
from utils.logging import setup_logger
from typing import Annotated, TypedDict, Optional, Any
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from openinference.instrumentation.langchain import LangChainInstrumentor
from phoenix.otel import register

# `mcp_tools` are expected to be supplied by the caller (e.g. via LLMService.run_agent)

# 1. Define the State
class AgentState(TypedDict):
    # 'add_messages' allows the graph to append new messages to the history
    messages: Annotated[list, add_messages]
    alert_id: str
    query_message: str
    context: str  # Store RAG results here

logger = setup_logger(__name__)

tracer_provider = register(
  project_name="RedfishAgent",
)
LangChainInstrumentor().instrument(tracer_provider=tracer_provider)

async def main(db_service: Optional[Any] = None, config_service: Optional[Any] = None, mcp_tools: Optional[Any] = None):
    # 2. Setup OpenAI / embeddings configuration from environment
    # Reads these environment variables (defaults used if not set):
    # - `OPENAI_API_BASE` : base URL for a local OpenAI-compatible server
    # - `OPENAI_API_KEY`  : API key (optional)
    # - `OPENAI_MODEL`    : model name (defaults to `text-embedding-3-small`)
    base = os.getenv('OPENAI_API_BASE', '').rstrip('/')
    openai_api_base = f"{base}/v1" if base else ""
    openai_api_key = os.getenv("OPENAI_API_KEY", "EMPTY")
    openai_model = os.getenv("OPENAI_MODEL", "text-embedding-3-small")
    # Optional separate embedding endpoint/key
    #embed_api_base = os.getenv("OPENAI_API_BASE_EMBED", openai_api_base)
    #embed_api_key = os.getenv("OPENAI_API_KEY_EMBED", openai_api_key)
    #chat_model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o")

    # `mcp_tools` is provided by the caller; if not provided the graph
    # will run without bound tools (safe default).

    # 4. Define Graph Nodes
    async def retrieve_context(state: AgentState):
        """Perform RAG lookup using alert_id as a filter."""
        docs = await vector_store.asimilarity_search(
            state["query_message"], 
            k=3, 
            filter={"alert_id": state["alert_id"]}
        )
        context_text = "\n".join([d.page_content for d in docs])
        return {"context": context_text}

    async def call_model(state: AgentState):
        """The LLM deciding whether to use the dynamic MCP tools."""
        llm = ChatOpenAI(model=openai_model, base_url=openai_api_base, api_key=openai_api_key).bind_tools(mcp_tools)

        # Inject context into the system prompt (use safe default if missing)
        context_text = state.get("context", "")
        system_msg = f"Use this context to help: {context_text}"
        messages = [{"role": "system", "content": system_msg}] + state.get("messages", [])

        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    # 5. Build the Graph
    workflow = StateGraph(AgentState)

    #workflow.add_node("retriever", retrieve_context)
    workflow.add_node("agent", call_model)
    #workflow.add_node("tools", ToolNode(mcp_tools))

    workflow.add_edge(START, "agent")
    #workflow.add_edge(START, "retriever")
    #workflow.add_edge("retriever", "agent")

    # Conditional logic: If LLM calls a tool, go to 'tools' node, else END
    def should_continue(state: AgentState):
        if state["messages"][-1].tool_calls:
            return "tools"
        return END

    workflow.add_conditional_edges("agent", should_continue)
    #workflow.add_edge("tools", "agent")

    app = workflow.compile()

    # 6. Run it
    inputs = {
        "messages": [("user", "Check the logs for this alert.")],
        "alert_id": "ALT-9901",
        "query_message": "Critical failure in database connection"
    }

    async for chunk in app.astream(inputs):
        print(chunk)

if __name__ == "__main__":
    asyncio.run(main())