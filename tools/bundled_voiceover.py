"""Whole-script Vids narration, automatic language/word checks and scene alignment."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
import requests
from script_generator import PROXIES
from native_audio import speech_tokens
from audio_quality import inspect


class SpeechMismatch(RuntimeError):
    pass


class NarrationNeedsReview(RuntimeError):
    pass


def check_scene_fits(duration):
    # The same audio gives the same length on every retry, so waiting would only block the channel's queue.
    if not 1<duration<=7.8:raise NarrationNeedsReview('Vids scene narration cannot fit the generated clip')


def prepare(script,run_dir,run_id):
    """Rephrase at most twice before any visual submission; never relax ASR checks."""
    from autonomous_video_pipeline import OUTPUT_DIR
    from script_generator import validate_script,save_script
    from script_review import audit,require_pass
    from migratoria_editor import llm
    if 'narration_voice' not in script:
        from voice_experiments import choose
        # Preserve earlier audio/documents when upgrading a running server.
        script['narration_voice']=('Holt' if (run_dir/'narration.wav').exists() else choose(os.getenv('MEDIA_BRAND','wrongdev'),run_id))
        save_script(script,run_dir)
    while True:
        try:return _prepare(script,run_dir,run_id)
        except SpeechMismatch:
            revision=script.get('narration_revision',0)
            submitted=(list((OUTPUT_DIR/'generation_routes').glob(run_id+'_s*.json'))+
                list(OUTPUT_DIR.glob('flow_'+run_id+'_s*.json'))+
                list(OUTPUT_DIR.glob('vids_'+run_id+'_s*.json')))
            if submitted:
                raise RuntimeError('Narration mismatch retained for review; visual generation already started')
            if revision>=2:
                raise NarrationNeedsReview('Narration mismatch retained for review; bounded rewrites exhausted')
            # A proper name can be pronounced differently on a fresh take. Retry
            # the exact script once, retaining the rejected audio and transcript.
            if script.get('narration_take',1)==1:
                archived=run_dir/'rejected_narration_take_1';archived.mkdir(exist_ok=True)
                for old in run_dir.glob('narration.*'):
                    if old.is_file():old.replace(archived/old.name)
                script['narration_take']=2;save_script(script,run_dir)
                continue
            repair_state=run_dir/'narration_repair_state.json'
            attempts=json.loads(repair_state.read_text()).get('attempts',0) if repair_state.exists() else 0
            if attempts>=2:raise NarrationNeedsReview('Narration mismatch retained for review; bounded repair requests exhausted')
            repair_state.write_text(json.dumps({'attempts':attempts+1}))
            actual=json.loads((run_dir/'narration.asr.json').read_text())
            result=llm('''Rewrite only the Russian narration fragments of this short video.
The generated voice did not say the exact intended words. Use short familiar words,
grammatical sentences and no abbreviations. Preserve every fact, negation, amount,
eligibility condition and the scene meaning. Do not copy ASR mistakes into the script.
Keep three to sixteen words per scene. Russian letters and spaces only. Do not change
the last scene CTA. You MUST change the wording of earlier fragments: choose simpler
synonyms or a different sentence structure rather than returning the same text.
Do not try another phonetic spelling of a difficult company name. Keep that name
on screen and replace it in speech with its precise Russian role, or omit the
spoken name when the subject stays unambiguous. Keep all quantities unchanged.
Return JSON {"voiceovers":["one fragment per existing scene"]}.''',
                json.dumps({'source':script['source_text'],'scenes':script['scenes'],
                    'actual_transcription':actual.get('text','')},ensure_ascii=False))
            voices=result.get('voiceovers',[])
            if len(voices)!=len(script['scenes']):raise RuntimeError('Narration repair changed the scene count')
            revised=json.loads(json.dumps(script))
            for scene,voice in zip(revised['scenes'][:-1],voices[:-1]):scene['voiceover_ru']=voice
            revised=validate_script(revised)
            if revised['voiceover_ru']==script['voiceover_ru']:raise RuntimeError('Narration repair did not change the failed wording')
            revised['narration_revision']=revision+1
            reviewed=audit(revised,run_dir)
            try:require_pass(reviewed)
            except ValueError:
                if attempts+1>=2:raise NarrationNeedsReview('Narration repair failed factual review: '+reviewed.get('reason','Unsupported revised script')) from None
                continue
            archived=run_dir/('rejected_narration_'+str(revision));archived.mkdir(exist_ok=True)
            save_script(script,archived)
            for old in run_dir.glob('narration.*'):
                if old.is_file():old.replace(archived/old.name)
            save_script(revised,run_dir)
            script.clear();script.update(revised)


def _prepare(script,run_dir,run_id):
    from autonomous_video_pipeline import BASE_DIR,container_path,docker_command,probe,write_json
    voice_id=run_id+'_'+hashlib.sha256(script['voiceover_ru'].encode()).hexdigest()[:10]
    voice=script.get('narration_voice','Holt')
    if voice!='Holt':voice_id+='_'+voice.replace(' ','')
    if script.get('narration_take',1)>1:voice_id+='_t'+str(script['narration_take'])
    from browser_process import run as run_browser
    result=run_browser(['docker','exec','video_factory_auth','python3','-u','/app/scripts/vids_voiceover.py',
        voice_id,script['voiceover_ru'],voice],capture_output=True,text=True,timeout=900)
    if result.returncode:
        receipt_path=BASE_DIR/'output'/f'vids_voice_{voice_id}.json'
        failed=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if (failed.get('script')==script['voiceover_ru'] and failed.get('voice')==voice
                and failed.get('failure_kind')=='voiceover_not_inserted'
                and failed.get('terminal_failure_observed') and failed.get('reconciliation_observations')):
            raise NarrationNeedsReview('Vids did not insert audio after bounded, observed recovery; source retained for review')
        raise RuntimeError('Vids voiceover retained for retry: '+result.stderr[-700:])
    receipt=json.loads(result.stdout.strip().splitlines()[-1])
    source=BASE_DIR/'output'/Path(receipt['file']).relative_to('/data/output')
    if receipt['status']!='completed' or hashlib.sha256(source.read_bytes()).hexdigest()!=receipt['sha256']:
        raise RuntimeError('Vids narration file does not match its receipt')
    wave=run_dir/'narration.wav'
    docker_command('ffmpeg','-v','error','-y','-i',container_path(source),'-vn','-ac','1','-ar','16000',container_path(wave))
    digest=hashlib.sha256(wave.read_bytes()).hexdigest()
    asr_path=run_dir/'narration.asr.json'
    data=json.loads(asr_path.read_text()) if asr_path.exists() else {}
    if data.get('audio_sha256')!=digest:
        with wave.open('rb') as stream:
            r=requests.post('https://api.groq.com/openai/v1/audio/transcriptions',
                headers={'Authorization':'Bearer '+os.environ['GROQ_API_KEY']},
                files={'file':(wave.name,stream,'audio/wav')},data={'model':'whisper-large-v3',
                'response_format':'verbose_json','temperature':0,'timestamp_granularities[]':['word','segment']},proxies=PROXIES,timeout=90)
        if not r.ok:raise RuntimeError(f'Vids narration verification unavailable: HTTP {r.status_code}')
        data=r.json();data.update(audio_sha256=digest,language_detection='automatic');write_json(asr_path,data)
    expected=speech_tokens(script['voiceover_ru'])
    if data.get('language','').lower()!='russian' or speech_tokens(data.get('text',''))!=expected:
        # Match the established native-audio path: a second independent ASR
        # decoding may resolve a transcription error, but must match exactly.
        second=run_dir/'narration.whisper-large-v3-turbo.json'
        alternate=json.loads(second.read_text()) if second.exists() else {}
        if alternate.get('audio_sha256')!=digest:
            with wave.open('rb') as stream:
                r=requests.post('https://api.groq.com/openai/v1/audio/transcriptions',
                    headers={'Authorization':'Bearer '+os.environ['GROQ_API_KEY']},
                    files={'file':(wave.name,stream,'audio/wav')},data={'model':'whisper-large-v3-turbo',
                    'response_format':'verbose_json','temperature':0,'timestamp_granularities[]':['word','segment']},proxies=PROXIES,timeout=90)
            if not r.ok:raise RuntimeError(f'Second narration transcription unavailable: HTTP {r.status_code}')
            alternate=r.json();alternate.update(audio_sha256=digest,language_detection='automatic',verification_model='whisper-large-v3-turbo')
            write_json(second,alternate)
        if alternate.get('language','').lower()=='russian' and speech_tokens(alternate.get('text',''))==expected:
            write_json(run_dir/'narration.whisper-large-v3.json',data)
            data=alternate;write_json(asr_path,data)
    if data.get('language','').lower()!='russian' or speech_tokens(data.get('text',''))!=expected:
        raise SpeechMismatch('Vids narration differs from the Russian script; no video credits used')
    quality=inspect([wave],run_dir/'narration.quality.json')
    clips=quality['clips']
    if (len(clips)!=1 or not clips[0].get('native_russian') or clips[0].get('naturalness_1_to_5',0)<4
            or clips[0].get('confidence',0)<0.8 or quality.get('consistent_speaker') is not True):
        raise SpeechMismatch('Narration pronunciation or naturalness failed quality review')
    words=data.get('words',[])
    if not words:raise RuntimeError('Narration has no timing evidence')
    # Locate complete scene boundaries in actual ASR words, preserving number values and negations.
    ends=[];prefix=''
    for scene in script['scenes']:
        prefix=(prefix+' '+scene['voiceover_ru']).strip();expected=speech_tokens(prefix)
        matches=[i for i in range(1,len(words)+1) if speech_tokens(' '.join(w['word'] for w in words[:i]))==expected]
        if len(matches)!=1:raise RuntimeError('Narration scene boundary is ambiguous')
        ends.append(matches[0])
    full_duration=float(probe(wave)['format']['duration'])
    bounds=[max(0,float(words[0]['start'])-0.15)]
    for end in ends[:-1]:bounds.append((float(words[end-1]['end'])+float(words[end]['start']))/2)
    bounds.append(min(full_duration,float(words[-1]['end'])+0.18))
    prepared=[]
    for index,scene in enumerate(script['scenes'],1):
        start,end=bounds[index-1:index+1];duration=end-start
        check_scene_fits(duration)
        audio=run_dir/f'scene_{index:02d}.wav'
        docker_command('ffmpeg','-v','error','-y','-i',container_path(wave),'-ss',f'{start:.4f}',
            '-t',f'{duration:.4f}',container_path(audio))
        first=0 if index==1 else ends[index-2]
        scene['_speech_words']=[{'word':w['word'],'start':max(0,float(w['start'])-start),
            'end':min(duration,float(w['end'])-start)} for w in words[first:ends[index-1]]]
        prepared.append((scene,audio,duration))
    write_json(run_dir/'narration.alignment.json',{'source_sha256':digest,'bounds':bounds,
        'script':script['voiceover_ru'],'tokens_match':True,'document_url':receipt['document_url']})
    return prepared
