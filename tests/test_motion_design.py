"""Routing, freshness and source copy remain strict when only some shots need Veo."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import motion_design as motion
import autonomous_video_pipeline as pipeline
import script_generator as generator


def plan():
    return {'visual_mode':'hybrid','motion_palette':'cobalt','scenes':[
        {'visual_type':kind,'motion':{'layout':layout,'headline':'Заголовок',
            'items':['Первый','Второй'],'kicker':''},'on_screen_text_ru':'@WrongDevp'}
        for kind,layout in [('hybrid','spotlight'),('motion','workflow'),('motion','kinetic')]]}


class MotionContract(unittest.TestCase):
    def test_legacy_saved_script_is_unchanged(self):
        saved={'scenes':[{'video_prompt_en':'Original shot'}]}
        self.assertEqual(motion.validate_direction(copy.deepcopy(saved)),saved)

    def test_whole_video_cannot_silently_remain_generated(self):
        data=plan()
        for scene in data['scenes']:scene['visual_type']='video'
        with self.assertRaisesRegex(ValueError,'combine motion'):
            motion.validate_direction(data)

    def test_new_model_answer_must_supply_direction(self):
        with self.assertRaisesRegex(ValueError,'New scripts require'):
            generator._validated('{"scenes":[]}',{},'wrongdev',False)

    def test_same_layout_for_every_scene_is_rejected(self):
        data=plan()
        for scene in data['scenes']:scene['motion']['layout']='kinetic'
        with self.assertRaisesRegex(ValueError,'two motion layouts'):
            motion.validate_direction(data)

    def test_fresh_footage_is_mandatory_for_hybrid(self):
        with self.assertRaisesRegex(ValueError,'own fresh'):
            motion.render(plan()['scenes'][0],None,None,3,Path('unused.mp4'))

    def test_short_raw_video_is_not_looped_to_cover_speech(self):
        with patch.object(pipeline,'probe',return_value={'format':{'duration':'2'}}):
            with self.assertRaisesRegex(RuntimeError,'no looping'):
                motion.render(plan()['scenes'][0],None,None,3,Path('unused.mp4'),raw=Path('fresh.mp4'))

    def test_copy_is_escaped_and_composition_has_one_paused_clock(self):
        scene=plan()['scenes'][1];scene['motion']['items']=['<script>alert(1)</script>']
        page=motion.composition(scene,4)
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>alert(1)',page)
        self.assertIn('paused:true',page)
        self.assertIn('window.seek',page)
        self.assertNotIn('Math.random',page)

    def test_motion_does_not_duplicate_the_static_editorial_title(self):
        scene={'on_screen_text_ru':'Заголовок','voiceover_ru':'Вот короткая речь'}
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'speech.ass'
            pipeline.scene_subtitles(scene,3,target,show_label=False)
            text=target.read_text()
            self.assertNotIn('Заголовок',text)
            self.assertIn('Вот короткая речь',text)

    def test_brand_handle_is_bound_to_the_validated_script(self):
        data=plan();data['scenes'][-1]['motion']['kicker']='@Invented'
        motion.validate_direction(data)
        self.assertEqual(data['scenes'][-1]['motion']['kicker'],'@WrongDevp')


if __name__=='__main__':unittest.main()
