
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
    "Mapping your experience...",
    "Scanning what you've worked with...",
    "Building your profile...",
    "Checking what clicks for you...",
    "Piecing together your background...",
    "Understanding how you think about this...",
    "Getting the lay of the land...",
    "Tuning to your level...",
    "Reading between the lines...",
    "Connecting the dots on your experience...",
]


_POOLS["normal"] = [
    "Cooking for you...",
    "Getting things ready...",
    "Loading up...",
    "Moseying...",
    "Almost Done...",
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

    def _start_loop(self, tool_type: str, callback):
        self._stop   = threading.Event()
        self._thread = threading.Thread(
            target=_status_loop,
            args=(tool_type, callback, self._stop),
            daemon=True,
        )
        self._thread.start()

    def start(self, tool_type: str, callback=None):
        if callback is None:
            callback = lambda msg: print(f"\n  ⟳ {msg}", flush=True)
        self._start_loop(tool_type, callback)

    def start_queued(self, tool_type: str):
        self._start_loop(tool_type, self.queue.put)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.5)
            self._thread = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()
