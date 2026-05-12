"""
FastAPI server — Teaching Assistant POC
Run: uvicorn server:app --reload --port 8000
"""

import json
import uuid
import asyncio
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR   = Path(__file__).parent
SLIDES_DIR = BASE_DIR / "Cash-Flow-Training for akash"

app = FastAPI(title="Teaching Assistant POC", root_path="/kairos")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/src", StaticFiles(directory=BASE_DIR / "src"), name="src")


@app.get("/slides/{filename}")
async def get_slide(filename: str):
    path = SLIDES_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Slide not found: {filename}")
    return FileResponse(str(path))


_sessions: dict = {}  # session_id -> Session (RAM cache per user)


def _display_messages_from_history(full_history: list) -> list:
    return [
        m for m in full_history
        if isinstance(m.get("content"), str) and m["content"] != "[slide loaded]"
    ]


def load_session(session_id: str, course_id: str):
    """Return session from RAM cache; load from MongoDB on first hit or after restart."""
    if session_id in _sessions:
        return _sessions[session_id]

    from session import Session
    from mongo import build_course_items, rag_search, load_course_session

    doc = load_course_session(session_id)
    if not doc:
        return None

    items = build_course_items(course_id)

    sess = Session(session_id=session_id)
    sess.user_name               = doc.get("user_name", "")
    sess.user_role               = doc.get("user_role", "")
    sess.user_description        = doc.get("user_description", "")
    sess.user_skillsets          = doc.get("user_skillsets", [])
    sess.user_level              = doc.get("user_level", "")
    sess.user_tactics            = doc.get("user_tactics", {})
    sess.history_summary         = doc.get("history_summary", "")
    sess.level_assessment_active = doc.get("level_assessment_active", False)

    full_history = doc.get("history", [])
    sess.full_history    = full_history
    sess.recent_messages = full_history[-5:] if sess.history_summary else list(full_history)

    saved_statuses = doc.get("slide_statuses", {})
    sess.slide_statuses = saved_statuses
    sess.course_items   = items
    for item in sess.course_items:
        if item["id"] in saved_statuses:
            item["status"] = saved_statuses[item["id"]]

    saved_item_id = doc.get("current_item_id", "")
    current_item  = next((i for i in items if i["id"] == saved_item_id), None) or (items[0] if items else None)
    if current_item:
        sess.current_item_id = current_item["id"]
        from tools.tool_executor import condense_query_for_rag
        rag_context = rag_search(
            query=condense_query_for_rag(f"{current_item['title']} {current_item.get('description', '')}"),
            course_id=course_id,
        )
        sess.current_content_context = f"{current_item['title']}\n\n{current_item.get('description', '')}"
        sess.current_rag_context     = rag_context

    _sessions[session_id] = sess
    print(f"[Server] session loaded | session_id='{session_id}' | history={len(full_history)} msgs", flush=True)
    return sess


# ── Request / response models ─────────────────────────────────────────────────

class NewSessionRequest(BaseModel):
    course_id:        str
    session_id:       str = ""   # pass existing session_id to resume
    user_name:        str = ""
    user_role:        str = ""
    user_skillsets:   list = []
    user_description: str = ""

class NewSessionResponse(BaseModel):
    session_id:   str
    course_items: list
    video_url:    str = ""
    image_url:    str = ""

class ChatRequest(BaseModel):
    session_id: str
    course_id:  str
    message:    str

class NavigateRequest(BaseModel):
    session_id: str
    course_id:  str
    item_index: int


# ── HTML ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=(BASE_DIR / "index.html").read_text(encoding="utf-8"))


# ── Session endpoints ─────────────────────────────────────────────────────────

@app.post("/api/session/new", response_model=NewSessionResponse)
async def new_session(req: NewSessionRequest):
    from session import Session
    from mongo import build_course_items, rag_search, save_user_profile, save_course_session
    from tools.tool_executor import condense_query_for_rag
    from config import VIDEO_URL, IMAGE_URL

    session_id = req.session_id.strip() or str(uuid.uuid4())[:8]

    # Try to load existing session
    sess = load_session(session_id, req.course_id)

    if sess is None:
        # Brand new — create user + session
        items = build_course_items(req.course_id)
        if not items:
            raise HTTPException(status_code=404, detail=f"Course '{req.course_id}' not found.")

        sess = Session(session_id=session_id)
        sess.user_name        = req.user_name
        sess.user_role        = req.user_role
        sess.user_skillsets   = req.user_skillsets
        sess.user_description = req.user_description
        sess.course_items     = items

        first = items[0]
        sess.current_item_id             = first["id"]
        first["status"]                  = "ongoing"
        sess.slide_statuses[first["id"]] = "ongoing"

        rag_context = rag_search(
            query=condense_query_for_rag(f"{first['title']} {first['description']}"),
            course_id=req.course_id,
        )
        sess.current_content_context = f"{first['title']}\n\n{first['description']}"
        sess.current_rag_context     = rag_context

        if req.user_name or req.user_role:
            save_user_profile(req.course_id, session_id, {
                "name":        req.user_name,
                "role":        req.user_role,
                "skillsets":   req.user_skillsets,
                "description": req.user_description,
            })

        _sessions[session_id] = sess
        save_course_session(sess)
        print(f"[Server] session created | session_id='{session_id}'", flush=True)
    else:
        print(f"[Server] session resumed | session_id='{session_id}' | history={len(sess.full_history)} msgs", flush=True)

    ui_items = [
        {
            "id":          i["id"],
            "type":        i["type"],
            "title":       i["title"],
            "image_name":  i.get("image_name", ""),
            "video_name":  i["id"],
            "status":      i["status"],
            "description": i.get("description", ""),
        }
        for i in sess.course_items
    ]

    return {
        "session_id":   session_id,
        "course_items": ui_items,
        "video_url":    VIDEO_URL.rstrip("/"),
        "image_url":    IMAGE_URL.rstrip("/"),
    }


@app.get("/api/session/{session_id}/state")
async def session_state(session_id: str, course_id: str):
    from mongo import load_course_session, build_course_items
    doc = load_course_session(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    history = doc.get("history", [])
    saved_statuses = doc.get("slide_statuses", {})
    items = build_course_items(course_id)
    for item in items:
        if item["id"] in saved_statuses:
            item["status"] = saved_statuses[item["id"]]
    current_item = next((i for i in items if i["id"] == doc.get("current_item_id")), None)
    return {
        "messages":     _display_messages_from_history(history),
        "current_item": current_item,
    }


@app.get("/api/session/{session_id}/messages")
async def get_session_messages(session_id: str):
    from mongo import load_course_session
    doc = load_course_session(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    history = doc.get("history", [])
    return {"messages": _display_messages_from_history(history)}



@app.post("/api/advance")
async def advance(req: NavigateRequest):
    from mongo import rag_search, save_course_session
    from tools.tool_executor import condense_query_for_rag

    sess = load_session(req.session_id, req.course_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    sess.advance_to_next()
    item = sess.get_current_item()

    if item:
        rag_context = rag_search(
            query=condense_query_for_rag(f"{item['title']} {item['description']}"),
            course_id=req.course_id,
        )
        sess.current_content_context = f"{item['title']}\n\n{item['description']}"
        sess.current_rag_context     = rag_context

    threading.Thread(target=save_course_session, args=(sess,), daemon=True).start()
    return {"ok": True, "current_item": item}


# ── Course wrap (end of course) ───────────────────────────────────────────────

@app.get("/api/session/{session_id}/wrap")
async def get_wrap(session_id: str, course_id: str):
    from agents.observer_agent import generate_wrap
    from mongo import save_course_session

    sess = load_session(session_id, course_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    loop = asyncio.get_event_loop()
    wrap = await loop.run_in_executor(None, lambda: generate_wrap(sess))
    save_course_session(sess)

    print(f"[Server] wrap generated for session='{session_id}'", flush=True)
    return {"wrap": wrap}


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

async def _drain_event_queue(event_q: queue.Queue, on_done=None):
    from agents.status_agent import StatusAgent, _TOOL_POOL_MAP
    # Start cycling "normal" status immediately — stops on first token
    status_agent = StatusAgent()
    status_phrase_q = status_agent.queue
    status_agent.start_queued("normal")

    while True:
        # Drain any cycling status phrases first
        if status_phrase_q:
            try:
                while True:
                    phrase = status_phrase_q.get_nowait()
                    yield _sse("status", {"text": phrase})
            except queue.Empty:
                pass

        try:
            event_name, data = event_q.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue

        if event_name == "token":
            if status_agent:
                status_agent.stop()
                status_agent = None
                status_phrase_q = None
            yield _sse("token", {"text": data["text"]})

        elif event_name == "tool_start":
            tool = data.get("tool", "")
            pool_key = _TOOL_POOL_MAP.get(tool, "thinking")
            status_agent = StatusAgent()
            status_phrase_q = status_agent.queue
            status_agent.start_queued(pool_key)

        elif event_name == "tool_done":
            if status_agent:
                status_agent.stop()
                status_agent = None
                status_phrase_q = None

        elif event_name == "status":
            yield _sse("status", {"text": data.get("text", "")})

        elif event_name == "done":
            from config import SIGNAL_READY_TO_PROCEED

            if "response" in data:
                reply = data["response"]
                ready = data["ready_to_proceed"]
            else:
                reply = data.get("text", "")
                ready = data.get("ready_to_proceed", SIGNAL_READY_TO_PROCEED in reply)
                reply = reply.replace(SIGNAL_READY_TO_PROCEED, "").strip()

            # Strip internal level-assessment signal if it leaked into display text
            reply = reply.replace("[LEVEL_ASSESSMENT_STARTED]", "").replace("[LEVEL_ALREADY_SET]", "").strip()

            if on_done:
                on_done(data)
            yield _sse("result", {"ready_to_proceed": ready})
            return

        elif event_name == "error":
            yield _sse("error", {"message": data.get("message", "Unknown error")})
            return


# ── Chat SSE ──────────────────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat_sse(req: ChatRequest):
    sess = load_session(req.session_id, req.course_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    if sess.level_assessment_active:
        from agents.level_agent import level_answer

        async def level_chat():
            loop = asyncio.get_event_loop()
            sess.add_message("user", req.message)
            result = await loop.run_in_executor(None, lambda: level_answer(sess, req.message))
            reply  = result["response"]
            done   = result["level_complete"]

            if reply:
                sess.add_message("assistant", reply)
                yield _sse("token", {"text": reply})

            from mongo import save_course_session, save_user_profile
            if done:
                save_user_profile(req.course_id, req.session_id, {
                    "user_level":   sess.user_level,
                    "user_tactics": sess.user_tactics,
                })
                yield _sse("level_complete", {"level": sess.user_level, "tactics": sess.user_tactics})

            threading.Thread(target=save_course_session, args=(sess,), daemon=True).start()
            yield _sse("result", {"ready_to_proceed": False})

        return StreamingResponse(level_chat(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    from agents.main_agent import chat_threaded
    event_q = chat_threaded(sess, req.course_id, req.message)

    return StreamingResponse(
        _drain_event_queue(event_q),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
