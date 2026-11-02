import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from native_audio import speech_tokens

class NativeNarrationTests(unittest.TestCase):
    def test_asr_digits_match_spoken_date_without_losing_value(self):
        expected='Ищите файл с датой восемь октября две тысячи двадцать шестого'
        self.assertEqual(speech_tokens(expected),speech_tokens('Ищите файл с датой 8 октября 2026.'))
    def test_thousands_separator_is_one_value_and_the_value_still_counts(self):
        self.assertEqual(speech_tokens('бонус десять тысяч баллов'),speech_tokens('бонус 10 000 баллов'))
        self.assertEqual(speech_tokens('бонус десять тысяч баллов'),speech_tokens('бонус 10\u00a0000 баллов'))
        self.assertEqual(speech_tokens('пять тысяч миль'),speech_tokens('5 тысяч миль'))
        self.assertNotEqual(speech_tokens('бонус десять тысяч баллов'),speech_tokens('бонус 20 000 баллов'))
        self.assertNotEqual(speech_tokens('бонус десять тысяч баллов'),speech_tokens('бонус 1 000 баллов'))
        self.assertNotEqual(speech_tokens('пять тысяч миль'),speech_tokens('6 тысяч миль'))
    def test_declined_numbers_match_digits_and_keep_their_value(self):
        for spoken,written,wrong in (('от тридцати дней','от 30 дней','от 13 дней'),('с третьего ноября','с 3 ноября','с 13 ноября'),
                                     ('три к одному','3 к 1','3 к 2'),('с восемнадцати лет','с 18 лет','с 16 лет')):
            self.assertEqual(speech_tokens(spoken),speech_tokens(written))
            self.assertNotEqual(speech_tokens(spoken),speech_tokens(wrong))
    def test_percent_sign_is_the_spoken_unit(self):
        self.assertEqual(speech_tokens('скидка двадцати пяти процентов'),speech_tokens('скидка 25%'))
        self.assertEqual(speech_tokens('двадцать пять процентов'),speech_tokens('25 процентов'))
        self.assertNotEqual(speech_tokens('двадцать пять процентов'),speech_tokens('26%'))
        self.assertNotEqual(speech_tokens('двадцать пять процентов'),speech_tokens('25 дней'))
    def test_airline_names_written_in_latin_match_only_their_spoken_name(self):
        self.assertEqual(speech_tokens('мили Бритиш Эйрвэйз'),speech_tokens('мили British Airways'))
        self.assertEqual(speech_tokens('бонус Юнайтед'),speech_tokens('бонус United'))
        self.assertNotEqual(speech_tokens('бонус Юнайтед'),speech_tokens('бонус Etihad'))
    def test_a_different_word_is_still_a_mismatch(self):
        self.assertNotEqual(speech_tokens('код перевозчика'),speech_tokens('код переводчика'))
    def test_changed_date_is_not_equivalent(self):
        self.assertNotEqual(speech_tokens('восемь октября'),speech_tokens('18 октября'))
    def test_asr_digit_matches_declined_weight_but_keeps_value(self):
        self.assertEqual(speech_tokens('легче восьми килограммов'),speech_tokens('легче 8 килограммов'))
        self.assertNotEqual(speech_tokens('легче восьми килограммов'),speech_tokens('легче 18 килограммов'))
    def test_negation_is_preserved(self):
        self.assertNotEqual(speech_tokens('Виза не нужна'),speech_tokens('Виза нужна'))
    def test_extra_generated_narration_is_not_accepted(self):
        self.assertNotEqual(speech_tokens('Проверьте бюджет перед подачей'),speech_tokens('Привет друзья проверьте бюджет перед подачей'))
    def test_asr_latin_brand_maps_only_to_its_spoken_name(self):
        self.assertEqual(speech_tokens('OpenAI'),speech_tokens('Опен Эй Ай'))
    def test_confirmed_channel_name_split_is_equivalent_only_in_cta(self):
        self.assertEqual(speech_tokens('в канале Вронг Дев'),speech_tokens('в канале в Ронг Дев'))
        self.assertEqual(speech_tokens('в канале Вронг Дев'),speech_tokens('в канале в Ронгдев'))
        self.assertEqual(speech_tokens('Вронг Дев'),speech_tokens('Вронгдев'))
        self.assertNotEqual(speech_tokens('в канале Вронг Дев'),speech_tokens('не в канале в Ронг Дев'))
        self.assertNotEqual(speech_tokens('проект Вронг Дев'),speech_tokens('проект в Ронг Дев'))

if __name__=='__main__':unittest.main()
