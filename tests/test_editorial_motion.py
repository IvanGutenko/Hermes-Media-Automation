"""Recorded caption timing and a bounded, source-bound editorial direction contract."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import editorial_motion as editorial
import motion_design as motion
from test_video_factory import package


class EditorialContract(unittest.TestCase):
    def test_saved_script_without_new_system_keeps_original_composition(self):
        scene=package()['scenes'][0]
        self.assertIn('class="sheet"',motion.composition(scene,4))
        self.assertNotIn('class="sheet"',motion.composition(scene,4,system=editorial.SYSTEM))

    def test_caption_word_times_come_from_actual_recording(self):
        scene={'voiceover_ru':'Первое второе','_speech_words':[
            {'word':'Первое','start':.3,'end':.7},{'word':'второе','start':1.2,'end':1.8}]}
        cues,source=editorial.speech_cues(scene,3)
        self.assertEqual(cues,scene['_speech_words'])
        self.assertEqual(source,'recorded_words')

    def test_estimated_timing_is_not_presented_as_recorded(self):
        cues,source=editorial.speech_cues({'voiceover_ru':'Первое второе'},3)
        self.assertEqual(source,'estimated_text_weights')
        self.assertTrue(0<=cues[0]['start']<cues[-1]['end']<=3)

    def test_invalid_recorded_timing_is_rejected(self):
        for start,end in [(-1,1),(2,1),(1,9),(float('nan'),1)]:
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):
                editorial.speech_cues({'_speech_words':[{'word':'Текст','start':start,'end':end}]},3)

    def test_source_caption_cannot_execute_javascript(self):
        scene=copy.deepcopy(package()['scenes'][1])
        scene['voiceover_ru']='</script><script>alert(1)</script>'
        page=motion.composition(scene,4,system=editorial.SYSTEM)
        self.assertNotIn('</script><script>alert(1)',page)
        self.assertIn('\\u003c/script',page)

    def test_v2_rejects_paragraphs_and_too_many_foreground_objects(self):
        for layout,items in [('kinetic',['x'*25]),('spotlight',['a','b','c']),('editor',['a'])]:
            data=package();data['scenes'][0]['motion'].update(layout=layout,items=items)
            with self.subTest(layout=layout),self.assertRaises(ValueError):
                motion.validate_direction(data)


if __name__=='__main__':unittest.main()
