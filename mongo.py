from pymongo import MongoClient
from config import (
    MONGO_URI, MONGO_DB,
    MONGO_COLLECTION_COURSE_DATA,
    MONGO_COLLECTION_COURSE_STRUCT,
)

# ── MongoDB connection ───────

_client = None

def _get_db():
    global _client
    if _client is None:
        print(f"[MongoDB] connecting to {MONGO_URI} / db={MONGO_DB}", flush=True)
        _client = MongoClient(MONGO_URI)
    return _client[MONGO_DB]


# ── Course structure ─────────

def fetch_course_structure(course_id: str) -> list:
    """
    Actual doc shape in DB:
    { "course_id": "cashflow_001", "slides": [{ "id": "slide-01-welcome", "type": "slide" }, ...] }
    """
    db  = _get_db()
    col = db[MONGO_COLLECTION_COURSE_STRUCT]
    doc = col.find_one({"course_id": course_id}, {"_id": 0})

    if not doc:
        print(f"[MongoDB] ERROR: no course-structure found for course_id='{course_id}'", flush=True)
        return []

    # DB uses "slides", fall back to "items" if ever migrated
    slides = doc.get("slides", doc.get("items", []))
    print(f"[MongoDB] course-structure fetched | course_id='{course_id}' | {len(slides)} slides", flush=True)
    for s in slides:
        print(f"  - [{s.get('type','?')}] {s.get('id','?')} status={s.get('status','none')}", flush=True)

    return slides


def _build_description_index(course_id: str) -> dict:
    """
    Actual doc shape in DB:
    { "course_id": "cashflow_001", "content": [{ "type": "image", "image_name": "slide-01-welcome.png", "description": "..." }, ...] }
    Videos use "video_name" instead of "image_name".

    Returns: { "slide-01-welcome": { "description": "...", "image_name": "slide-01-welcome.png", "type": "slide" }, ... }
    The key is the slide ID (image_name without extension, or video_name slug).
    """
    db  = _get_db()
    col = db[MONGO_COLLECTION_COURSE_DATA]
    doc = col.find_one({"course_id": course_id}, {"_id": 0, "content": 1})

    if not doc:
        print(f"[MongoDB] ERROR: no course-data found for course_id='{course_id}'", flush=True)
        return {}

    content = doc.get("content", [])
    print(f"[MongoDB] course-data fetched | course_id='{course_id}' | {len(content)} content items", flush=True)

    index = {}
    for entry in content:
        if "image_name" in entry:
            img   = entry["image_name"]
            title = _title_from_filename(img)
            index[img] = {
                "description": entry.get("description", ""),
                "image_name":  img,
                "title":       title,
                "type":        "slide",
            }
        elif "video_name" in entry:
            name  = entry["video_name"]
            key   = name
            index[key] = {
                "description": entry.get("description", ""),
                "image_name":  "",
                "video_name":  name,
                "title":       name,
                "type":        "video",
            }

    print(f"[MongoDB] description index built | {len(index)} entries", flush=True)
    return index


def _title_from_filename(filename: str) -> str:
    """'slide-02-daily-impact.png' → 'Daily Impact'"""
    name  = filename.replace(".png", "").replace(".jpg", "")
    parts = name.split("-")
    # Drop leading word "slide"/"video" and any numeric parts
    words = [p for p in parts if not p.isdigit() and p.lower() not in ("slide", "video")]
    return " ".join(w.capitalize() for w in words)


def fetch_item_description(course_id: str, item_id: str) -> str:
    """Fetch description for a single item by its ID."""
    index = _build_description_index(course_id)
    entry = index.get(item_id, {})
    desc  = entry.get("description", "")
    if desc:
        print(f"[MongoDB] description fetched | item_id='{item_id}' | {len(desc)} chars", flush=True)
    else:
        print(f"[MongoDB] WARNING: no description for item_id='{item_id}'", flush=True)
    return desc


# ── Write slide progress back to MongoDB ────────────────────────────────────

def clear_ongoing_statuses(course_id: str):
    """Strip 'ongoing' from all slides — called before marking a new slide ongoing."""
    db  = _get_db()
    col = db[MONGO_COLLECTION_COURSE_STRUCT]
    doc = col.find_one({"course_id": course_id}, {"slides": 1})
    if not doc:
        return
    slides = doc.get("slides", [])
    for s in slides:
        if s.get("status") == "ongoing":
            s.pop("status")
    col.update_one({"course_id": course_id}, {"$set": {"slides": slides}})
    print(f"[MongoDB] cleared stale 'ongoing' statuses for course_id='{course_id}'", flush=True)


def update_item_status(course_id: str, item_id: str, status: str):
    """
    Write 'ongoing' or 'completed' back to the items array inside course-structure.
    status: 'ongoing' | 'completed'
    """
    db  = _get_db()
    col = db[MONGO_COLLECTION_COURSE_STRUCT]
    if status == "ongoing":
        clear_ongoing_statuses(course_id)
    result = col.update_one(
        {"course_id": course_id, "slides.id": item_id},
        {"$set": {"slides.$.status": status}}
    )
    if result.matched_count:
        print(f"[MongoDB] status updated | item_id='{item_id}' → '{status}'", flush=True)
    else:
        print(f"[MongoDB] WARNING: could not update status for item_id='{item_id}'", flush=True)


# ── Save / fetch user profile ─────────────────────────────────────────────────

def save_user_profile(course_id: str, session_id: str, profile: dict):
    """
    Upsert user profile into a 'user-profiles' collection.
    profile keys: name, role, skillsets (list), description
    """
    db  = _get_db()
    col = db["user-profiles"]
    col.update_one(
        {"course_id": course_id, "session_id": session_id},
        {"$set": {**profile, "course_id": course_id, "session_id": session_id}},
        upsert=True
    )
    print(f"[MongoDB] user profile saved | session='{session_id}' | role='{profile.get('role','?')}' | name='{profile.get('name','?')}'", flush=True)


# ── Build course item list ───

def build_course_items(course_id: str) -> list:
    """
    Combine course-structure (ordered slide IDs) with course-data (descriptions)
    into a single list the session and UI can use.
    """
    structure = fetch_course_structure(course_id)
    if not structure:
        return []

    # Build description index keyed by slide ID from the content array
    desc_index = _build_description_index(course_id)

    items = []
    for i, raw in enumerate(structure):
        item_id = raw["id"]
        entry   = desc_index.get(item_id, {})

        # Status is always "upcoming" from the shared structure —
        # actual per-user status is stored in the session doc and applied after load.
        status = "upcoming"

        items.append({
            "id":          item_id,
            "type":        entry.get("type", raw.get("type", "slide")),
            "title":       entry.get("title", item_id),
            "image_name":  entry.get("image_name", ""),
            "video_name":  entry.get("video_name", ""),
            "status":      status,
            "description": entry.get("description", ""),
        })

    print(f"[MongoDB] built {len(items)} course items for course_id='{course_id}'", flush=True)
    return items


# ── Course session persistence ────────────────────────────────────────────────

def save_course_session(session) -> None:
    """
    Upsert the full session state into course-session collection.
    Called after every chat turn.
    """
    db  = _get_db()
    col = db["course-session"]

    doc = {
        "session_id":               session.session_id,
        "user_name":                session.user_name,
        "user_role":                session.user_role,
        "user_description":         session.user_description,
        "user_skillsets":           session.user_skillsets,
        "user_level":               session.user_level,
        "user_tactics":             session.user_tactics,
        "history":                  session.full_history,
        "history_summary":          session.history_summary,
        "current_item_id":          session.current_item_id,
        "level_assessment_active":  session.level_assessment_active,
        "slide_statuses":           session.slide_statuses,
    }

    col.update_one(
        {"session_id": session.session_id},
        {"$set": doc},
        upsert=True
    )
    print(f"[MongoDB] session saved | session_id='{session.session_id}' | history={len(session.full_history)} msgs", flush=True)


def load_course_session(session_id: str) -> dict | None:
    """
    Load a previously saved session from course-session collection.
    Returns the raw doc or None if not found.
    """
    db  = _get_db()
    col = db["course-session"]
    doc = col.find_one({"session_id": session_id}, {"_id": 0})
    if doc:
        print(f"[MongoDB] session loaded | session_id='{session_id}' | history={len(doc.get('history', []))} msgs", flush=True)
    else:
        print(f"[MongoDB] no saved session found for session_id='{session_id}'", flush=True)
    return doc


