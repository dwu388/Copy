import json
from pathlib import Path
import re,sqlite3,unittest
import numpy as np
import pandas as pd
from learning.core import read_payload,score_payload,causal_training
from learning.policy_labels import reference_net_roi
from learning.strategy_replay import Wallet
from learning.backtest_trader_guard import simulate

ROOT=Path(__file__).resolve().parents[2]

class IntegratedPaperTests(unittest.TestCase):
    def paper(self):
        # Public base weights plus a fixed synthetic map; no private model in CI.
        p=read_payload(ROOT/'app/src/main/assets/adaptive_hybrid_v2.json.gz')
        p['fixtures']=[f for f in p['fixtures'] if f['trader'].lower() not in {t.lower() for t in p.get('excludedTraders',[])}]
        p['paperOnly']=True
        p['probabilityCalibration']=dict(schemaVersion='platt_oos_v1',slope=.8,intercept=-.2)
        for f in p['fixtures']:
            value=np.clip(f['probability'],1e-12,1-1e-12)
            f['probability']=1/(1+np.exp(-(.8*np.log(value/(1-value))-.2)))
            f['confidenceRank']=float(np.searchsorted(p['confidenceReference'],f['probability'],side='right')/len(p['confidenceReference']))
        return p
    def test_synthetic_calibration_fixtures_match(self):
        p=self.paper()
        self.assertTrue(p['paperOnly']);self.assertGreaterEqual(p['probabilityCalibration']['slope'],0)
        frame=pd.DataFrame([dict(trader=f['trader'],entry_market_cap=f['marketCap'],source_buy_usd=f['sourceBuyUsd']) for f in p['fixtures']])
        scores=score_payload(p,frame)
        np.testing.assert_allclose(scores.probability,[f['probability'] for f in p['fixtures']],atol=1e-12)
        np.testing.assert_allclose(scores.predicted_roi,[f['predictedRoi'] for f in p['fixtures']],atol=1e-12)
    def test_net_reference_never_charges_an_unfunded_exit_fee(self):
        f=pd.DataFrame(dict(source_buy_usd=[500],realized_source_roi=[-.99999]))
        self.assertAlmostEqual(-(25+.95)/25,reference_net_roi(f)[0])
    def test_strict_margin_rejects_low_roi_and_rule_fee_ceiling(self):
        p=self.paper();row=dict(trader='test',coin='X',source_amount=2000.,market_cap=100.,post_time=100,captured_time=100,opportunity_id='a')
        w=Wallet(p['config'],0,strict_margin=True)
        w.buy(row,dict(probability=.9,confidence=1,predicted_roi=-.01),True)
        self.assertEqual([],w.positions)
        row['source_amount']=500
        w=Wallet(p['config'],0,strict_margin=True,use_forecast=False)
        w.buy(row,dict(probability=0,confidence=0,predicted_roi=100),True)
        self.assertEqual([],w.positions)
    def test_circuit_marks_losses_and_keeps_sells_enabled(self):
        p=self.paper();w=Wallet(p['config'],0,strict_margin=True,loss_limit=.02)
        row=dict(trader='test',coin='X',source_amount=2000.,market_cap=100.,post_time=100,captured_time=100,opportunity_id='a')
        w.buy(row,dict(probability=.9,confidence=1,predicted_roi=1),True)
        w.expire(200,lambda *args:50.)
        self.assertTrue(w.entries_paused)
        w.sell(dict(row,post_time=300,captured_time=300,market_cap=50.))
        self.assertFalse(w.positions)
        self.assertAlmostEqual(948.10,w.cash)
        w.expire(400,lambda *args:1000.)
        self.assertTrue(w.entries_paused)
    def test_new_tables_are_additive_and_do_not_erase_v2_wallet(self):
        text=(ROOT/'app/src/main/java/com/dwu/fomocontroller/data/StrategyDatabase.kt').read_text()
        statements=re.findall(r'db\.execSQL\(\s*"""(.*?)"""',text,re.S)
        db=sqlite3.connect(':memory:')
        base=[s for s in statements if s.strip().upper().startswith('CREATE TABLE')]
        for sql in base:
            if 'paper_sessions' not in sql and 'entry_circuit_events' not in sql:db.execute(sql)
        db.execute("INSERT INTO strategy_state VALUES (1,777,33,1000,0,0,'CAUTION',0,0)")
        for sql in base:
            if 'paper_sessions' in sql or 'entry_circuit_events' in sql:db.execute(sql)
        self.assertEqual((777.,33.),db.execute('SELECT cash,open_cost FROM strategy_state').fetchone())
        self.assertEqual(0,db.execute('SELECT COUNT(*) FROM paper_sessions').fetchone()[0])
        db.close()
