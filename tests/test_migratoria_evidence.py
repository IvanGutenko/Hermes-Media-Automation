import sys,unittest,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from migratoria_editor import valid_fact_quotes
import migratoria_editor as editor

class FactEvidenceTests(unittest.TestCase):
    def test_search_fallback_finds_primary_links_without_accepting_snippets_or_lookalikes(self):
        html='''<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnews.klm.com%2Ffree-wifi%2F">Unverified claim</a>
        <a href="https://news.klm.com/free-wifi/">Duplicate</a>
        <a href="https://klm.com.evil.test/news/">Lookalike</a>'''
        with patch.object(editor,'get',return_value=Mock(text=html)):
            self.assertEqual(editor.search_primary('KLM Starlink site:klm.com'),['https://news.klm.com/free-wifi/'])

    def test_verified_publisher_domains_do_not_allow_lookalikes(self):
        for url in ('https://www.nps.gov/articles/glacier-bay.htm','https://news.klm.com/story/',
                    'https://www.metsa-hanno.com/en/'):
            self.assertTrue(editor.primary(url),url)
        for url in ('https://nps.gov.example.com/story','https://fake-klm.com/story','https://klm.com.evil.test/'):
            self.assertFalse(editor.primary(url),url)

    def test_rejected_json_uses_another_model_then_still_requires_valid_json(self):
        bad=Mock(ok=False,status_code=400);bad.json.return_value={'error':{'message':'Failed to generate JSON. Please adjust your prompt.'}}
        good=Mock(ok=True,status_code=200);good.json.return_value={'choices':[{'message':{'content':'{"publishable":false}'}}]}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(editor.os.environ,GROQ_API_KEY='test'),patch.object(editor,'BASE_DIR',Path(tmp)),patch.object(editor.requests,'post',side_effect=[bad,good]) as post,patch('text_llm.ORDER',('groq',)),patch('text_llm.COOLDOWNS',Path(tmp)/'cooldowns.json'),patch('content_feedback.editorial_brief',return_value=''):
            result=editor.llm('Return JSON','Public lead')
        self.assertEqual(result,{'publishable':False});self.assertEqual(post.call_count,2)

    def test_income_estimate_from_a_lead_is_not_proved_by_a_salary_formula(self):
        doc={'url':'https://example.test','text':'At least twice the average monthly net salary.'}
        draft={'text':'Нужно около €3 400 в месяц.','facts':[{'url':doc['url'],'quote':doc['text']}]}
        self.assertFalse(valid_fact_quotes(draft,[doc]))
    def test_exact_primary_fare_is_allowed_with_formatting_equivalence(self):
        doc={'url':'https://example.test','text':'Минимальная стоимость 28 445 ₽ в одну сторону.'}
        draft={'text':'Билет от 28\u202f445 ₽.','facts':[{'url':doc['url'],'quote':doc['text']}]}
        self.assertTrue(valid_fact_quotes(draft,[doc]))
    def test_inserted_ellipsis_is_not_an_exact_source_quote(self):
        doc={'url':'https://example.test','text':'Flight departs at night and arrives the next day.'}
        draft={'text':'Прилёт на следующий день.','facts':[{'url':doc['url'],'quote':'Flight departs ... next day.'}]}
        self.assertFalse(valid_fact_quotes(draft,[doc]))

class CisNewsTests(unittest.TestCase):
    def test_cis_official_sites_count_as_primary_but_lookalikes_do_not(self):
        from primary_publishers import primary_host
        for host in ('www.gov.kz','www.gov.uz','gov.kg','mfa.gov.by','www.gov.am','airastana.com','azal.az'):
            self.assertTrue(primary_host(host),host)
        for host in ('gov.kg.evil.test','notgov.kg','tengrinews.kz'):
            self.assertFalse(primary_host(host),host)

    def test_a_practical_cis_post_becomes_a_lead_but_general_news_does_not(self):
        self.assertTrue(editor.CIS_PRACTICAL.search('Казахстан продлил безвизовый въезд для граждан ряда стран на 30 дней'))
        self.assertTrue(editor.CIS_PRACTICAL.search('Ташкент: новые рейсы авиакомпании в Стамбул со следующего месяца'))
        self.assertFalse(editor.CIS_PRACTICAL.search('Президент провел перестановки в дипкорпусе: назначены новые послы'))

    def test_acute_cis_topics_are_still_removed_by_the_owner_policy(self):
        from editorial_policy import allowed
        self.assertTrue(allowed('Казахстан упростил въезд для туристов из ряда стран'))
        for text in ('В стране объявлена мобилизация, въезд ограничен','Новые санкции затронули авиарейсы','Военные учения вблизи границы'):
            self.assertFalse(allowed(text),text)

    def test_the_editor_is_told_that_cis_news_is_in_scope(self):
        self.assertIn('Kazakhstan',editor.CIS_FOCUS);self.assertIn('war, politics',editor.CIS_FOCUS)
        self.assertEqual({'tengrinews','gazetauz'},set(editor.CIS_NEWS_CHANNELS))
        self.assertTrue(set(editor.CIS_NEWS_CHANNELS)<=set(editor.CHANNELS))

if __name__=='__main__':unittest.main()
