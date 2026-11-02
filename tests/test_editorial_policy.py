import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from editorial_policy import allowed


class OwnerTopicPolicyTests(unittest.TestCase):
    def test_owner_example_is_excluded(self):
        self.assertFalse(allowed('Релокантам в Сербии прислали повестки из военкоматов России'))

    def test_rephrased_sensitive_topic_is_still_excluded(self):
        self.assertFalse(allowed('Ограничения для оппозиции и новые санкции'))

    def test_criminal_prosecution_lead_is_excluded(self):
        self.assertFalse(allowed('Следователи сослались на VPN как основание для заключения под стражу'))

    def test_russian_regulator_politics_are_excluded(self):
        for topic in ('Роскомнадзор заблокировал песню, созданную ИИ', 'РКН вводит новые блокировки'):
            with self.subTest(topic=topic):self.assertFalse(allowed(topic))

    def test_practical_relocation_topics_remain_available(self):
        for topic in ('Как перевезти кошку в салоне самолёта', 'Стипендия для учёбы в Китае',
                      'Аренда жилья в Белграде', 'Виза для удалённой работы в Испании',
                      'Исследователи улучшили Claude Code', 'Следовательно, тесты стали быстрее',
                      'Что посмотреть в Бухаресте',
                      'Как настроить VPN на роутере','Как избежать двойной оплаты багажа'):
            with self.subTest(topic=topic):self.assertTrue(allowed(topic))


if __name__=='__main__':unittest.main()
