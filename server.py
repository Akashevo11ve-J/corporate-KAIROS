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

from config import VIDEO_URL, IMAGE_URL, SIGNAL_READY_TO_PROCEED
from mongo import load_course_session, build_course_items
from mongo import save_user_profile
from agents.observer_agent import generate_wrap
from agents.level_agent import level_answer
from agents.main_agent import chat as agent_chat
from agents.status_agent import StatusAgent, _TOOL_POOL_MAP
from context_loader import load_context, new_context
from store import persist_session

BASE_DIR   = Path(__file__).parent
SLIDES_DIR = BASE_DIR / "Cash-Flow-Training for akash"

app = FastAPI(title="Teaching Assistant POC")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/src", StaticFiles(directory=BASE_DIR / "src"), name="src")


def _display_messages_from_history(full_history: list) -> list:
    return [
        m for m in full_history
        if isinstance(m.get("content"), str) and m["content"] != "[slide loaded]"
    ]


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
    session_id = req.session_id.strip() or str(uuid.uuid4())[:8]

    sess = load_context(session_id, req.course_id)

    if sess is None:
        sess = new_context(
            session_id=session_id,
            course_id=req.course_id,
            user_name=req.user_name,
            user_role=req.user_role,
            user_skillsets=req.user_skillsets,
            user_description=req.user_description,
        )
        if not sess.course_items:
            raise HTTPException(status_code=404, detail=f"Course '{req.course_id}' not found.")

        if req.user_name or req.user_role:
            save_user_profile(req.course_id, session_id, {
                "name": req.user_name, "role": req.user_role,
                "skillsets": req.user_skillsets, "description": req.user_description,
            })

        persist_session(sess)
        print(f"[Server] session created | session_id='{session_id}'", flush=True)
    else:
        print(f"[Server] session resumed | session_id='{session_id}' | recent={len(sess.recent_messages)} msgs", flush=True)

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
    doc = load_course_session(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    history = doc.get("history", [])
    return {"messages": _display_messages_from_history(history)}



@app.post("/api/advance")
async def advance(req: NavigateRequest):
    sess = load_context(req.session_id, req.course_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    sess.advance_to_next()
    item = sess.get_current_item()
    if item:
        sess.set_current_item(item)

    persist_session(sess, background=True)
    return {"ok": True, "current_item": item}


# ── Course wrap (end of course) ───────────────────────────────────────────────

@app.get("/api/session/{session_id}/wrap")
async def get_wrap(session_id: str, course_id: str):
    sess = load_context(session_id, course_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    loop = asyncio.get_event_loop()
    wrap = await loop.run_in_executor(None, lambda: generate_wrap(sess))
    persist_session(sess)

    print(f"[Server] wrap generated for session='{session_id}'", flush=True)
    return {"wrap": wrap}


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

async def _drain_event_queue(event_q: queue.Queue, on_done=None):
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
    event_q = queue.Queue()

    def emit(event, data):
        event_q.put((event, data))

    def run():
        try:
            sess = load_context(req.session_id, req.course_id, emit=emit)
            if not sess:
                event_q.put(("error", {"message": "Session not found"}))
                return

            if sess.level_assessment_active:
                sess.add_message("user", req.message)
                result = level_answer(sess, req.message)
                reply  = result["response"]
                done   = result["level_complete"]

                if reply:
                    sess.add_message("assistant", reply)
                    emit("token", {"text": reply})

                if done:
                    save_user_profile(req.course_id, req.session_id, {
                        "user_level": sess.user_level, "user_tactics": sess.user_tactics,
                    })
                    emit("level_complete", {"level": sess.user_level, "tactics": sess.user_tactics})

                persist_session(sess, background=True)
                emit("done", {"response": "", "ready_to_proceed": False})
                return

            result = agent_chat(sess, req.course_id, req.message, emit=emit)
            persist_session(sess, background=True)
            event_q.put(("done", result))

        except Exception as e:
            event_q.put(("error", {"message": str(e)}))

    threading.Thread(target=run, daemon=True).start()

    return StreamingResponse(
        _drain_event_queue(event_q),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
