"""A stalled editorial helper cannot erase another task's durable progress."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import media_research as r


class Isolation(unittest.TestCase):
    def test_a_real_stalled_subprocess_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/'slow.py'
            script.write_text('import time\ntime.sleep(20)\n')
            with patch.object(r,'__file__',str(script)):
                self.assertEqual(r.execute('slow',.05),124)

    def test_a_timeout_is_saved_and_the_next_task_still_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();path=root/'data/research_status.json'
            seen=[]
            def execute(name,timeout):
                seen.append(name)
                if name=='stalled':return 124
                status=json.loads(path.read_text())
                self.assertEqual(status['stalled']['status'],'retry_on_next_tick')
                status[name]={'status':'completed','checked_at':1};path.write_text(json.dumps(status))
                return 0
            with patch.object(r,'BASE_DIR',root),patch.object(r,'tasks',return_value=[('stalled',None,1),('healthy',None,1)]),patch.object(r,'execute',side_effect=execute),patch.object(sys,'argv',['media_research.py']):
                self.assertEqual(r.main(),1)
            self.assertEqual(seen,['stalled','healthy'])
            state=json.loads(path.read_text())
            self.assertEqual(state['healthy']['status'],'completed')
            self.assertEqual(state['stalled']['status'],'retry_on_next_tick')

    def test_the_editor_stops_between_candidates_inside_its_kill_limit(self):
        import migratoria_editor as me
        self.assertLess(r.EDITOR_BUDGET+300,r.EDITOR_TIMEOUT+1)
        self.assertEqual(r.editor.keywords,{'budget':r.EDITOR_BUDGET})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(me,'DB',Path(tmp)/'m.db'):
                with me.db() as con:
                    for i in range(3):
                        con.execute("INSERT INTO candidates(url,text,date,views,links) VALUES(?,?,?,?,?)",
                            (f'https://t.me/x/{i}','текст','2999-01-01','1','[]'))
                calls=[];clock=iter([0])
                import growth_actions
                with patch.object(me,'allowed',side_effect=lambda t:calls.append(t) or False),\
                        patch.object(growth_actions,'choose',return_value=None),\
                        patch.object(me.time,'time',side_effect=lambda:next(clock,10**6)):
                    me.prepare_one(budget=540)
                self.assertEqual(calls,[])
                with patch.object(me,'allowed',side_effect=lambda t:calls.append(t) or False),\
                        patch.object(growth_actions,'choose',return_value=None):
                    me.prepare_one()
                self.assertEqual(len(calls),3)  # control: without a budget every candidate is read


if __name__=='__main__':unittest.main()
