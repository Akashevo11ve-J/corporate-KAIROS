"""
Real video processing pipeline:
  - clip_and_transcribe: extract audio segment → Whisper transcription
  - transcribe_full_video: transcribe entire audio track via Whisper
  - extract_frame: pull a single frame → GPT-4o Vision description

Falls back to frame-by-frame GPT-4o Vision when the audio is silent or missing.
"""

import os
import base64
import tempfile

import requests

from config import OPENAI_API_KEY, VIDEO_BASE_DIR, VIDEO_FRAME_INTERVAL, VIDEO_URL

try:
    try:
        from moviepy import VideoFileClip
    except ImportError:
        from moviepy.editor import VideoFileClip
except Exception as e:
    raise ImportError(f"moviepy not installed. Run: pip install moviepy\n{e}")

try:
    from openai import OpenAI
except ImportError:
    raise ImportError("openai not installed. Run: pip install openai")

try:
    from PIL import Image
except ImportError:
    raise ImportError("pillow not installed. Run: pip install pillow")


def _openai_client() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY)


def download_from_s3(url: str, output_path: str) -> bool:
    """Download a video from S3 (or any HTTP URL) to output_path."""
    print(f"\n[VideoProcessor] Downloading video from S3: {url}", flush=True)
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"[VideoProcessor] Downloaded to: {output_path}", flush=True)
        return True
    except Exception as e:
        print(f"[VideoProcessor] Download failed: {e}", flush=True)
        return False


def _resolve_video_path(video_name: str) -> str:
    """
    Given a video_name from MongoDB (e.g. 'Cash_Flow_for_Beginners...mp4'),
    return the absolute path on disk. If not found locally and VIDEO_URL is
    set, downloads the file from S3 first.
    """
    path = os.path.join(VIDEO_BASE_DIR, video_name)
    if os.path.exists(path):
        return path
    # Try current working directory as fallback
    cwd_path = os.path.join(os.getcwd(), video_name)
    if os.path.exists(cwd_path):
        return cwd_path
    # Download from S3 if URL is configured — append the filename to the base URL
    if VIDEO_URL:
        full_url = VIDEO_URL.rstrip("/") + "/" + video_name
        dest = os.path.join(VIDEO_BASE_DIR, video_name)
        if download_from_s3(full_url, dest):
            return dest
    raise FileNotFoundError(f"Video file not found: '{video_name}' (looked in '{VIDEO_BASE_DIR}', cwd, and S3)")


def _parse_timestamp(ts: str) -> float:
    """Convert 'MM:SS' or 'HH:MM:SS' string to seconds."""
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return float(ts)


def _subclip(clip: "VideoFileClip", start: float, end: float) -> "VideoFileClip":
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start, end)
    if hasattr(clip, "with_subclip"):
        return clip.with_subclip(start, end)
    return clip.subclip(start, end)


def _write_audio(audio_clip, path: str):
    try:
        audio_clip.write_audiofile(path, logger=None)
    except TypeError:
        audio_clip.write_audiofile(path)


# ── Whisper transcription ─────────────────────────────────────────────────────

def _seconds_to_mmss(seconds: float) -> str:
    """Convert float seconds to MM:SS string."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _transcribe_audio_file(audio_path: str, offset_seconds: float = 0.0) -> list[dict]:
    """
    Transcribe audio using Whisper with word+segment timestamps.
    Returns a list of segments: [{"start": float, "end": float, "text": str}, ...]
    offset_seconds is added to all timestamps (for clip_and_transcribe where audio starts mid-video).
    """
    client = _openai_client()
    print(f"[VideoProcessor] Whisper transcribing: {audio_path}", flush=True)
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )
    segments = []
    for seg in response.segments:
        segments.append({
            "start": round(seg.start + offset_seconds, 2),
            "end":   round(seg.end   + offset_seconds, 2),
            "text":  seg.text.strip(),
        })
    print(f"[VideoProcessor] Whisper done | {len(segments)} segments", flush=True)
    return segments


def segments_to_text(segments: list[dict]) -> str:
    """Format timed segments as '[MM:SS–MM:SS] sentence' lines."""
    lines = []
    for seg in segments:
        start = _seconds_to_mmss(seg["start"])
        end   = _seconds_to_mmss(seg["end"])
        lines.append(f"[{start}–{end}] {seg['text']}")
    return "\n".join(lines)


# ── Vision fallback ───────────────────────────────────────────────────────────

def _extract_frames_to_dir(clip: "VideoFileClip", start: float, end: float,
                            interval: int, tmpdir: str) -> list[dict]:
    frames = []
    duration = clip.duration
    t = start
    while t <= min(end, duration):
        frame = clip.get_frame(t)
        img = Image.fromarray(frame.astype("uint8"))
        fname = os.path.join(tmpdir, f"frame_{int(t):06d}s.jpg")
        img.save(fname, "JPEG", quality=85)
        frames.append({"timestamp": t, "path": fname})
        print(f"[VideoProcessor] frame saved: {t:.0f}s → {fname}", flush=True)
        t += interval
    return frames


def _describe_frames(frames: list[dict]) -> str:
    client = _openai_client()
    descriptions = []
    for f in frames:
        ts = f["timestamp"]
        with open(f["path"], "rb") as img_file:
            b64 = base64.b64encode(img_file.read()).decode("utf-8")
        prompt = (
            f"This is a frame from an e-learning video captured at {ts:.0f} seconds. "
            "Describe what is shown in 2-3 sentences. Focus on visible text, diagrams, "
            "charts, and any on-screen actions that would help a learner understand the content."
        )
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                            "detail": "low"
                        }}
                    ]
                }]
            )
            desc = resp.choices[0].message.content.strip()
            descriptions.append(f"[{ts:.0f}s] {desc}")
            print(f"[VideoProcessor] frame described: {ts:.0f}s", flush=True)
        except Exception as e:
            print(f"[VideoProcessor] vision failed at {ts:.0f}s: {e}", flush=True)
            descriptions.append(f"[{ts:.0f}s] (visual description unavailable)")

    return "\n\n".join(descriptions)


def _vision_summary(frame_descriptions: str, start: float, end: float) -> str:
    client = _openai_client()
    prompt = (
        f"Below are visual descriptions of frames from an e-learning video "
        f"({start:.0f}s to {end:.0f}s), sampled every {VIDEO_FRAME_INTERVAL} seconds.\n\n"
        f"{frame_descriptions}\n\n"
        "Write a 3-5 sentence coherent summary of what happens in this segment, "
        "as if narrating it to a learner who missed it."
    )
    resp = _openai_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024
    )
    return resp.choices[0].message.content.strip()


def _audio_then_vision_fallback(clip: "VideoFileClip", start: float, end: float,
                                 tmpdir: str, label: str) -> str:
    """
    Try Whisper on the audio segment. If silent or empty, fall back to GPT-4o Vision.
    Returns timed transcript lines '[MM:SS–MM:SS] sentence' or vision description.
    """
    audio_path = os.path.join(tmpdir, "segment.mp3")
    segment = _subclip(clip, start, min(end, clip.duration))

    segments = []
    if segment.audio is not None:
        try:
            _write_audio(segment.audio, audio_path)
            # Pass start as offset so timestamps are relative to the full video, not the clip
            segments = _transcribe_audio_file(audio_path, offset_seconds=start)
        except Exception as e:
            print(f"[VideoProcessor] audio extraction failed ({label}): {e}", flush=True)

    if segments:
        return segments_to_text(segments)

    print(f"[VideoProcessor] silent/no audio — falling back to vision pipeline ({label})", flush=True)
    frames = _extract_frames_to_dir(clip, start, end, VIDEO_FRAME_INTERVAL, tmpdir)
    if not frames:
        return "No content could be extracted from this segment."
    frame_descs = _describe_frames(frames)
    summary = _vision_summary(frame_descs, start, end)
    return f"{frame_descs}\n\n── Summary ──\n{summary}"


# ── Public tool functions ─────────────────────────────────────────────────────

def clip_and_transcribe(video_name: str, start_time: str, end_time: str) -> str:
    """
    Extract and transcribe a specific segment of a video.
    Returns transcript text, or a GPT-4o Vision description if audio is silent.
    """
    video_path = _resolve_video_path(video_name)
    start = _parse_timestamp(start_time)
    end   = _parse_timestamp(end_time)
    label = f"{start_time}→{end_time}"
    print(f"[VideoProcessor] clip_and_transcribe | {video_name} | {label}", flush=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        clip = VideoFileClip(video_path)
        result = _audio_then_vision_fallback(clip, start, end, tmpdir, label)
        clip.close()

    return result


def transcribe_full_video(video_name: str) -> str:
    """
    Transcribe the entire audio track of a video.
    Falls back to frame-by-frame vision if silent.
    """
    video_path = _resolve_video_path(video_name)
    print(f"[VideoProcessor] transcribe_full_video | {video_name}", flush=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        clip = VideoFileClip(video_path)
        start = 0.0
        end   = clip.duration
        result = _audio_then_vision_fallback(clip, start, end, tmpdir, "full")
        clip.close()

    return result


def extract_frame(video_name: str, timestamp: str, what_to_look_for: str) -> str:
    """
    Pull a single frame at the given timestamp and describe it with GPT-4o Vision,
    focused on what_to_look_for.
    """
    video_path = _resolve_video_path(video_name)
    t = _parse_timestamp(timestamp)
    print(f"[VideoProcessor] extract_frame | {video_name} | t={t:.0f}s | '{what_to_look_for}'", flush=True)

    clip = VideoFileClip(video_path)
    t = min(t, clip.duration - 0.1)

    with tempfile.TemporaryDirectory() as tmpdir:
        frame = clip.get_frame(t)
        img = Image.fromarray(frame.astype("uint8"))
        img_path = os.path.join(tmpdir, "frame.jpg")
        img.save(img_path, "JPEG", quality=85)

        with open(img_path, "rb") as img_file:
            b64 = base64.b64encode(img_file.read()).decode("utf-8")

    clip.close()

    client = _openai_client()
    prompt = (
        f"This is a frame from an e-learning video at {timestamp}. "
        f"The learner wants to understand: '{what_to_look_for}'. "
        "Describe what you see in the frame clearly and concisely, focusing specifically on "
        "what would help the learner understand that point."
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                    "detail": "high"
                }}
            ]
        }],
        max_tokens=1024
    )
    description = resp.choices[0].message.content.strip()
    print(f"[VideoProcessor] frame described | {len(description)} chars", flush=True)
    return description
