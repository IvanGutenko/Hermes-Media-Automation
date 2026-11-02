import re
import os
import subprocess
from pathlib import Path

OUTPUT_DIR = Path('/home/gut/hermes-workspace/video_factory/output')
TEMP_DIR = OUTPUT_DIR / 'temp'
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# 1. Prepare ASS Subtitles with rapid 2-3 word bursts and pop-in animation
voiceover = 'Эй разработчик. Квен три восемь теперь доступен абсолютно бесплатно. Никаких подписок, просто забирай готовый промпт и все ссылки в моем телеграм канале Вронг Дев, ссылка в описании.'

words = voiceover.replace('.', '').replace(',', '').split()
chunks = []
i = 0
while i < len(words):
    chunk_len = 3 if i + 3 <= len(words) else len(words) - i
    chunks.append(' '.join(words[i:i+chunk_len]))
    i += chunk_len

duration = 10.0 # test 10 seconds
step = duration / len(chunks)

header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,DejaVu Sans,52,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,3,8,40,40,160,1
Style: SubBurst,DejaVu Sans,68,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,4,2,60,60,850,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

events = []
events.append(f"Dialogue: 0,0:00:00.00,0:00:{int(duration):02d}.00,Hook,,0,0,0,,WRONGDEV | ХАЛЯВА ДЛЯ IT")

for idx, chunk in enumerate(chunks):
    start_s = idx * step
    end_s = (idx + 1) * step
    start_ass = f"0:00:{start_s:05.2f}"
    end_ass = f"0:00:{end_s:05.2f}"
    
    # Pop-in scale bounce animation: scales from 120% to 100% in 100ms
    text = f"{{\\fscx120\\fscy120\\t(0,100,\\fscx100\\fscy100)}}{chunk.upper()}"
    events.append(f"Dialogue: 1,{start_ass},{end_ass},SubBurst,,0,0,0,,{text}")

ass_path = TEMP_DIR / 'test_v2.ass'
ass_path.write_text(header + '\n'.join(events) + '\n', encoding='utf-8')
print(f"Generated {len(chunks)} rapid subtitle bursts!")

# 2. FFmpeg Render with:
# - Animated gradient / tech visual background
# - Audio waveform visualizer (reactive to voice!)
# - Avatar with subtle breathing / floating motion
# - Dynamic pop-in subtitles
cmd = [
    'docker', 'exec', 'video_factory_auth',
    'ffmpeg', '-y',
    '-f', 'lavfi', '-t', str(duration),
    '-i', 'color=c=#0b1021:s=1080x1920:r=30',
    '-loop', '1', '-t', str(duration),
    '-i', '/data/output/temp/avatar.png',
    '-i', '/data/output/temp/voice.mp3',
    '-filter_complex',
    '[2:a]showwaves=s=1080x240:mode=p2p:colors=#00ffc8@0.8:scale=sqrt[wave];'
    '[0:v][wave]overlay=0:1250[bg_wave];'
    '[1:v]scale=580:-1[avatar];'
    '[bg_wave][avatar]overlay=(W-w)/2:H-h+15*sin(2*PI*t/2.5)[v_avatar];'
    '[v_avatar]subtitles=/data/output/temp/test_v2.ass[outv]',
    '-map', '[outv]',
    '-map', '2:a',
    '-c:v', 'libx264',
    '-preset', 'veryfast',
    '-crf', '22',
    '-pix_fmt', 'yuv420p',
    '-c:a', 'aac',
    '-b:a', '192k',
    '-t', str(duration),
    '/data/output/test_v2_short.mp4'
]

print("Rendering preview...")
subprocess.run(cmd, check=True)
print("Preview rendered to test_v2_short.mp4")
