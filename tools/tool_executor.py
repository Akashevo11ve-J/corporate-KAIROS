import time
import json
from anthropic import Anthropic
from config import WORKER_MODEL, MAX_TOKENS_WORKER, TEMP_WORKER
from prompts import WORKER_SYSTEM, WORKER_SUMMARISE_HISTORY, WORKER_CONDENSE_RAG_QUERY

client = Anthropic()


def _worker_call(prompt: str, task_label: str = "generic") -> str:
    print(f"[WorkerAgent] task='{task_label}' | model={WORKER_MODEL}", flush=True)
    print(f"[WorkerAgent] prompt ({len(prompt)} chars): {prompt[:300]}", flush=True)

    t0 = time.time()
    response = client.messages.create(
        model=WORKER_MODEL,
        max_tokens=MAX_TOKENS_WORKER,
        temperature=TEMP_WORKER,
        system=WORKER_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    )
    elapsed = int((time.time() - t0) * 1000)
    result  = response.content[0].text.strip()

    print(f"[WorkerAgent] done ({elapsed}ms) | result ({len(result)} chars): {result[:300]}", flush=True)
    return result


def condense_query_for_rag(raw_query: str) -> str:
    """
    Use the worker LLM to compress a verbose slide description or user question
    into a tight, keyword-focused search query for Pinecone.
    """
    print("query sent to llm",raw_query)
    prompt = WORKER_CONDENSE_RAG_QUERY.format(raw_query=raw_query)
    condensed = _worker_call(prompt, task_label="condense_rag_query")
    print(f"[RAGQuery] condensed: '{condensed}'", flush=True)
    return condensed


def execute_tool(tool_name: str, tool_input: dict, session, course_id: str) -> str:
    """
    Execute a tool call from the main agent.
    session  — the Session object (for state mutations like user_role)
    course_id — passed through for mongo/rag calls
    """

    if tool_name == "rag_search":
        from mongo import rag_search
        raw_query = tool_input["query"]
        top_k     = min(int(tool_input.get("top_k", 3)), 7)  # agent can request up to 7
        query     = condense_query_for_rag(raw_query)
        result    = rag_search(query, course_id, top_k=top_k)

    elif tool_name == "fetch_slide_content":
        from mongo import fetch_item_description
        item_id = tool_input["item_id"]
        print(f"[ToolExecutor] fetch_slide_content | item_id='{item_id}'", flush=True)

        # Block access to upcoming slides — only current/ongoing/completed allowed
        allowed = {i["id"] for i in session.course_items if i.get("status") in ("ongoing", "completed", "current")}
        if item_id not in allowed:
            print(f"[ToolExecutor] BLOCKED fetch_slide_content — '{item_id}' is upcoming", flush=True)
            result = f"That slide hasn't been reached yet. Stay with the current content."
        else:
            desc = fetch_item_description(course_id, item_id)
            result = f"[Slide: {item_id}]\n\n{desc}" if desc else f"No content found for '{item_id}'."

    elif tool_name == "clip_and_transcribe":
        result = _clip_and_transcribe(
            tool_input["video_id"],
            tool_input["start_time"],
            tool_input["end_time"],
            tool_input["what_user_wants"],
            course_id,
        )

    elif tool_name == "transcribe_full_video":
        video_id   = tool_input["video_id"]
        video_name = _resolve_video_name(course_id, video_id)
        print(f"[ToolExecutor] transcribe_full_video | video_id='{video_id}' | file='{video_name}'", flush=True)
        from tools.video_processor import transcribe_full_video as _transcribe_full
        raw    = _transcribe_full(video_name)
        result = (
            f"[Full Video Transcript: {video_id}]\n"
            f"Each line is one sentence with its exact timestamp in [MM:SS–MM:SS] format.\n\n"
            f"{raw}"
        )

    elif tool_name == "extract_frame":
        result = _extract_frame(
            tool_input["video_id"],
            tool_input["timestamp"],
            tool_input["what_to_look_for"],
            course_id,
        )


    elif tool_name == "summarise_history":
        print(f"[ToolExecutor] summarise_history | tokens={session.get_recent_token_count()}", flush=True)
        summary = _worker_call(
            WORKER_SUMMARISE_HISTORY.format(history_text=session.get_history_text()),
            task_label="summarise_history"
        )
        session.apply_compression(summary)
        result = f"History compressed.\n\nSummary:\n{summary}"

    elif tool_name == "assess_user_level":
        if session.user_level:
            # Already assessed — guard against double-call
            result = f"[LEVEL_ALREADY_SET] User level is '{session.user_level}'. Tactics: {session.user_tactics}. Do not call this again. IMPORTANT: Check the conversation history — you may have already introduced this slide or video before the assessment. Do NOT re-explain or re-introduce content you already covered. Pick up exactly where the conversation left off."
        else:
            from agents.level_agent import start_level_assessment
            user_context = tool_input.get("user_context", "")
            first_question = start_level_assessment(session, user_context)
            result = f"[LEVEL_ASSESSMENT_STARTED]\n{first_question}"

    elif tool_name == "observe_learner":
        from agents.observer_agent import get_observer_advice
        result = get_observer_advice(session)

    else:
        print(f"[ToolExecutor] ERROR: unknown tool '{tool_name}'", flush=True)
        result = f"Unknown tool: {tool_name}"

    return result


def _resolve_video_name(course_id: str, video_id: str) -> str:
    """
    Look up the actual filename for a video from the course-data collection.
    Returns the slugified filename with .mp4 extension (matching S3 key format).
    Falls back to using video_id directly if not found.
    """
    from mongo import _build_description_index
    index = _build_description_index(course_id)
    entry = index.get(video_id, {})
    # Prefer explicit video_name field; fall back to title
    raw = entry.get("video_name", "") or entry.get("title", "") or video_id
    slug = raw.replace(" ", "_").lower()
    return slug if slug.endswith(".mp4") else slug + ".mp4"


def _clip_and_transcribe(video_id: str, start_time: str, end_time: str,
                          what_user_wants: str, course_id: str) -> str:
    video_name = _resolve_video_name(course_id, video_id)
    print(f"[ToolExecutor] clip_and_transcribe | video='{video_name}' | {start_time}→{end_time}", flush=True)
    from tools.video_processor import clip_and_transcribe as _clip
    raw = _clip(video_name, start_time, end_time)
    return (
        f"[Segment Transcript: {start_time} → {end_time}]\n"
        f"Each line is one sentence with its exact timestamp in [MM:SS–MM:SS] format.\n\n"
        f"{raw}"
    )


def _extract_frame(video_id: str, timestamp: str, what_to_look_for: str, course_id: str) -> str:
    video_name = _resolve_video_name(course_id, video_id)
    print(f"[ToolExecutor] extract_frame | video='{video_name}' | t={timestamp}", flush=True)
    from tools.video_processor import extract_frame as _frame
    description = _frame(video_name, timestamp, what_to_look_for)
    return f"[Frame at {timestamp}]\n\n{description}"
