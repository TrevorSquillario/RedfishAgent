import asyncio
import logging
import os
from redfishagent.utils.logging import root_logger
from typing import Annotated, TypedDict, Optional, Any
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_postgres import PGVector
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
import openai

# 1. Define the State
class AgentState(TypedDict):
    # 'add_messages' allows the graph to append new messages to the history
    messages: Annotated[list, add_messages]
    alert_id: str
    query_message: str
    context: str  # Store RAG results here

logger = root_logger.getChild(__name__)


def _build_mcp_servers_map(mcp_entries: Optional[list[Any]]) -> dict:
    """Turn a list of MCP entries into the mapping expected by MultiServerMCPClient.

    Each entry may be:
    - a dict with a full server config (passed through)
    - a string URL (http/https)
    - a string path to a .py file (will be launched with `python` + stdio)
    """
    if not mcp_entries:
        return {}

    servers = {}
    for idx, entry in enumerate(mcp_entries):
        name = None
        cfg = None
        if isinstance(entry, dict):
            # allow explicit name in the dict
            name = entry.get("name") or f"mcp-{idx}"
            cfg = {k: v for k, v in entry.items() if k != "name"}
        elif isinstance(entry, str):
            name = f"mcp-{idx}"
            if entry.startswith(("http://", "https://")):
                cfg = {"url": entry, "transport": "sse"}
            elif entry.endswith(".py") or os.path.exists(entry):
                cfg = {"command": "python", "args": [entry], "transport": "stdio"}
            else:
                # treat unknown strings as URLs by default
                cfg = {"url": entry, "transport": "sse"}
        else:
            # unsupported type; skip
            logger.warning("Skipping unsupported MCP entry type: %s", type(entry))
            continue

        servers[name] = cfg

    return servers


async def main(db_service: Optional[Any] = None, config_service: Optional[Any] = None):
    # 2. Setup OpenAI / embeddings configuration from environment
    # Reads these environment variables (defaults used if not set):
    # - `OPENAI_API_BASE` : base URL for a local OpenAI-compatible server
    # - `OPENAI_API_KEY`  : API key (optional)
    # - `OPENAI_MODEL`    : model name (defaults to `text-embedding-3-small`)
    openai_api_base = os.getenv("OPENAI_API_BASE")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    embedding_model = os.getenv("OPENAI_MODEL", "text-embedding-3-small")
    # Optional separate embedding endpoint/key
    embed_api_base = os.getenv("OPENAI_API_BASE_EMBED", openai_api_base)
    embed_api_key = os.getenv("OPENAI_API_KEY_EMBED", openai_api_key)
    chat_model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o")

    if openai_api_key:
        openai.api_key = openai_api_key
    if openai_api_base:
        openai.api_base = openai_api_base

    # 2. Setup pgvector (RAG)
    # If a `db_service` is passed in, try to derive a vector store or connection from it.
    vector_store = None
    if db_service is not None:
        if hasattr(db_service, "vector_store"):
            vector_store = getattr(db_service, "vector_store")
        elif hasattr(db_service, "connection"):
            conn = getattr(db_service, "connection")
            prev_base = getattr(openai, "api_base", None)
            prev_key = getattr(openai, "api_key", None)
            if embed_api_key:
                openai.api_key = embed_api_key
            if embed_api_base:
                openai.api_base = embed_api_base
            try:
                embeddings_instance = OpenAIEmbeddings(model=embedding_model)
            finally:
                openai.api_base = prev_base
                openai.api_key = prev_key

            vector_store = PGVector(
                connection=conn,
                embeddings=embeddings_instance,
                collection_name="alerts_collection",
            )
        elif hasattr(db_service, "dsn"):
            conn = getattr(db_service, "dsn")
            prev_base = getattr(openai, "api_base", None)
            prev_key = getattr(openai, "api_key", None)
            if embed_api_key:
                openai.api_key = embed_api_key
            if embed_api_base:
                openai.api_base = embed_api_base
            try:
                embeddings_instance = OpenAIEmbeddings(model=embedding_model)
            finally:
                openai.api_base = prev_base
                openai.api_key = prev_key

            vector_store = PGVector(
                connection=conn,
                embeddings=embeddings_instance,
                collection_name="alerts_collection",
            )

    if vector_store is None:
        prev_base = getattr(openai, "api_base", None)
        prev_key = getattr(openai, "api_key", None)
        if embed_api_key:
            openai.api_key = embed_api_key
        if embed_api_base:
            openai.api_base = embed_api_base
        try:
            embeddings_instance = OpenAIEmbeddings(model=embedding_model)
        finally:
            openai.api_base = prev_base
            openai.api_key = prev_key

        vector_store = PGVector(
            connection="postgresql+psycopg2://user:pass@localhost:5432/dbname",
            embeddings=embeddings_instance,
            collection_name="alerts_collection",
        )

    # 3. Setup MCP Client & Dynamic Tool Lookup
    # This connects to your MCP server (e.g., via stdio or SSE)
    # 3. Setup MCP Client & Dynamic Tool Lookup
    # The `config_service` may be a dict-like or object with an `mcp` attribute
    mcp_entries = None
    if config_service is not None:
        if isinstance(config_service, dict):
            mcp_entries = config_service.get("mcp")
        elif hasattr(config_service, "get"):
            try:
                mcp_entries = config_service.get("mcp")
            except Exception:
                mcp_entries = None
        elif hasattr(config_service, "mcp"):
            mcp_entries = getattr(config_service, "mcp")

    servers_map = _build_mcp_servers_map(mcp_entries)

    async with MultiServerMCPClient(servers_map) as mcp_client:
        
        # DYNAMIC LOOKUP: Fetch tools available on the server right now
        mcp_tools = await mcp_client.get_tools()
        
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
            llm = ChatOpenAI(model=chat_model).bind_tools(mcp_tools)
            
            # Inject context into the system prompt
            system_msg = f"Use this context to help: {state['context']}"
            messages = [{"role": "system", "content": system_msg}] + state["messages"]
            
            response = await llm.ainvoke(messages)
            return {"messages": [response]}

        # 5. Build the Graph
        workflow = StateGraph(AgentState)

        workflow.add_node("retriever", retrieve_context)
        workflow.add_node("agent", call_model)
        workflow.add_node("tools", ToolNode(mcp_tools))

        workflow.add_edge(START, "retriever")
        workflow.add_edge("retriever", "agent")
        
        # Conditional logic: If LLM calls a tool, go to 'tools' node, else END
        def should_continue(state: AgentState):
            if state["messages"][-1].tool_calls:
                return "tools"
            return END

        workflow.add_conditional_edges("agent", should_continue)
        workflow.add_edge("tools", "agent")

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