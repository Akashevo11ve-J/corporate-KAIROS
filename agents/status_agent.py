
import threading
import time
import queue
import random

import logger

# ── Word pools per tool type ───────────────────────────────────────────────────
# Each entry is a list of short phrases. One is picked randomly each tick.

_POOLS = {
    "thinking": [
        "Thinking it through...",
        "Moseying through this...",
        "Chewing on it...",
        "Sitting with this one...",
        "Turning it over...",
        "Working through it...",
        "Pulling the threads...",
        "Connecting the dots...",
        "Piecing it together...",
        "Getting the full picture...",
        "On it...",
        "Shaping a response...",
        "Brewing something...",
        "Reading between the lines...",
        "Lining things up...",
        "Figuring this out...",
        "Almost there...",
        "Digging into this...",
        "Thinking out loud...",
        "Working it out...",
    ],
    "rag": [
        "Searching the material...",
        "Digging through the course...",
        "Scanning for context...",
        "Pulling relevant bits...",
        "Hunting through the content...",
        "Sifting through the slides...",
        "Looking this up...",
        "Finding the right piece...",
        "Combing through the notes...",
        "Fetching more context...",
    ],
    "slide": [
        "Pulling up that slide...",
        "Fetching the slide content...",
        "Loading slide details...",
        "Getting that slide...",
        "Grabbing the slide info...",
    ],
    "history": [
        "Looking back through our chat...",
        "Reviewing what we covered...",
        "Checking the conversation...",
        "Recalling earlier context...",
        "Scanning back through this...",
        "Refreshing memory on this...",
    ],
    "video_clip": [
        "Clipping that segment...",
        "Digging into the video...",
        "Processing that part...",
        "Pulling that clip...",
        "Extracting the segment...",
        "Going into the video...",
    ],
    "transcribe": [
        "Going through the full video...",
        "Transcribing it now...",
        "Reading through the video...",
        "Working through the footage...",
        "Processing the whole video...",
    ],
    "frame": [
        "Grabbing that frame...",
        "Extracting the frame...",
        "Pulling up that moment...",
        "Locking onto that timestamp...",
        "Getting that frame...",
    ],
    "summarise": [
        "Tidying up the conversation...",
        "Compressing history...",
        "Folding up earlier context...",
        "Archiving what we covered...",
        "Making room for more...",
    ],
}

_TOOL_POOL_MAP = {
    "rag_search":            "rag",
    "fetch_slide_content":   "slide",
    "clip_and_transcribe":   "video_clip",
    "transcribe_full_video": "transcribe",
    "extract_frame":         "frame",
    "summarise_history":     "summarise",
    "assess_user_level":     "level",
}

_POOLS["level"] = [
    "Getting a feel for where you're at...",
    "Reading your background...",
    "Calibrating to you...",
    "Sizing up what you know...",
    "Figuring out your level...",
]


_POOLS["normal"] = [
    "Cooking for you...",
    "Getting things ready...",
    "Loading up...",
    "On it...",
    "Just a sec...",
    "Warming up...",
    "Setting the scene...",
    "Almost ready...",
]

_DEFAULT_POOL = _POOLS["thinking"]


def pick_for_tool(tool_name: str) -> str:
    pool_key = _TOOL_POOL_MAP.get(tool_name, "thinking")
    pool = _POOLS.get(pool_key, _DEFAULT_POOL)
    return random.choice(pool)


def _pick(tool_type: str, last: str) -> str:
    pool = _POOLS.get(tool_type, _DEFAULT_POOL)
    choices = [m for m in pool if m != last] or pool
    return random.choice(choices)


def _status_loop(tool_type: str, callback, stop_event: threading.Event):
    last = ""
    while not stop_event.is_set():
        msg = _pick(tool_type, last)
        last = msg
        logger.log_status_agent(msg)
        callback(msg)

        for _ in range(30):   # 30 × 100ms = 3s per message
            if stop_event.is_set():
                return
            time.sleep(0.1)


class StatusAgent:
    def __init__(self):
        self._thread = None
        self._stop   = threading.Event()
        self.queue   = queue.Queue()

    def start(self, tool_type: str, callback=None):
        self._stop = threading.Event()
        if callback is None:
            callback = lambda msg: print(f"\n  ⟳ {msg}", flush=True)
        self._thread = threading.Thread(
            target=_status_loop,
            args=(tool_type, callback, self._stop),
            daemon=True,
        )
        self._thread.start()

    def start_queued(self, tool_type: str):
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=_status_loop,
            args=(tool_type, self.queue.put, self._stop),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.5)
            self._thread = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()
