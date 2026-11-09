"""Exercise the common pipeline: motion shots must not submit video generations."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import autonomous_video_pipeline as pipeline
import script_generator as generator
from test_video_factory import package


class PipelineRouting(unittest.TestCase):
    def exercise(self,hybrid):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'output';data=root/'data'
            script=generator.validate_script(package())
            if not hybrid:script.pop('visual_mode')
            source='Original source'
            script.update(source_text=source,source_sha256=hashlib.sha256(source.encode()).hexdigest())
            def script_result(topic,target):
                generator.save_script(script,target);return script
            def audio_result(text,target):target.write_bytes(text.encode());return 2.0
            submissions=[]
            def footage(prompt,title,asset_run,**kwargs):
                submissions.append(asset_run)
                raw=out/(asset_run+'.mp4');raw.write_bytes(asset_run.encode())
                return raw,{'media_id':asset_run,'sha256':hashlib.sha256(raw.read_bytes()).hexdigest()}
            def motion(scene,audio,subtitles,duration,target,**kwargs):
                target.write_bytes(('motion'+str(scene['id'])).encode());return {'engine':'test'}
            def generated(raw,audio,subtitles,duration,target):target.write_bytes(raw.read_bytes())
            def concat(*args):
                (out/'runs'/'routing'/'short.mp4').write_bytes(b'assembled')
                return SimpleNamespace(stdout='')
            verdict={'publishable':True,'score_1_to_5':5,'scenes':[
                {'id':i,'matches_narration':True,'meaningful_action_or_edit':True} for i in range(1,4)]}
            with patch.dict(os.environ,MEDIA_AUDIO_MODE='gemini',MEDIA_BRAND='wrongdev'), \
                 patch.object(pipeline,'OUTPUT_DIR',out),patch.object(pipeline,'DATA_DIR',data), \
                 patch.object(pipeline,'generate_shorts_script',side_effect=script_result), \
                 patch.object(pipeline,'create_voiceover_audio',side_effect=audio_result), \
                 patch.object(pipeline,'probe',return_value={'format':{'duration':'6'},'streams':[{'codec_type':'audio'}]}), \
                 patch.object(pipeline,'docker_command',side_effect=concat), \
                 patch.object(pipeline,'render_scene',side_effect=generated), \
                 patch('video_provider_pool.generate_video_file',side_effect=footage), \
                 patch('motion_design.render',side_effect=motion), \
                 patch('script_review.audit',return_value={'supported':True,'allowed_topic':True}), \
                 patch('video_quality.inspect',return_value=verdict),patch('builtins.print'):
                result=pipeline.run_pipeline(custom_topic=source,job_id='routing')
            return submissions,result

    def test_hybrid_submits_only_the_scene_that_needs_fresh_footage(self):
        submissions,result=self.exercise(True)
        self.assertEqual(submissions,['routing_s1'])
        self.assertEqual([s['visual_type'] for s in result['scenes']],['hybrid','motion','motion'])
        self.assertEqual([s['raw_video'] is None for s in result['scenes']],[False,True,True])

    def test_legacy_plan_keeps_its_three_independent_generated_clips(self):
        submissions,result=self.exercise(False)
        self.assertEqual(submissions,['routing_s1','routing_s2','routing_s3'])
        self.assertEqual([s['visual_type'] for s in result['scenes']],['video','video','video'])


if __name__=='__main__':unittest.main()
