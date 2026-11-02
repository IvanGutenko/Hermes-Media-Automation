"""The Studio uploader picks the video language per job; Russian stays the default."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
try:
    import youtube_studio_uploader as uploader
except ImportError:  # playwright lives in the browser container only
    uploader = None


@unittest.skipIf(uploader is None, 'needs the browser container')
class LanguageTests(unittest.TestCase):
    def test_russian_and_english_patterns(self):
        shown_ru, option_ru = uploader.LANGUAGE_PATTERNS['ru']
        shown_en, option_en = uploader.LANGUAGE_PATTERNS['en']
        self.assertTrue(re.search(shown_ru, 'Язык видео Русский') and re.search(option_ru, 'Русский'))
        self.assertTrue(re.search(shown_en, 'Video language English') and re.search(option_en, 'English'))
        self.assertTrue(re.search(option_en, 'English (United States)'))
        self.assertFalse(re.search(option_en, 'English-based Creole'))
        self.assertFalse(re.search(shown_ru, 'English'))

    def test_plain_english_is_preferred_over_regional_variants(self):
        _, option_en = uploader.LANGUAGE_PATTERNS['en']
        listed = ['English (India)', 'English (United Kingdom)', 'English (United States)', 'English']
        self.assertTrue(all(re.search(option_en, n) for n in listed))
        pick = next((listed.index(n) for n in uploader.LANGUAGE_PREFERENCE['en'] if n in listed), 0)
        self.assertEqual(listed[pick], 'English')
        pick = next((listed.index(n) for n in uploader.LANGUAGE_PREFERENCE['en'] if n in listed[:3]), 0)
        self.assertEqual(listed[pick], 'English (United States)')

    def test_every_supported_language_has_both_patterns(self):
        for pair in uploader.LANGUAGE_PATTERNS.values():self.assertEqual(len(pair), 2)


if __name__ == '__main__':unittest.main()
