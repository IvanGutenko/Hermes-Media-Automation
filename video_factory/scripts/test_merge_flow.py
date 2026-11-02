import os
import subprocess
from pathlib import Path

BASE_DIR = Path("/home/gut/hermes-workspace/video_factory")
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = OUTPUT_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

EDGE_TTS_BIN = "/home/gut/hermes-workspace/tg_autoposter/.venv/bin/edge-tts"

voice_text = "Эй, разработчик. Новый Клод три точка восемь рвет все бенчмарки и пишет код в десять раз быстрее. Забирай готовый промпт и полный гайд в моем канале Вронг Дев. Ссылка в описании."
voice_file = TEMP_DIR / "test_flow_voice.mp3"
final_video = OUTPUT_DIR / "final_flow_short_demo.mp4"

print(f"Synthesizing voice: '{voice_text}'...")
subprocess.run([
    EDGE_TTS_BIN,
    "--voice", "ru-RU-DmitryNeural",
    "--rate=+5%",
    "--text", voice_text,
    "--write-media", str(voice_file)
], check=True)

# Probe voice duration
probe = subprocess.run([
    "docker", "exec", "video_factory_auth",
    "ffprobe", "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", "/data/output/temp/test_flow_voice.mp3"
], capture_output=True, text=True, check=True)
v_dur = float(probe.stdout.strip())
print(f"Voice duration: {v_dur:.2f}s")

# Merge Flow video with voiceover using docker container ffmpeg
cmd = [
    "docker", "exec", "video_factory_auth",
    "ffmpeg", "-y",
    "-stream_loop", "-1", "-i", "/data/output/generated_flow_video.mp4",
    "-i", "/data/output/temp/test_flow_voice.mp3",
    "-map", "0:v:0",
    "-map", "1:a:0",
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "18",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac",
    "-b:a", "192k",
    "-t", f"{v_dur:.2f}",
    "/data/output/final_flow_short_demo.mp4"
]
subprocess.run(cmd, check=True)
print(f"🎉 Final video created: {final_video} ({final_video.stat().st_size / (1024*1024):.2f} MB)")
