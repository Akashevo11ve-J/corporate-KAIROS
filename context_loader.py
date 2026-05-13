import threading
from concurrent.futures import ThreadPoolExecutor

import tiktoken

from config import HISTORY_TOKEN_THRESHOLD
from mongo import build_course_items_and_wrap, load_course_session, get_db
from session import Session


# ── Process-level course cache (course structure never changes) ───────────────
_course_cache: dict = {}
_course_cache_lock  = threading.Lock()


def _get_course_data(course_id: str) -> tuple[list, str]:
    with _course_cache_lock:
        if course_id in _course_cache:
            return _course_cache[course_id]
    items, wrap = build_course_items_and_wrap(course_id)
    with _course_cache_lock:
        _course_cache[course_id] = (items, wrap)
    return items, wrap


# ── Token counting ────────────────────────────────────────────────────────────

def _count_tokens(messages: list, prefix: str = "") -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    parts = [prefix] if prefix else []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict):
                    parts.append(block.get("text", "") or block.get("content", ""))
    return len(enc.encode(" ".join(parts)))


# ── History: find compression marker, split into summary + recent ─────────────

def _split_history(history: list) -> tuple[str, list]:
    """
    Scan history from the end for the latest 'compressed' marker.
    Returns (summary_str, recent_messages_after_marker).
    If no marker found, summary is "" and recent = full history.
    """
    for i in range(len(history) - 1, -1, -1):
        if "compressed" in history[i]:
            summary        = history[i]["compressed"]
            recent         = history[i + 1:]
            return summary, recent
    return "", list(history)


def save_compression_marker(session_id: str, old_marker_idx, new_marker_idx: int, summary: str) -> None:
    col = get_db()["course-session"]

    if old_marker_idx is not None:
        col.update_one(
            {"session_id": session_id},
            {"$unset": {f"history.{old_marker_idx}.compressed": ""}}
        )

    col.update_one(
        {"session_id": session_id},
        {"$set": {f"history.{new_marker_idx}.compressed": summary}}
    )
    print(f"[ContextLoader] compression saved | marker_idx={new_marker_idx}", flush=True)


def _compress_if_needed(session_id: str, history: list,
                         summary: str, recent: list,
                         emit=None) -> tuple[str, list]:
    """
    Check token count. If over threshold, compress and persist marker to DB.
    Returns updated (summary, recent).
    """
    token_count = _count_tokens(recent, prefix=summary)
    print(f"[ContextLoader] tokens={token_count} | threshold={HISTORY_TOKEN_THRESHOLD}", flush=True)

    if token_count <= HISTORY_TOKEN_THRESHOLD:
        return summary, recent

    # Signal UI
    if emit:
        emit("tool_start", {"tool": "summarise_history", "label": "summarise"})

    from tools.tool_executor import _worker_call
    from prompts import WORKER_SUMMARISE_HISTORY

    # Build text: previous summary + recent messages
    history_lines = []
    if summary:
        history_lines.append(f"PREVIOUS SUMMARY:\n{summary}")
    for m in recent:
        c = m.get("content", "")
        if isinstance(c, str) and c != "[slide loaded]":
            history_lines.append(f"{m['role'].upper()}: {c}")
    history_text = "\n".join(history_lines)

    new_summary = _worker_call(
        WORKER_SUMMARISE_HISTORY.format(history_text=history_text),
        task_label="auto_compress"
    )

    # Find old marker index
    old_marker_idx = None
    for i in range(len(history) - 1, -1, -1):
        if "compressed" in history[i]:
            old_marker_idx = i
            break

    # New marker = index of last message in recent batch
    # recent messages start right after old marker (or from 0)
    start_of_recent = (old_marker_idx + 1) if old_marker_idx is not None else 0
    new_marker_idx  = start_of_recent + len(recent) - 1

    threading.Thread(
        target=save_compression_marker,
        args=(session_id, old_marker_idx, new_marker_idx, new_summary),
        daemon=True,
    ).start()

    # Keep last 5 recent messages as the fresh window
    fresh_recent = recent[-5:]

    if emit:
        emit("tool_done", {"tool": "summarise_history"})

    print(f"[ContextLoader] compressed | old_marker={old_marker_idx} new_marker={new_marker_idx}", flush=True)
    return new_summary, fresh_recent


# ── Main entry point ──────────────────────────────────────────────────────────

def load_context(session_id: str, course_id: str, emit=None) -> Session | None:
    """
    Load everything needed for one request in parallel.
    Returns a fully assembled Session, or None if session_id not found.
    compress if history is over threshold — shows loader via emit.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_doc   = pool.submit(load_course_session, session_id)
        f_items = pool.submit(_get_course_data, course_id)
        doc              = f_doc.result()
        items, course_wrap = f_items.result()

    if not doc:
        return None

    # Split history into summary + recent using compression marker
    raw_history          = doc.get("history", [])
    summary, recent      = _split_history(raw_history)

    # Compress if needed — saves marker to DB in background thread
    summary, recent = _compress_if_needed(
        session_id, raw_history, summary, recent, emit=emit
    )

    # Assemble Session — lives only for this request
    sess = Session(session_id=session_id)
    sess.user_name               = doc.get("user_name", "")
    sess.user_role               = doc.get("user_role", "")
    sess.user_description        = doc.get("user_description", "")
    sess.user_skillsets          = doc.get("user_skillsets", [])
    sess.user_level              = doc.get("user_level", "")
    sess.user_tactics            = doc.get("user_tactics", {})
    sess.level_assessment_active = doc.get("level_assessment_active", False)
    sess.course_wrap             = course_wrap
    sess.course_items            = items
    sess.recent_messages         = recent
    sess.full_history            = raw_history
    sess.history_summary         = summary

    saved_statuses = doc.get("slide_statuses", {})
    sess.slide_statuses = saved_statuses
    sess.apply_slide_statuses(saved_statuses)

    current_item = sess.find_current_item(doc.get("current_item_id", ""))
    if current_item:
        sess.set_current_item(current_item)

    print(f"[ContextLoader] ready | session='{session_id}' | recent={len(recent)} msgs | summary={'yes' if summary else 'no'}", flush=True)
    return sess


def new_context(session_id: str, course_id: str,
                user_name: str = "", user_role: str = "",
                user_skillsets: list = None,
                user_description: str = "") -> Session:
    """Build a brand-new Session for a first-time user."""
    items, course_wrap = _get_course_data(course_id)

    sess = Session(session_id=session_id)
    sess.user_name        = user_name
    sess.user_role        = user_role
    sess.user_skillsets   = user_skillsets or []
    sess.user_description = user_description
    sess.course_items     = items
    sess.course_wrap      = course_wrap

    if items:
        first = items[0]
        first["status"] = "ongoing"
        sess.slide_statuses[first["id"]] = "ongoing"
        sess.set_current_item(first)

    return sess
