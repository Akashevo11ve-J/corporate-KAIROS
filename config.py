"""
Configuration — loads from .env
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Models ───────────────────────────────────────────────────────────────────
MAIN_AGENT_MODEL   = "claude-sonnet-4-6"
WORKER_MODEL       = "claude-haiku-4-5-20251001"
ASSESSMENT_MODEL   = "claude-sonnet-4-6"

# ── Temperatures ──────────────────────────────────────────────────────────────
TEMP_MAIN_AGENT   = 0.7   # teaching — warm and natural but focused
TEMP_ASSESSMENT   = 0.3   # MCQ generation — structured, consistent
TEMP_LEVEL_AGENT  = 0.3   # calibration questions + JSON output
TEMP_GUARDRAIL    = 0.1   # classification — as deterministic as possible
TEMP_WORKER       = 0.2   # compression, RAG condense — purely functional

# ── Token limits ──────────────────────────────────────────────────────────────
HISTORY_TOKEN_THRESHOLD = 1000
MAX_TOKENS_RESPONSE     = 600
MAX_TOKENS_WORKER       = 512
MAX_TOKENS_ASSESSMENT   = 4096

# ── Signals ───────────────────────────────────────────────────────────────────
SIGNAL_READY_TO_PROCEED = "[READY_TO_PROCEED]"
SIGNAL_TOPIC_COMPLETE   = "[TOPIC_COMPLETE]"

# ── Assessment ────────────────────────────────────────────────────────────────
ASSESSMENT_QUESTION_COUNT = 5

# ── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URI                      = os.getenv("MONGO_URI")
MONGO_DB                       = os.getenv("MONGO_DB", "db")
MONGO_COLLECTION_COURSE_DATA   = os.getenv("MONGO_COLLECTION_COURSE_DATA", "course-data")
MONGO_COLLECTION_COURSE_STRUCT = os.getenv("MONGO_COLLECTION_COURSE_STRUCTURE", "course-structure")

# ── Pinecone ──────────────────────────────────────────────────────────────────
PINECONE_API_KEY   = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX     = os.getenv("PINECONE_INDEX", "poc-corporate")

MAX_LEVEL_QUESTIONS = 5
# ── Anthropic ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY =  os.environ["ANTHROPIC_API_KEY"]

# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ── Video & Images ────────────────────────────────────────────────────────────
VIDEO_URL = os.getenv("VIDEO_URL", "")
IMAGE_URL = os.getenv("IMAGE_URL", "")
# Directory where video files are stored / downloaded on disk
VIDEO_BASE_DIR = os.getenv("VIDEO_BASE_DIR", ".")
# Seconds between frames when falling back to vision pipeline
VIDEO_FRAME_INTERVAL = int(os.getenv("VIDEO_FRAME_INTERVAL", "3"))
