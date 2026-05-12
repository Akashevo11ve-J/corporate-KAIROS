TOOL_SCHEMAS = [
    {
        "name": "rag_search",
        "description": (
            "Search the course content for more detailed or specific information. "
            "Use this when the user asks something that goes deeper than what's in your current context, "
            "or when you need additional material to explain a concept properly. "
            "A RAG search is already done automatically when each slide loads — only call this if you genuinely need MORE or DIFFERENT information than what you already have. "
            "Set top_k= "" only when the default 3 results are not enough to answer the question — Maximum is 7"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you're searching for — be specific about the concept or topic."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to retrieve. Default 3. Use 7 only when you need broader coverage. Max 7.",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 7
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_slide_content",
        "description": (
            "Fetch the full description of a PREVIOUSLY COMPLETED slide or video — only when "
            "the user explicitly asks about something from an earlier slide. "
            "Never use this for the current slide (already in your context) or for upcoming slides (locked)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The ID of the slide or video. Use the course structure to find the right ID."
                }
            },
            "required": ["item_id"]
        }
    },
    {
        "name": "clip_and_transcribe",
        "description": (
            "Get the timestamped transcript of a specific time segment from the current video. "
            "Returns sentences with their exact start and end times in [MM:SS–MM:SS] format. "
            "Use this when the user asks about a specific part, timestamp, or section they didn't understand. "
            "Also use this to locate when a topic starts or how long it lasts — scan the returned timestamps to find the answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "video_id": {
                    "type": "string",
                    "description": "The current video's ID from the course structure."
                },
                "start_time": {
                    "type": "string",
                    "description": "Start timestamp in MM:SS format, e.g. '1:30'."
                },
                "end_time": {
                    "type": "string",
                    "description": "End timestamp in MM:SS format, e.g. '2:45'."
                },
                "what_user_wants": {
                    "type": "string",
                    "description": "What the user wants to understand from this segment."
                }
            },
            "required": ["video_id", "start_time", "end_time", "what_user_wants"]
        }
    },
    {
        "name": "transcribe_full_video",
        "description": (
            "Get the full timestamped transcript of the current video. "
            "Returns every sentence with its exact start and end time in [MM:SS–MM:SS] format, covering the whole video. "
            "Use this when the user is not sure which part confused them, or when they ask questions like "
            "'when does X topic start?', 'how long does the instructor talk about Y?', or 'at what point does Z come up?' — "
            "scan the returned timestamps to find and report the exact duration or starting point."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "video_id": {
                    "type": "string",
                    "description": "The current video's ID."
                }
            },
            "required": ["video_id"]
        }
    },
    {
        "name": "extract_frame",
        "description": (
            "Extract and analyze a frame from the current video at a specific timestamp. "
            "Use this when the user is confused about something visual — a diagram, chart, or animation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "video_id": {
                    "type": "string",
                    "description": "The current video's ID."
                },
                "timestamp": {
                    "type": "string",
                    "description": "Timestamp in MM:SS format."
                },
                "what_to_look_for": {
                    "type": "string",
                    "description": "What the user wants to understand from this frame."
                }
            },
            "required": ["video_id", "timestamp", "what_to_look_for"]
        }
    },
    {
        "name": "summarise_history",
        "description": (
            "Compress the conversation history to free up context. "
            "Call this when the conversation has gotten very long."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why you're summarising now."
                }
            },
            "required": ["reason"]
        }
    },
    {
        "name": "observe_learner",
        "description": (
            "Call this when you're unsure how the learner is doing — when they seem confused, "
            "are giving vague answers, just saying 'ok' without showing understanding, or you "
            "need a second opinion on how to proceed. "
            "Returns a specific instruction on what to do next based on how this learner is engaging."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why you're calling the observer — what you're unsure about."
                }
            },
            "required": ["reason"]
        }
    },
    {
  "name": "assess_user_level",
  "description": "Call this ONCE before explaining the first real learning unit (slide or video)...",
  "input_schema": {
    "type": "object",
    "properties": {
      "content_type": {
        "type": "string",
        "enum": ["slide", "video"],
        "description": "Whether the current content is a slide or a video."
      },
      "title": {
        "type": "string",
        "description": "Title of the slide or video."
      },
      "user_context": {
        "type": "string",
        "description": "Short note about learner background."
      }
    },
    "required": ["content_type", "title", "user_context"]
  }
},
]


TOOL_STATUS_MAP = {
    "assess_user_level":     "level",
    "rag_search":            "rag",
    "fetch_slide_content":   "slide",
    "clip_and_transcribe":   "video_clip",
    "transcribe_full_video": "transcribe",
    "extract_frame":         "frame",
    "summarise_history":     "summarise",
    "observe_learner":       "observer",
}
