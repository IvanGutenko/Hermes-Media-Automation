import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from reddit_format import platform_body


class TelegramCtaTests(unittest.TestCase):
    def test_duplicate_telegram_handle_cannot_turn_into_a_reddit_user(self):
        self.assertEqual(platform_body('Полный отчёт: https://t.me/WrongDevp @WrongDevp'),
            'Полный отчёт: https://t.me/WrongDevp')

    def test_unrelated_mentions_and_facts_are_preserved(self):
        text='Двенадцать часов, а не шесть. @OtherUser\n\nhttps://t.me/WrongDevp'
        self.assertEqual(platform_body(text),text)

    def test_link_to_a_similarly_named_channel_is_not_equivalent(self):
        text='https://t.me/WrongDevpNews @WrongDevp'
        self.assertEqual(platform_body(text),text)


if __name__=='__main__':unittest.main()
