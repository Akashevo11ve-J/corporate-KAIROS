import json
from concurrent.futures import ThreadPoolExecutor

from config import (
    MAIN_AGENT_MODEL,
    MAX_TOKENS_RESPONSE,
    SIGNAL_IMMEDIATE_PROCEED,
    SIGNAL_READY_TO_PROCEED,
    TEMP_MAIN_AGENT,
    anthropic_client,
)
from prompts import FORMATTING_RULES, MAIN_AGENT_SYSTEM_DYNAMIC, MAIN_AGENT_SYSTEM_STATIC, get_level_guidance
from session import Session
from tools.tool_executor import execute_tool
from tools.tool_schemas import TOOL_SCHEMAS, TOOL_STATUS_MAP
from utils import strip_signals


def _parse_tool_input(input_str: str) -> dict:
    """Safely parse a JSON string from a streaming tool input block."""
    if not input_str:
        return {}
    try:
        return json.loads(input_str)
    except Exception:
        return {}


def _build_system_prompt(session: Session, video_watch_status="no_video") -> list:
    current_item = session.get_current_item() or {}
    skillsets_str = ", ".join(session.user_skillsets)

    static_text = MAIN_AGENT_SYSTEM_STATIC.format(
        ready_signal=SIGNAL_READY_TO_PROCEED,
        immediate_signal=SIGNAL_IMMEDIATE_PROCEED,
        formatting_rules=FORMATTING_RULES,
    )

    dynamic_text = MAIN_AGENT_SYSTEM_DYNAMIC.format(
        user_name=session.user_name,
        user_role=session.user_role,
        user_description=session.user_description,
        user_skillsets=skillsets_str,
        user_level_guidance=get_level_guidance(session.user_level, session.user_tactics),
        current_content_title=current_item.get("title", ""),
        content_type=current_item.get("type", "slide"),
        course_progress_json=session.get_course_progress_json(),
        course_wrap=session.course_wrap,
        current_content_context=session.current_content_context,
    )

    # Add video watch context if applicable
    video_context = ""
    if video_watch_status != "no_video":
        video_context_map = {
            "watched": "The user watched the video completely without significant skipping.",
            "not_watched": "The user has NOT watched the video. They opened it but did not watch.",
            "skipped_to_end": "The user did NOT watch the video properly. They used the seek bar to jump to the end.",
            "partial_skip": "The user partially watched the video but skipped through sections."
        }
        context = video_context_map.get(video_watch_status, "")
        if context:
            video_context = f"\n\nCURRENT VIDEO WATCH STATUS: {context}"
    dynamic_text += video_context

    return [
        {"type": "text", "text": static_text, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": dynamic_text},
    ]

def _build_messages(session: Session) -> list:
    messages = list(session.recent_messages)

    if not session.history_summary:
        return messages

    summary_injection = f"[CURRENT TOPIC — CONVERSATION SUMMARY SO FAR]\n{session.history_summary}"

    for i, msg in enumerate(messages):
        if msg["role"] == "user":
            messages[i] = {
                "role": "user",
                "content": f"{summary_injection}\n\n[CURRENT MESSAGE]\n{msg['content']}"
            }
            return messages

    messages.insert(0, {"role": "user", "content": summary_injection})
    return messages


def _execute_tool_block(tb: dict, session: Session, course_id: str, tool_calls_made: list, emit) -> dict:
    """Run a single tool and return its result block."""
    tool_input = _parse_tool_input(tb["input_str"])
    tool_name = tb["name"]

    tool_calls_made.append(tool_name)
    print(f"[MainAgent] >> tool: {tool_name} | input: {json.dumps(tool_input, ensure_ascii=False)}", flush=True)

    emit("tool_start", {"tool": tool_name, "label": TOOL_STATUS_MAP.get(tool_name, "thinking")})
    result = execute_tool(tool_name, tool_input, session, course_id)
    emit("tool_done", {"tool": tool_name})

    print(f"[MainAgent]    result {tool_name} ({len(result)} chars): {result[:300]}", flush=True)
    return {"type": "tool_result", "tool_use_id": tb["id"], "content": result}


def _run_streaming_loop(session: Session, course_id: str, messages: list, emit, video_watch_status="no_video") -> tuple[str, list]:
    tool_calls_made = []
    loop_messages = list(messages)
    full_text = ""

    while True:
        print(f"[MainAgent] API call | model={MAIN_AGENT_MODEL} | messages={len(loop_messages)}", flush=True)

        turn_text = ""
        tool_use_blocks = []
        current_tool = None
        stop_reason = None

        system_blocks = _build_system_prompt(session, video_watch_status)

        with anthropic_client.beta.messages.stream(
            model=MAIN_AGENT_MODEL,
            max_tokens=MAX_TOKENS_RESPONSE,
            temperature=TEMP_MAIN_AGENT,
            system=system_blocks,
            tools=TOOL_SCHEMAS,
            messages=loop_messages,
            betas=["prompt-caching-2024-07-31"],
        ) as stream:

            for event in stream:
                etype = event.type

                if etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        turn_text += delta.text
                        emit("token", {"text": delta.text})
                    elif delta.type == "input_json_delta" and current_tool:
                        current_tool["input_str"] += delta.partial_json

                elif etype == "content_block_start":
                    if event.content_block.type == "tool_use":
                        block = event.content_block
                        current_tool = {"id": block.id, "name": block.name, "input_str": ""}

                elif etype == "content_block_stop":
                    if current_tool is not None:
                        tool_use_blocks.append(current_tool)
                        current_tool = None

                elif etype == "message_delta":
                    if hasattr(event.delta, "stop_reason"):
                        stop_reason = event.delta.stop_reason

        print(f"[MainAgent] stop_reason={stop_reason} | tools_requested={len(tool_use_blocks)}", flush=True)

        # No tools requested — we're done
        if stop_reason == "end_turn" or not tool_use_blocks:
            full_text += turn_text
            if SIGNAL_IMMEDIATE_PROCEED in full_text:
                print("[MainAgent] >> IMMEDIATE_PROCEED emitted", flush=True)
            elif SIGNAL_READY_TO_PROCEED in full_text:
                print("[MainAgent] >> READY_TO_PROCEED emitted", flush=True)
            return full_text, tool_calls_made

        # Any text the model wrote before calling tools
        if turn_text.strip():
            print(f"[MainAgent] thinking: {turn_text.strip()[:200]}", flush=True)
            full_text += turn_text

        # Build the assistant turn message (text + tool calls)
        assistant_content = []
        if turn_text:
            assistant_content.append({"type": "text", "text": turn_text})
        for tb in tool_use_blocks:
            assistant_content.append({
                "type": "tool_use",
                "id": tb["id"],
                "name": tb["name"],
                "input": _parse_tool_input(tb["input_str"]),
            })
        loop_messages.append({"role": "assistant", "content": assistant_content})

        # Run all requested tools in parallel
        with ThreadPoolExecutor(max_workers=len(tool_use_blocks)) as pool:
            tool_results = list(pool.map(
                lambda tb: _execute_tool_block(tb, session, course_id, tool_calls_made, emit),
                tool_use_blocks,
            ))

        # If the level assessment just started, hand off immediately
        for tr in tool_results:
            content = tr.get("content", "")
            if content.startswith("[LEVEL_ASSESSMENT_STARTED]\n"):
                first_question = content[len("[LEVEL_ASSESSMENT_STARTED]\n"):]
                emit("token", {"text": first_question})
                return full_text + first_question, tool_calls_made

        loop_messages.append({"role": "user", "content": tool_results})
        emit("status", {"text": "Picking up where we left off…"})


def chat(session: Session, course_id: str, user_message: str, emit=None, video_watch_status="no_video") -> dict:
    if emit is None:
        def emit(event, data):
            if event == "token":
                print(data["text"], end="", flush=True)
            elif event in ("tool_start", "status"):
                print(f"\n  [{data.get('label') or data.get('text', '')}]", flush=True)

    session.add_message("user", user_message)

    messages = _build_messages(session)
    response_text, tools_used = _run_streaming_loop(session, course_id, messages, emit, video_watch_status)

    session.add_message("assistant", response_text)

    immediate_proceed = SIGNAL_IMMEDIATE_PROCEED in response_text
    ready_to_proceed = (not immediate_proceed) and (SIGNAL_READY_TO_PROCEED in response_text)
    display_text = strip_signals(response_text)

    return {
        "response": display_text,
        "raw_response": response_text,
        "ready_to_proceed": ready_to_proceed,
        "immediate_proceed": immediate_proceed,
        "tools_used": tools_used,
    }
