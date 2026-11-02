#!/usr/bin/env python3
"""Listen to actual generated audio; transcript equality alone cannot judge accents."""
import base64
import hashlib
import json
import os
from pathlib import Path
import requests
from script_generator import PROXIES


def normalize(data):
    while isinstance(data,list) and len(data)==1 and isinstance(data[0],dict) and 'clips' in data[0]:data=data[0]
    if isinstance(data,list):data={'clips':data,'consistent_speaker':None,'notes':'Model did not report speaker consistency'}
    if isinstance(data,dict) and len(data.get('clips',[]))==1 and 'clips' in data['clips'][0]:
        nested=normalize(data['clips']);data={**data,**nested}
    if not isinstance(data,dict) or not isinstance(data.get('clips'),list) or not all('index' in c for c in data['clips']):
        raise RuntimeError('Audio review returned an invalid structure; raw response retained')
    return data


def inspect(paths, result_path):
    hashes=[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths]
    target=Path(result_path)
    if target.exists():
        old=json.loads(target.read_text())
        if old.get('audio_hashes')==hashes:return normalize(old)
    parts=[{'text':'''Listen to each actual audio clip. Identify the spoken language from sound,
and assess pronunciation: native standard Russian, Russian with Ukrainian accent,
Ukrainian, or another accent. Do not infer accent solely from vocabulary. Give concrete
phonetic observations and confidence. Transcribe the words you actually hear, including
proper names, independently of any expected script. Flag robotic delivery and inconsistent speakers.
Return JSON {"clips":[{"index":1,"language":"...","accent":"...",
"native_russian":bool,"transcript_ru":"...","phonetic_evidence":"...","confidence":0.0,
"naturalness_1_to_5":1}],"consistent_speaker":bool,"notes":"..."}.
Be honest when pronunciation cannot reliably be determined. Do not assume Russian
just because the client wants Russian.'''}]
    for i,path in enumerate(paths,1):
        parts += [{'text':'Clip '+str(i)}, {'inlineData':{'mimeType':'audio/wav',
            'data':base64.b64encode(Path(path).read_bytes()).decode()}}]
    from media_review_client import review
    result,model=review(parts,target.with_suffix('.attempts.json'))
    target.with_suffix('.response.json').write_text(json.dumps(result,ensure_ascii=False))
    data=normalize(result)
    data.update(audio_hashes=hashes,model=model)
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    return data


if __name__=='__main__':
    import sys
    print(json.dumps(inspect(sys.argv[2:],sys.argv[1]),ensure_ascii=False,indent=2))
