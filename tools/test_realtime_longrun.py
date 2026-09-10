import unittest
from tools.realtime_longrun_probe import MeasuredQueue, process_memory
from tools.verify_realtime_longrun import memory_gate, percentile, RULES, MIB


class LongRunTests(unittest.TestCase):
    def samples(self, slope=0):
        return [dict(elapsed_s=s,memory=dict(rss_bytes=40*MIB+(s-120)/60*slope*MIB,
            private_bytes=32*MIB+(s-120)/60*slope*MIB)) for s in range(120,840)]

    def test_stable_platform_passes_both_metrics(self):
        result=memory_gate(self.samples(),RULES)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['minutes'],12)

    def test_sustained_growth_fails_even_below_absolute_budget(self):
        result=memory_gate(self.samples(.5),RULES)
        self.assertEqual(result['status'],'FAIL')
        self.assertGreater(result['metrics']['private_bytes']['slope_mib_per_min'],.25)

    def test_short_or_missing_metric_cannot_claim_stability(self):
        self.assertEqual(memory_gate(self.samples()[:100],RULES)['status'],'INSUFFICIENT_DURATION')
        rows=self.samples()
        for row in rows: row['memory']['private_bytes']=None
        self.assertEqual(memory_gate(rows,RULES)['status'],'FAIL')

    def test_queue_peak_survives_drain_and_does_not_change_capacity(self):
        queue=MeasuredQueue(2)
        queue.put(1); queue.put(2); queue.get(); queue.get()
        self.assertEqual((queue.peak,queue.qsize(),queue.maxsize),(2,0,2))

    def test_flat_but_oversized_memory_still_fails(self):
        rows=self.samples()
        for row in rows: row['memory']['rss_bytes']=300*MIB
        self.assertEqual(memory_gate(rows,RULES)['status'],'FAIL')

    def test_warmup_and_incomplete_minute_cannot_bias_platform(self):
        rows=[dict(elapsed_s=s,memory=dict(rss_bytes=200*MIB,private_bytes=200*MIB)) for s in range(120)]
        rows+=self.samples()
        rows+=[dict(elapsed_s=840,memory=dict(rss_bytes=200*MIB,private_bytes=200*MIB))]
        self.assertEqual(memory_gate(rows,RULES)['status'],'PASS')

    def test_process_rss_and_nearest_rank_percentile(self):
        self.assertGreater(process_memory()['rss_bytes'],0)
        self.assertEqual(percentile([1,2,3,99],.99),99)
        self.assertIsNone(percentile([], .99))


if __name__=='__main__': unittest.main()
