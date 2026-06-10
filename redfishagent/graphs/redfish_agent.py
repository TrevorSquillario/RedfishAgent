"""redfish_agent graph

This module builds and runs a small agentic StateGraph to handle Redfish
alerts using a language model (LLM) and optional external tools.

Overview
- The `main` coroutine is the entrypoint. It constructs a `StateGraph` with
    three runtime nodes: `agent` (invokes the LLM), `tools` (invokes any bound
    tool nodes), and `format_output` (extracts the final human-readable
    response). The graph loops between `agent` and `tools` when the LLM
    produces a tool call, otherwise it proceeds to `format_output` and exits.

- `AgentState` defines the shared runtime state passed between nodes. The
    LLM receives `messages` (a history) and may append tool calls to messages.
- `ToolNode` is used to wrap external tooling (database, Prometheus, etc.)
    and is expected to be supplied by the caller via the `mcp_tools` parameter.
- `call_model`, `should_continue`, and `format_output` are the key submethods
    that demonstrate LLM/tool orchestration, result selection, and serialization.

Example usage
```
final_state = await main(log_entry, config, db_service=db, mcp_tools=tools)
print(final_state['final_message'])
```
"""

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
from models.app import LogEntry, ConfigModel, PluginsConfig

# `mcp_tools` are expected to be supplied by the caller (e.g. via LLMService.run_agent)

# 1. Define the State
class AgentState(TypedDict):
    """Runtime state shared between graph nodes.

    Fields
    - `messages`: list of chat messages / events. The `add_messages` annotation
        allows the graph to append new messages to the conversation history.
    - `source`: string identifying the origin of the alert/request.
    - `log_entry`: original `LogEntry` object for reference and RAG.
    - `context`: additional contextual information (RAG results) used by the LLM.
    - `labels`: alert labels / metadata.
    - `final_message`: final extracted text response from the LLM for downstream use.
    """
    messages: Annotated[list, add_messages]
    source: str
    log_entry: LogEntry
    context: str  # Store RAG results here
    labels: dict
    final_message: str  # Extracted final LLM response text
    system_prompt: Optional[str]

logger = setup_logger(__name__)

tracer_provider = register(
  project_name="RedfishAgent",
)
LangChainInstrumentor().instrument(tracer_provider=tracer_provider)

async def main(log_entry: LogEntry, config: ConfigModel, db_service: Optional[Any] = None, mcp_tools: Optional[Any] = None) -> dict:
    """Entrypoint: run the Redfish agent StateGraph for a single alert.

    Args:
        log_entry: `LogEntry` representing the alert or event to process.
        config: `ConfigModel` containing optional prompt templates under
            `config.prompts.redfish_agent.main`.
        db_service: optional database service object (passed through to tools).
        mcp_tools: optional list of tools (ToolNode-compatible) to bind to the LLM.

    Returns:
        A serializable dict representing the final `AgentState`. The returned dict
        contains `final_message` and `messages` (serialized via `message_to_dict`).
    """
    # Setup OpenAI configuration from environment
    base = os.getenv('OPENAI_API_BASE', 'http://192.168.0.30:8090').rstrip('/')
    openai_api_base = f"{base}/v1" if base else ""
    openai_api_key = os.getenv("OPENAI_API_KEY", "EMPTY")
    openai_model = os.getenv("OPENAI_MODEL", "google/gemma-4-26B-A4B-it")

    # Guard against None so ToolNode and bind_tools always receive a list
    tools = mcp_tools or []

    # Instantiate the LLM once; reused across all call_model invocations
    llm = ChatOpenAI(
        model=openai_model, base_url=openai_api_base, api_key=openai_api_key
    ).bind_tools(tools)

    # Define Graph Nodes
    async def call_model(state: AgentState):
        """Invoke the LLM with the current state and return updated messages.

        Behavior
        - Prepends a brief system message containing `state['context']`.
        - Passes the combined `messages` history to the bound LLM.
        - If the last tool message contains an HTTP URL, reformat it as a
          multimodal list (text + image_url) for vLLM-style consumption.
        - For embedded base64 image parts, append a human message with a
          data-URI `image_url` so the model can access the image.

        Returns a dict with the new `messages` list and the `source` tag.
        """
        context_text = state.get("context", "")
        system_base = state.get("system_prompt")
        if system_base:
            system_msg = system_base
            if context_text:
                system_msg = f"{system_base}\n\nContext:\n{context_text}"
        else:
            system_msg = f"Use this context to help: {context_text}"

        # Work on a mutable copy of the incoming messages
        state_messages = list(state.get("messages", []) or [])

        # Helper: extract a message's content regardless of shape
        def _extract_content(msg: Any):
            if isinstance(msg, (list, tuple)) and len(msg) >= 2:
                return msg[1]
            if isinstance(msg, dict):
                return msg.get("content")
            return getattr(msg, "content", None)

        # Helper: replace the last message while preserving its outer shape
        def _replace_last(new_content: Any):
            if not state_messages:
                return
            last = state_messages[-1]
            if isinstance(last, (list, tuple)) and len(last) >= 2:
                state_messages[-1] = (last[0], new_content)
            elif isinstance(last, dict):
                new_msg = dict(last)
                new_msg["content"] = new_content
                state_messages[-1] = new_msg
            else:
                try:
                    setattr(last, "content", new_content)
                except Exception:
                    state_messages[-1] = {"role": "tool", "content": new_content}

        # If the last message appears to be a tool message containing an HTTP URL,
        # convert it to a multimodal structure expected by vLLM handlers.
        if state_messages:
            last_content = _extract_content(state_messages[-1])
            if isinstance(last_content, str) and "http" in last_content:
                url = last_content.strip()
                multimodal = [
                    {"type": "text", "text": "Tool output received."},
                    {"type": "image_url", "image_url": {"url": url}},
                ]
                _replace_last(multimodal)

        # Scan for embedded base64 image parts and append user messages with
        # data-URI image_urls so the model sees them as accessible image URLs.
        processed_msgs: List[Any] = []
        for m in state_messages:
            processed_msgs.append(m)
            content = _extract_content(m)
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image" and part.get("base64"):
                        b64 = part.get("base64")
                        mime = part.get("mime_type", "image/png")
                        data_url = f"data:{mime};base64,{b64}"
                        processed_msgs.append(("user", [{"type": "image_url", "image_url": {"url": data_url}}]))

        messages = [{"role": "system", "content": system_msg}] + processed_msgs
        response = await llm.ainvoke(messages)
        return {"messages": [response], "source": state.get("source", "redfish_agent")}

    def format_output(state: AgentState):
        """Select the best non-tool message text as `final_message`.

        Selection rules
        - Iterate messages in reverse (newest first).
        - Skip messages that contain `tool_calls` (these are not human responses).
        - If `content` is multimodal (a list of dict parts), join textual parts.
        - The first non-empty content encountered becomes `final_message`.

        Returns a dict containing `final_message`.
        """
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

    
    def should_continue(state: AgentState):
        """Decide the next graph node after `agent` returns.

        - If the last message includes a `tool_calls` attribute, transition
          to the `tools` node to execute bound tools.
        - Otherwise, proceed to `format_output` to extract the final text.
        """
        msgs = state.get("messages") or []
        last_msg = msgs[-1] if msgs else None
        if last_msg is not None and getattr(last_msg, "tool_calls", None):
            return "tools"
        return "format_output"


    # Build the Graph
    workflow = StateGraph(AgentState)

    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_node("format_output", format_output)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")
    workflow.add_edge("format_output", END)

    app = workflow.compile()

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
        f"Alert triggered host {getattr(log_entry, 'source', '')}.\n"
        f"Event Timestamp: {getattr(log_entry, 'event_timestamp', '')}\n"
        f"Event ID: {getattr(log_entry, 'event_id', '')}\n"
        f"Message ID: {getattr(log_entry, 'message_id', '')}\n"
        f"Message: {getattr(log_entry, 'message', '')}\n"
        f"Labels: {getattr(log_entry, 'labels', {})}\n"
        f"{prompt_template}"
    )

    inputs: AgentState = {
        "messages": [("user", user_prompt)],
        "source": getattr(log_entry, "source", "redfish_agent"),
        "labels": getattr(log_entry, "labels", {}) or {},
        "log_entry": log_entry,
        "context": "",
        "system_prompt": None,
        "final_message": "",
    }

    system_prompt = "You are an experienced hardware troubleshooting system assistant. You are acting as a methodical diagnostic advisor that researches, documents, and justifies every assumption and claim."

    # Prefer configured system prompt if present at `config.prompts.system`
    if config is not None:
        try:
            prompts = getattr(config, "prompts", {}) or {}
            sys_p = prompts.get("system")
            if sys_p:
                inputs["system_prompt"] = sys_p
            else:
                inputs["system_prompt"] = system_prompt
        except Exception:
            pass

    final_state: AgentState = await app.ainvoke(inputs)

    # Serialize messages so the returned dict is fully JSON-serializable
    return {
        **final_state,
        "messages": [message_to_dict(m) for m in final_state.get("messages", [])],
    }