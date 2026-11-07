import tempfile
import time
import unittest
from history_store import HistoryStore

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = HistoryStore(['room'], self.tmp.name, max_bytes=65536)
        self.t = round(time.time(),6)
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def snapshot(self, **kw):
        value = dict(id='room', status='online', presence=True, count=1,
                     coordinate_support=True, unit='mm', targets=[dict(id='1',x=10,y=20)])
        value.update(kw)
        return value
    def test_stationary_order_and_boundaries(self):
        for offset in (4,0,2): self.store.append_snapshot(self.t+offset,self.snapshot())
        rows=self.store.query('room',self.t,self.t+4)['samples']
        self.assertEqual([r['t'] for r in rows],[self.t,self.t+2])
        self.assertEqual(rows[0]['targets'],rows[1]['targets'])
    def test_offline_and_empty(self):
        for offset,changes in enumerate(({}, {'count':0}, {'status':'unavailable'})):
            self.store.append_snapshot(self.t+offset,self.snapshot(**changes))
        rows=self.store.query('room',self.t,self.t+4)['samples']
        self.assertTrue(rows[0]['targets'])
        self.assertEqual(rows[1]['targets'],[])
        self.assertEqual(rows[2]['targets'],[])
    def test_uncertainty_and_unsupported(self):
        cases = ({'count':None}, {'presence':None}, {'position_count_consistent':False}, {'coordinate_support':False})
        for n, changes in enumerate(cases):
            self.store.append_snapshot(self.t+n,self.snapshot(**changes))
        rows=self.store.query('room',self.t,self.t+10)['samples']
        self.assertTrue(all(not r['targets'] for r in rows))
        self.assertIsNone(rows[0]['count'])
        self.assertIsNone(rows[1]['presence'])
        self.assertIs(rows[2]['position_count_consistent'],False)
        self.assertIs(rows[3]['coordinate_support'],False)
    def test_duplicate_slots(self):
        target=dict(id='1',x=10,y=20)
        with self.assertRaises(ValueError):
            self.store.append_snapshot(self.t,self.snapshot(count=2,targets=[target,target]))
    def test_removed_sensor_retention_and_isolation(self):
        self.store.append_snapshot(self.t-31*86400,self.snapshot())
        self.store.close()
        self.store=HistoryStore(['replacement'],self.tmp.name,max_bytes=65536)
        self.assertNotIn('room',self.store.coverage()['sensors'])
        with self.assertRaises(ValueError): self.store.query('room',self.t-1,self.t+1)
    def test_query_does_not_backfill_boundary(self):
        self.store.append_snapshot(self.t,self.snapshot())
        self.assertEqual(self.store.query('room',self.t+1,self.t+3)['samples'],[])
    def test_validation(self):
        for snap in (self.snapshot(id='../other'),self.snapshot(targets=[dict(id='1',x=float('nan'),y=0)]), self.snapshot(unit='x'*100)):
            with self.assertRaises(ValueError): self.store.append_snapshot(self.t,snap)
        with self.assertRaises(ValueError): self.store.query('unknown',0,1)
    def test_restart(self):
        self.store.append_snapshot(self.t,self.snapshot())
        self.store.close()
        self.store=HistoryStore(['room'],self.tmp.name,max_bytes=65536)
        self.assertEqual(self.store.coverage()['sensors']['room']['samples'],1)
    def test_retention(self):
        self.store.append_snapshot(self.t-31*86400,self.snapshot())
        self.store.append_snapshot(self.t-30*86400,self.snapshot())
        self.store.retention_cleanup(self.t)
        coverage=self.store.coverage()
        self.assertEqual(coverage['sensors']['room']['samples'],1)
        self.assertEqual(coverage['pruning']['age_pruned_rows'],1)
    def test_page_cap_and_truncation(self):
        for n in range(1500): self.store.append_snapshot(self.t+n,self.snapshot())
        self.assertGreater(self.store.coverage()['pruning'].get('size_pruned_rows',0),0)
        self.assertLessEqual(self.store.coverage()['database_bytes'],65536)
        self.assertTrue(self.store.query('room',self.t,self.t+2000,limit=1)['truncated'])

if __name__=='__main__': unittest.main()

