import asyncio
import json
import logging
import os
import re
from typing import Annotated, TypedDict, Optional, Any, List
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_core.messages import message_to_dict
from openinference.instrumentation.langchain import LangChainInstrumentor
from phoenix.otel import register

from utils.logging import setup_logger
from models.app import RedfishLogEntry, ConfigModel, PluginsConfig

# `mcp_tools` are expected to be supplied by the caller (e.g. via LLMService.run_agent)

# 1. Define the State
class AgentState(TypedDict):
    # 'add_messages' allows the graph to append new messages to the history
    messages: Annotated[list, add_messages]
    source: str
    alert_ids: List[str]
    query_message: str
    context: str  # Store RAG results here
    labels: dict
    final_message: str  # Extracted final LLM response text

logger = setup_logger(__name__)

tracer_provider = register(
  project_name="RedfishAgent",
)
LangChainInstrumentor().instrument(tracer_provider=tracer_provider)

async def main(log_entry: RedfishLogEntry, config: ConfigModel, db_service: Optional[Any] = None, mcp_tools: Optional[Any] = None) -> dict:
    # Setup OpenAI configuration from environment
    base = os.getenv('OPENAI_API_BASE', 'http://192.168.0.30:8090').rstrip('/')
    openai_api_base = f"{base}/v1" if base else ""
    openai_api_key = os.getenv("OPENAI_API_KEY", "EMPTY")
    openai_model = os.getenv("OPENAI_MODEL", "nvidia/Nemotron-Cascade-2-30B-A3B")

    # Guard against None so ToolNode and bind_tools always receive a list
    tools = mcp_tools or []

    # Instantiate the LLM once; reused across all call_model invocations
    llm = ChatOpenAI(
        model=openai_model, base_url=openai_api_base, api_key=openai_api_key
    ).bind_tools(tools)

    # Define Graph Nodes
    async def call_model(state: AgentState):
        """Invoke the LLM; it decides whether to call tools."""
        context_text = state.get("context", "")
        system_msg = f"Use this context to help: {context_text}"
        messages = [{"role": "system", "content": system_msg}] + state.get("messages", [])
        response = await llm.ainvoke(messages)
        return {"messages": [response], "source": state.get("source", "redfish_agent")}

    def format_output(state: AgentState):
        """Extract the final LLM response text into final_message."""
        msgs = state.get("messages") or []
        final_msg = ""
        for msg in reversed(msgs):
            if getattr(msg, "tool_calls", None):
                continue
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                # Multimodal: join text parts
                content = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            if content:
                final_msg = content
                break
        return {"final_message": final_msg}

    # Build the Graph
    workflow = StateGraph(AgentState)

    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_node("format_output", format_output)

    workflow.add_edge(START, "agent")

    def should_continue(state: AgentState):
        msgs = state.get("messages") or []
        last_msg = msgs[-1] if msgs else None
        if last_msg is not None and getattr(last_msg, "tool_calls", None):
            return "tools"
        return "format_output"

    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")
    workflow.add_edge("format_output", END)

    app = workflow.compile()

    # 6. Run it
    # Parse alert IDs from the log entry payload
    unique_alert_ids: List[str] = []
    payload = getattr(log_entry, "payload", {}) or {}
    payload_obj: dict = {}
    if isinstance(payload, str):
        try:
            payload_obj = json.loads(payload)
        except Exception:
            payload_obj = {}
    elif isinstance(payload, dict):
        payload_obj = payload

    events = payload_obj.get("Events") if isinstance(payload_obj, dict) else None
    if isinstance(events, list):
        for ev in events:
            if not isinstance(ev, dict):
                continue
            mid = ev.get("MessageId")
            if not mid:
                continue
            mid_str = str(mid).strip()

            # If MessageId is dotted (e.g. 'IDRAC.2.9.CPU0001'), prefer the last segment
            last_seg = mid_str.split('.')[-1] if '.' in mid_str else mid_str

            # Prefer a pattern with letters+digits (e.g. CPU0001, FAN123)
            m = re.search(r'([A-Za-z]+\d+)$', last_seg)
            candidate = m.group(1).upper() if m else (last_seg.upper() if last_seg else None)

            if candidate and candidate not in unique_alert_ids:
                unique_alert_ids.append(candidate)

    # Prefer a configured prompt template if available: config.prompts.redfish_agent.main
    prompt_template = None
    if config is not None:
        try:
            prompts = getattr(config, "prompts", {}) or {}
            ra = prompts.get("redfish_agent") or {}
            if isinstance(ra, dict):
                prompt_template = ra.get("main")
            else:
                # allow cases where `redfish_agent` is a plain string
                prompt_template = ra
        except Exception:
            prompt_template = None

    if not prompt_template:
        prompt_template = (
            "1. Use the get_error_and_event_registry tool to get details about the alerts.\n"
            "2. Get the top 10 lifecycle logs of severity Critical.\n"
            "3. Discover the related metrics using the discover_metrics tool, "
            "then query them using the get_metric_analysis tool."
        )

    user_prompt = (
        f"Alert triggered host {log_entry.source}.\n"
        f"Alerts: {unique_alert_ids}\n"
        f"Labels: {log_entry.labels}\n"
        f"{prompt_template}"
    )

    inputs: AgentState = {
        "messages": [("user", user_prompt)],
        "source": getattr(log_entry, "source", "redfish_agent"),
        "labels": getattr(log_entry, "labels", {}) or {},
        "alert_ids": unique_alert_ids,
        "query_message": str(getattr(log_entry, "payload", "")),
        "context": "",
        "final_message": "",
    }

    final_state: AgentState = await app.ainvoke(inputs)

    # Serialize messages so the returned dict is fully JSON-serializable
    return {
        **final_state,
        "messages": [message_to_dict(m) for m in final_state.get("messages", [])],
    }