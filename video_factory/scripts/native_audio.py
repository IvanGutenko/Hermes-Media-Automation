#!/usr/bin/env python3
"""Verify native Veo narration before any publication; preserve synchronized audio."""
import json
import hashlib
import os
import re
from pathlib import Path
import requests
from script_generator import PROXIES,SPEECH_NAMES

NUMBERS=dict(zip('ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать'.split(),range(20)))
NUMBERS.update({'одна':1,'одно':1,'две':2,'двадцать':20,'тридцать':30,'сорок':40,'пятьдесят':50,'шестьдесят':60,'семьдесят':70,'восемьдесят':80,'девяносто':90,'сто':100,'двести':200,'триста':300,'четыреста':400,'пятьсот':500,'шестьсот':600,'семьсот':700,'восемьсот':800,'девятьсот':900,'тысяча':1000,'тысячи':1000,'тысяч':1000,'миллион':1000000,'миллиона':1000000,'миллионов':1000000})
NUMBERS.update({'нуля':0,'одного':1,'одной':1,'двух':2,'трех':3,'четырех':4,
                'пяти':5,'шести':6,'семи':7,'восьми':8,'девяти':9,'десяти':10})
# Declined cardinals: the narration says «от тридцати», speech recognition writes «30».
NUMBERS.update({'одному':1,'двум':2,'трем':3,'четырем':4,'одиннадцати':11,'двенадцати':12,'тринадцати':13,
                'четырнадцати':14,'пятнадцати':15,'шестнадцати':16,'семнадцати':17,'восемнадцати':18,
                'девятнадцати':19,'двадцати':20,'тридцати':30,'сорока':40,'пятидесяти':50,'шестидесяти':60,
                'семидесяти':70,'восьмидесяти':80,'девяноста':90,'ста':100,'двухсот':200,'трехсот':300,
                'четырехсот':400,'пятисот':500,'шестисот':600,'семисот':700,'восьмисот':800,'девятисот':900,
                'тысячу':1000,'тысячам':1000,'миллионам':1000000})
ORDINALS={'перв':1,'втор':2,'трет':3,'четверт':4,'пят':5,'шест':6,'седьм':7,'восьм':8,'девят':9,'десят':10,'одиннадцат':11,'двенадцат':12,'тринадцат':13,'четырнадцат':14,'пятнадцат':15,'шестнадцат':16,'семнадцат':17,'восемнадцат':18,'девятнадцат':19,'двадцат':20,'тридцат':30}
for stem,value in ORDINALS.items():
    for suffix in ('ый','ой','ий','ое','ая','ого','ому','ом','ую'):NUMBERS.setdefault(stem+suffix,value)
for word in ('третьего','третьему','третьем','третье','третья','третью','третьей'):NUMBERS.setdefault(word,3)


def speech_tokens(text):
    text=re.sub(r'[\u2010-\u2015\u2212]','-',text)
    # Whisper split the spoken channel name into a preposition and a name.
    # Independent audio transcription confirmed WrongDev in this exact CTA context.
    text=re.sub(r'(?i)(канале)\s+в\s+ронг\s*дев\b',r'\1 Вронг Дев',text)
    text=re.sub(r'(?i)\bвронгдев\b','Вронг Дев',text)
    for name,spoken in sorted(SPEECH_NAMES.items(),key=lambda p:-len(p[0])):
        text=re.sub(r'(?<!\w)'+re.escape(name)+r'(?!\w)',spoken,text,flags=re.I)
    # «10 000» is one number written with a thousands separator, not the numbers ten and zero.
    text=re.sub(r'(?<![\d.,])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?![\d])',lambda m:re.sub(r'\s','',m.group()),text)
    text=re.sub(r'(?<=\d)[.,](?=\d)',' точка ',text)
    text=re.sub(r'\s*%',' процент ',text)
    tokens=re.findall(r'[а-яa-z]+|\d+',text.lower().replace('ё','е'))
    tokens=['процент' if re.fullmatch(r'процент(а|ов|е|ах|ам|ом|ы)?',t) else t for t in tokens]
    result=[];i=0
    while i<len(tokens):
        token=tokens[i]
        if token.isdigit():
            # «5 тысяч» says the same value as «пять тысяч».
            if i+1<len(tokens) and NUMBERS.get(tokens[i+1],0)>=1000:
                result.append('#'+str(int(token)*NUMBERS[tokens[i+1]]));i+=2;continue
            result.append('#'+str(int(token)));i+=1;continue
        if token not in NUMBERS:result.append(token);i+=1;continue
        total=0;current=0
        while i<len(tokens) and tokens[i] in NUMBERS:
            value=NUMBERS[tokens[i]]
            if value>=1000:total+=(current or 1)*value;current=0
            else:current+=value
            i+=1
        result.append('#'+str(total+current))
    # ASR writes the spoken fractional quantity «три с половиной» as «3,5».
    # Preserve the exact value, unit and surrounding words; do not accept
    # approximate quantities or missing negations.
    canonical=[];i=0
    while i<len(result):
        if (re.fullmatch(r'#\d+',result[i]) and result[i+1:i+3]==['с','половиной']):
            canonical.extend([result[i],'точка','#5']);i+=3
        else:canonical.append(result[i]);i+=1
    return canonical


def analyze(raw,scene,run_dir,index):
    from autonomous_video_pipeline import probe,docker_command,container_path,write_json
    if not any(s['codec_type']=='audio' for s in probe(raw)['streams']):raise RuntimeError('Native Flow clip has no audio; refusing silent publication')
    audio=run_dir/f'native_{index:02d}.wav'
    docker_command('ffmpeg','-v','error','-y','-i',container_path(raw),'-vn','-ac','1','-ar','16000',container_path(audio))
    expected=speech_tokens(scene['voiceover_ru'])
    cached=run_dir/f'native_{index:02d}.asr.json'
    digest=hashlib.sha256(raw.read_bytes()).hexdigest()
    previous=json.loads(cached.read_text()) if cached.exists() else {}
    candidates=[previous] if previous.get('raw_sha256')==digest and previous.get('language_detection')=='automatic' else []
    for model in ('whisper-large-v3-turbo','whisper-large-v3'):
        if not candidates:
            with audio.open('rb') as stream:
                response=requests.post('https://api.groq.com/openai/v1/audio/transcriptions',
                    headers={'Authorization':'Bearer '+os.environ['GROQ_API_KEY']},
                    files={'file':(audio.name,stream,'audio/wav')},data={'model':model,
                    'response_format':'verbose_json','temperature':'0','timestamp_granularities[]':['word','segment']},proxies=PROXIES,timeout=90)
            if not response.ok:raise RuntimeError(f'Native speech verification HTTP {response.status_code}; clip retained for retry')
            data=response.json();data.update(verification_model=model,language_detection='automatic',raw_sha256=digest)
            write_json(run_dir/f'native_{index:02d}.{model}.json',data)
        else:data=candidates.pop()
        actual=speech_tokens(data.get('text',''))
        if data.get('language','').lower()=='russian' and actual==expected:
            words=data.get('words',[])
            if not words:raise RuntimeError('Native speech has no word timing evidence')
            duration=float(probe(raw)['format']['duration'])
            start=max(0,float(words[0]['start'])-0.15)
            end=min(duration,float(words[-1]['end'])+0.18)
            if not 1<end-start<=duration:raise RuntimeError('Native speech timing is invalid')
            data.update(expected=scene['voiceover_ru'],tokens_match=True,trim_start=start,trim_end=end)
            write_json(cached,data)
            return start,end,data
    raise RuntimeError('Native Russian speech differs from the script; scene retained for review, no publication')


def render(raw,subtitles,start,end,target):
    from autonomous_video_pipeline import docker_command,container_path
    docker_command('ffmpeg','-v','error','-y','-i',container_path(raw),
        '-vf',f'trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=24,subtitles={container_path(subtitles)}',
        '-af',f'atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS',
        '-map','0:v:0','-map','0:a:0','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p',
        '-c:a','aac','-ar','48000','-ac','2','-b:a','160k',container_path(target))
