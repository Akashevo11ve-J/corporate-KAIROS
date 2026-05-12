from moviepy.video.io.VideoFileClip import VideoFileClip
import os

# Input video file
video_path = "vidssave.com What is Data Collection_ _ Data Fundamentals for Beginners 720P.mp4"

# Output folder
output_folder = "clips"
os.makedirs(output_folder, exist_ok=True)

# List of clips to cut
# Format: ("start_time", "end_time")
clips = [
    ("00:25", "04:25")
]

# Load video
video = VideoFileClip(video_path)

# Function to convert time string to seconds
def time_to_seconds(time_str):
    parts = list(map(int, time_str.split(":")))
    
    if len(parts) == 2:  # MM:SS
        minutes, seconds = parts
        return minutes * 60 + seconds
    
    elif len(parts) == 3:  # HH:MM:SS
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds

    else:
        raise ValueError("Invalid time format")

# Cut and save clips
for i, (start, end) in enumerate(clips, start=1):
    start_sec = time_to_seconds(start)
    end_sec = time_to_seconds(end)

    clip = video.subclipped(start_sec, end_sec)

    output_path = os.path.join(output_folder, f"video____{i}.mp4")

    clip.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac"
    )

print("All clips exported successfully!")