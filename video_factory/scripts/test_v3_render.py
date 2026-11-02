#!/usr/bin/env python3
"""V3 Video Generator matching WrongDev August 2026 Reference Videos:
- Full-bleed vertical animated pixel-art character scenes
- Dynamic multi-scene cuts:
    1. Hook: scene_standing_mug.mp4 (Animated avatar with steaming #1 Programmer mug)
    2. Core: scene_ide_screen.mp4 (High-tech glowing IDE syntax scrolling)
    3. Value: scene_macbook_network.mp4 (Animated avatar with laptop & pulsing network nodes)
    4. Outro CTA: scene_avatar_tg_outro.mp4 (Animated avatar pointing to @WrongDevp & Подпишись!)
- Press Start 2P pixel-art top hook banner
- Animated 2-3 word pop-in subtitle bursts with yellow/cyan highlights
- Natural Russian male Edge-TTS voiceover
"""
import os
import sys
import subprocess
import json
import re
from pathlib import Path

BASE_DIR = Path('/home/gut/hermes-workspace/video_factory')
OUTPUT_DIR = BASE_DIR / 'output'
TEMP_DIR = OUTPUT_DIR / 'temp'
SCENES_DIR = OUTPUT_DIR / 'scenes'
EDGE_TTS_BIN = '/home/gut/hermes-workspace/tg_autoposter/.venv/bin/edge-tts'
PROXY_URL = os.getenv('HTTP_PROXY', 'http://127.0.0.1:17890')
TEMP_DIR.mkdir(parents=True, exist_ok=True)

def sanitize_voiceover(text: str) -> str:
    """Transliterate tech terms and numbers to Cyrillic words to prevent TTS artifacts."""
    text = re.sub(r'[\r\n\t]+', ' ', text)
    tech_map = {
        'DeepSeek': 'Дипсик',
        'Cursor': 'Курсор',
        'Qwen': 'Квен',
        'Claude': 'Клод',
        'ChatGPT': 'Чат Джи Пи Ти',
        'GPT': 'Джи Пи Ти',
        'Gemini': 'Джеминай',
        'AI': 'ИИ',
        'OpenAI': 'Опен А И',
        'Opencode': 'Опенкод',
        'WrongDev': 'Вронг Дев',
        'MAX': 'Макс',
        'Plus': 'Плюс',
        '@': 'собака ',
        '/': ' слэш ',
        '#': ' номер ',
        '100': 'сто',
        '3.8': 'три точка восемь',
        '20': 'двадцать',
        '40': 'сорок',
        '20x': 'двадцать икс',
        '5': 'пять',
        '3': 'три',
        '1': 'один',
    }
    for k, v in tech_map.items():
        text = re.sub(rf'\b{re.escape(k)}\b', v, text, flags=re.IGNORECASE)
    text = text.replace('\u2011', '-').replace('\u2014', '-').replace('\u2013', '-')
    text = re.sub(r'[\*\(\)\[\]_`~#<>]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def generate_voiceover(text: str, audio_path: Path) -> float:
    """Generates voiceover using Edge-TTS with Dmitry/Andrew fallback."""
    voices = ['en-US-AndrewMultilingualNeural', 'ru-RU-DmitryNeural', 'en-US-BrianMultilingualNeural']
    clean_text = sanitize_voiceover(text)
    print(f"Generating TTS for: '{clean_text[:60]}...' (length: {len(clean_text)})")
    
    last_err = None
    for voice in voices:
        try:
            cmd = [
                EDGE_TTS_BIN,
                '--proxy', PROXY_URL,
                '--text', clean_text,
                '--voice', voice,
                '--write-media', str(audio_path)
            ]
            subprocess.run(cmd, check=True, timeout=25)
            if audio_path.exists() and audio_path.stat().st_size > 5000:
                print(f"TTS synthesized with voice: {voice} ({audio_path.stat().st_size} bytes)")
                break
        except Exception as e:
            print(f"TTS voice {voice} failed: {e}")
            last_err = e
    else:
        raise RuntimeError(f"All Edge-TTS voices failed! Last error: {last_err}")
        
    probe_cmd = [
        'docker', 'exec', 'video_factory_auth',
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        '/data/output/temp/voice.mp3'
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    duration = float(res.stdout.strip())
    print(f"Voiceover duration: {duration:.2f}s")
    return duration

def build_ass_subtitles(voiceover: str, duration: float, hook_title: str) -> Path:
    """Generates stylish ASS subtitles matching August WrongDev layout."""
    clean = re.sub(r'[!?,.]+', ' ', voiceover).strip()
    words = clean.split()
    
    chunks = []
    i = 0
    while i < len(words):
        chunk_len = 3 if i + 3 <= len(words) else len(words) - i
        chunks.append(' '.join(words[i:i+chunk_len]))
        i += chunk_len
        
    step = duration / max(len(chunks), 1)
    
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TopHook,Press Start 2P,36,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,6,3,8,30,30,220,1
Style: SubBurst,Liberation Sans,66,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,7,4,2,40,40,580,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    
    # 1. Top Hook banner for first 85% of video
    hook_end_sec = max(duration - 2.0, 5.0)
    hook_m, hook_s = divmod(hook_end_sec, 60)
    hook_end_ass = f"0:{int(hook_m):02d}:{hook_s:05.2f}"
    
    clean_hook = hook_title.upper()
    if 'БЕСПЛАТНО' in clean_hook:
        clean_hook = clean_hook.replace('БЕСПЛАТНО', '{\\c&H0000FFFF&}БЕСПЛАТНО{\\c&H00FFFFFF&}')
    events.append(f"Dialogue: 0,0:00:00.00,{hook_end_ass},TopHook,,0,0,0,,{clean_hook}")
    
    # 2. Dynamic Spoken Subtitle Bursts (until outro starts)
    outro_start_sec = max(duration - 2.2, 0.0)
    for idx, chunk in enumerate(chunks):
        s_sec = idx * step
        e_sec = (idx + 1) * step
        
        if s_sec >= outro_start_sec:
            continue
            
        s_m, s_s = divmod(s_sec, 60)
        e_m, e_s = divmod(e_sec, 60)
        start_ass = f"0:{int(s_m):02d}:{s_s:05.2f}"
        end_ass = f"0:{int(e_m):02d}:{e_s:05.2f}"
        
        text_body = chunk.upper()
        if any(k in text_body for k in ["БЕСПЛАТНО", "ХАЛЯВА", "ПРОМПТ", "ТЕЛЕГРАМ", "ВРОНГ", "ССЫЛКА", "ГАЙД"]):
            styled_text = f"{{\\fscx112\\fscy112\\t(0,80,\\fscx100\\fscy100)\\c&H0000FFFF&}}{text_body}"
        else:
            styled_text = f"{{\\fscx112\\fscy112\\t(0,80,\\fscx100\\fscy100)}}{text_body}"
            
        events.append(f"Dialogue: 1,{start_ass},{end_ass},SubBurst,,0,0,0,,{styled_text}")
        
    ass_path = TEMP_DIR / 'subtitles.ass'
    ass_path.write_text(header + '\n'.join(events) + '\n', encoding='utf-8')
    print(f"Generated ASS subtitles: {ass_path}")
    return ass_path

def render_v3_short(title: str, voiceover_text: str, output_filename: str = 'v3_rendered_short.mp4') -> Path:
    audio_path = TEMP_DIR / 'voice.mp3'
    duration = generate_voiceover(voiceover_text, audio_path)
    
    ass_path = build_ass_subtitles(voiceover_text, duration, title)
    
    # Timing calculation:
    # 1. Hook (standing mug): 2.6s
    # 2. Outro (avatar pointing to TG): 2.0s
    # Remaining time split between IDE screen and Macbook network avatar
    t_hook = 2.6
    t_outro = 2.0
    rem = max(duration - t_hook - t_outro, 2.0)
    t_ide = rem * 0.50
    t_net = rem * 0.50
    
    print(f"Scene cuts: Hook={t_hook:.2f}s, IDE={t_ide:.2f}s, Net={t_net:.2f}s, Outro={t_outro:.2f}s (Total: {duration:.2f}s)")
    
    cmd = [
        'docker', 'exec', 'video_factory_auth',
        'ffmpeg', '-y',
        '-i', '/data/output/scenes/scene_standing_mug.mp4',
        '-i', '/data/output/scenes/scene_ide_screen.mp4',
        '-i', '/data/output/scenes/scene_macbook_network.mp4',
        '-i', '/data/output/scenes/scene_avatar_tg_outro.mp4',
        '-i', '/data/output/temp/voice.mp3',
        '-filter_complex',
        f'[0:v]trim=0:{t_hook:.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v0];'
        f'[1:v]trim=0:{t_ide:.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v1];'
        f'[2:v]trim=0:{t_net:.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v2];'
        f'[3:v]trim=0:{t_outro:.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v3];'
        f'[v0][v1][v2][v3]concat=n=4:v=1:a=0[vbase];'
        f'[vbase]subtitles=/data/output/temp/subtitles.ass[outv]',
        '-map', '[outv]',
        '-map', '4:a',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '19',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-t', str(duration),
        f'/data/output/{output_filename}'
    ]
    
    print(f"Rendering V3 Short with container FFmpeg...")
    subprocess.run(cmd, check=True)
    final_output = OUTPUT_DIR / output_filename
    print(f"SUCCESS! Rendered Short: {final_output} ({final_output.stat().st_size / (1024*1024):.2f} MB)")
    return final_output

if __name__ == '__main__':
    t = 'QWEN 3.8 MAX — БЕСПЛАТНО'
    vo = 'Эй разработчик. Квен три точка восемь Макс теперь доступен абсолютно бесплатно. Никаких подписок, просто забирай готовый промпт и все ссылки в моем телеграм канале Вронг Дев, ссылка в описании ролика!'
    render_v3_short(t, vo, 'v3_perfect_short.mp4')
