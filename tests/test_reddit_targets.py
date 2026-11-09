import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import reddit_targets as r


class Destinations(unittest.TestCase):
    def test_same_author_has_separate_community_destinations(self):
        for name in ('WrongDev','Migratoriya'):
            job={'username':'ThinkScope','target':'r_'+name}
            self.assertEqual(r.configured(job),('r_'+name,'ThinkScope'))
            self.assertEqual(r.home(job)+'/submit','https://www.reddit.com/r/'+name+'/submit')
            self.assertEqual(r.permalink(job,'abc123'),'https://www.reddit.com/r/'+name+'/comments/abc123/')
        self.assertNotEqual(r.cursor_name('r_WrongDev'),r.cursor_name('r_Migratoriya'))
        self.assertEqual(r.cursor_name('r_wrongdev'),r.cursor_name('r_WrongDev'))

    def test_old_jobs_remain_in_their_original_profile(self):
        self.assertEqual(r.destination({'username':'ThinkScope'}),'u_ThinkScope')
        self.assertEqual(r.home({'username':'ThinkScope'}),'https://www.reddit.com/user/ThinkScope')
        self.assertFalse(r.same('r_WrongDev','u_ThinkScope'))

    def test_community_requires_explicit_author_and_matching_readback(self):
        with self.assertRaises(ValueError):r.configured({'target':'r_WrongDev'})
        self.assertTrue(r.matches_community('r_WrongDev','r/WrongDev'))
        for actual in (None,'u_ThinkScope','r/Migratoriya'):
            self.assertFalse(r.matches_community('r_WrongDev',actual))
        for target in ('WrongDev','r_WrongDev/submit','r_../WrongDev'):
            with self.assertRaises(ValueError):r.normalize(target)

if __name__=='__main__':unittest.main()
