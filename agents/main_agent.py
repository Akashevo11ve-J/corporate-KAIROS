import json
import threading
import queue as _queue
from concurrent.futures import ThreadPoolExecutor
from anthropic import Anthropic
from config import MAIN_AGENT_MODEL, MAX_TOKENS_RESPONSE, SIGNAL_READY_TO_PROCEED, TEMP_MAIN_AGENT
from prompts import MAIN_AGENT_SYSTEM, get_level_guidance
from session import Session
from tools.tool_schemas import TOOL_SCHEMAS, TOOL_STATUS_MAP
from tools.tool_executor import execute_tool

client = Anthropic()


def _build_system_prompt(session: Session) -> str:
    current_item  = session.get_current_item() or {}
    skillsets_str = ", ".join(session.user_skillsets) if session.user_skillsets else "not specified"
    print(session.current_content_context)
    prompt = MAIN_AGENT_SYSTEM.format(
        user_name=session.user_name,
        user_role=session.user_role,
        user_description=session.user_description,
        user_skillsets=skillsets_str,
        user_level_guidance=get_level_guidance(session.user_level, session.user_tactics),
        current_content_title=current_item.get("title", ""),
        content_type=current_item.get("type", "slide"),
        course_progress_json=session.get_course_progress_json(),
        current_content_context=session.current_content_context,
        ready_signal=SIGNAL_READY_TO_PROCEED,
    )


    return prompt


def _build_messages(session: Session) -> list:
    messages = []
    if session.history_summary:
        messages.append({
            "role":    "user",
            "content": f"[CONVERSATION SUMMARY SO FAR]\n{session.history_summary}"
        })
        messages.append({
            "role":    "assistant",
            "content": "Understood — I have the context from our earlier conversation."
        })
    for msg in session.recent_messages:
        messages.append(msg)
    return messages


def _run_streaming_loop(session: Session, course_id: str, messages: list, emit) -> tuple[str, list]:
    tool_calls_made = []
    loop_messages   = list(messages)
    full_text       = ""

    while True:
        print(f"[MainAgent] API call | model={MAIN_AGENT_MODEL} | messages={len(loop_messages)}", flush=True)

        turn_text       = ""
        tool_use_blocks = []
        current_tool    = None
        stop_reason     = None

        with client.messages.stream(
            model=MAIN_AGENT_MODEL,
            max_tokens=MAX_TOKENS_RESPONSE,
            temperature=TEMP_MAIN_AGENT,
            system=_build_system_prompt(session),
            tools=TOOL_SCHEMAS,
            messages=loop_messages,
        ) as stream:

            for event in stream:
                etype = event.type

                if etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        chunk = delta.text
                        turn_text += chunk
                        emit("token", {"text": chunk})
                    elif delta.type == "input_json_delta" and current_tool:
                        current_tool["input_str"] += delta.partial_json

                elif etype == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool = {"id": block.id, "name": block.name, "input_str": ""}

                elif etype == "content_block_stop":
                    if current_tool is not None:
                        tool_use_blocks.append(current_tool)
                        current_tool = None

                elif etype == "message_delta":
                    if hasattr(event.delta, "stop_reason"):
                        stop_reason = event.delta.stop_reason

        print(f"[MainAgent] stop_reason={stop_reason} | tools_requested={len(tool_use_blocks)}", flush=True)

        if stop_reason == "end_turn" or not tool_use_blocks:
            full_text += turn_text
            if SIGNAL_READY_TO_PROCEED in full_text:
                print("[MainAgent] >> READY_TO_PROCEED emitted", flush=True)
            return full_text, tool_calls_made

        # Thinking text before tool calls
        if turn_text.strip():
            print(f"[MainAgent] thinking: {turn_text.strip()[:200]}", flush=True)
            full_text += turn_text

        # Build assistant message with tool use blocks
        assistant_content = []
        if turn_text:
            assistant_content.append({"type": "text", "text": turn_text})
        for tb in tool_use_blocks:
            try:
                parsed = json.loads(tb["input_str"]) if tb["input_str"] else {}
            except Exception:
                parsed = {}
            assistant_content.append({
                "type":  "tool_use",
                "id":    tb["id"],
                "name":  tb["name"],
                "input": parsed,
            })
        loop_messages.append({"role": "assistant", "content": assistant_content})

        # Execute tools in parallel
        def _run_one(tb):
            try:
                inp = json.loads(tb["input_str"]) if tb["input_str"] else {}
            except Exception:
                inp = {}
            tool_name = tb["name"]
            tool_calls_made.append(tool_name)
            print(f"[MainAgent] >> tool: {tool_name} | input: {json.dumps(inp, ensure_ascii=False)}", flush=True)
            emit("tool_start", {"tool": tool_name, "label": TOOL_STATUS_MAP.get(tool_name, "thinking")})
            result = execute_tool(tool_name, inp, session, course_id)
            emit("tool_done", {"tool": tool_name})
            print(f"[MainAgent]    result {tool_name} ({len(result)} chars): {result[:300]}", flush=True)
            return {"type": "tool_result", "tool_use_id": tb["id"], "content": result}

        with ThreadPoolExecutor(max_workers=len(tool_use_blocks)) as pool:
            tool_results = list(pool.map(_run_one, tool_use_blocks))

        # If a handoff agent was started, stream its first message and exit immediately
        for tr in tool_results:
            content = tr.get("content", "")
            if content.startswith("[LEVEL_ASSESSMENT_STARTED]\n"):
                first_question = content[len("[LEVEL_ASSESSMENT_STARTED]\n"):]
                emit("token", {"text": first_question})
                return full_text + first_question, tool_calls_made

        loop_messages.append({"role": "user", "content": tool_results})
        emit("status", {"text": "Picking up where we left off…"})

def chat(session: Session, course_id: str, user_message: str, emit=None) -> dict:
    if emit is None:
        def emit(event, data):
            if event == "token":
                print(data["text"], end="", flush=True)
            elif event in ("tool_start", "status"):
                print(f"\n  [{data.get('label') or data.get('text', '')}]", flush=True)

    session.add_message("user", user_message)

    token_count = session.get_recent_token_count()
    print(f"[MainAgent] history tokens={token_count} | threshold={1000} | compress={token_count > 1000}", flush=True)

    if session.needs_compression():
        from tools.tool_executor import _worker_call
        from prompts import WORKER_SUMMARISE_HISTORY
        summary = _worker_call(
            WORKER_SUMMARISE_HISTORY.format(history_text=session.get_history_text()),
            task_label="auto_compress"
        )
        session.apply_compression(summary)

    messages = _build_messages(session)
    response_text, tools_used = _run_streaming_loop(session, course_id, messages, emit)

    session.add_message("assistant", response_text)
    ready_to_proceed = SIGNAL_READY_TO_PROCEED in response_text
    display_text     = response_text.replace(SIGNAL_READY_TO_PROCEED, "").strip()

    # Save session to MongoDB
    def _save():
        from mongo import save_course_session
        save_course_session(session)

    threading.Thread(target=_save, daemon=True).start()

    return {
        "response":         display_text,
        "raw_response":     response_text,
        "ready_to_proceed": ready_to_proceed,
        "tools_used":       tools_used,
    }


# ── Threaded variants for server.py (FastAPI stays non-blocking) ──────────────

def chat_threaded(session: Session, course_id: str, user_message: str) -> _queue.Queue:
    event_q = _queue.Queue()

    def emit(event, data):
        event_q.put((event, data))

    def run():
        try:
            result = chat(session, course_id, user_message, emit=emit)
            event_q.put(("done", result))
        except Exception as e:
            event_q.put(("error", {"message": str(e)}))

    threading.Thread(target=run, daemon=True).start()
    return event_q


