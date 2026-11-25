"""Frozen integrated-paper comparison; never trains on the evaluated period.

The shipped artifact is evaluated only after its trainedThrough time. The older
holdout uses a separately rebuilt artifact, frozen before that holdout begins.
"""
from pathlib import Path
import argparse,json,tempfile
import pandas as pd
from learning.core import read_payload,sha256
from learning.build_paper_experiment import build
from learning.backtest_trader_guard import read_raw,score_buys,simulate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('history',type=Path);p.add_argument('latest',type=Path)
    p.add_argument('--paper-model',type=Path,default=Path('app/src/main/assets/corrected_paper_v1.json.gz'))
    p.add_argument('--bootstrap',type=Path,default=Path('app/src/main/assets/adaptive_hybrid_v2.json.gz'))
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();old=read_raw(a.history);latest=read_raw(a.latest)
    original=read_payload(a.bootstrap);shipped=read_payload(a.paper_model)
    cut=int(pd.Timestamp('2026-10-24',tz='UTC').timestamp()*1000)
    report=dict(schema='integrated_paper_v1',inputs={k:sha256(v) for k,v in [('history',a.history),('latest',a.latest),('paper_model',a.paper_model),('bootstrap',a.bootstrap)]},
        preset=dict(max_market_cap_exclusive=50000,min_source_usd=1000,loss_pause_fraction=.20,trader_guard='WARN',threshold=.50),
        assumptions=['Independent $1000 wallets; full adaptive ladder and reserves, immediate source-notification-cap fills.',
                     'Legacy case reproduces pre-PR selection without new entry caps, strict margins or loss latch.',
                     'Loss latch uses capped fresh notification liquidation marks; unpriced positions stay at cost and are not live prices.',
                     '20% loss latch stops new entries but never suppresses sells/expiry; no automatic resumption.',
                     'Guard records all source opportunities, including filtered/skipped entries; cold-start histories at each portfolio start.',
                     'Earlier holdout model uses only labels available before October 24; shipped paper artifact only scores the later file.',
                     'Paper training labels use scheduled capture-time 24h reference outcomes; runtime replay uses notification/3min sweeps. Processing/sweep delays are an approximation.',
                     'Latest data already inspected in prior analyses; this is retrospective, not a validated edge or production promotion.',
                     'Source partial quantities, mint identity, actual fills, executable liquidity and UI failures are unavailable.'],periods={})
    with tempfile.TemporaryDirectory() as temp:
        artifact=Path(temp)/'earlier_paper.json.gz'
        earlier_build=build(a.history,'2026-10-17',artifact,a.bootstrap,cut-1)
        earlier=read_payload(artifact)
        # Public report retains aggregate sample counts/cutoffs, not fitted parameters.
        earlier_build.pop('calibrationSlope', None)
        earlier_build.pop('calibrationIntercept', None)
        report['earlier_model_build']=earlier_build
        for period,rows,start,paper in [('older',old,cut,earlier),('latest',latest,latest[0]['captured_time'],shipped)]:
            trained=int(pd.Timestamp(paper['trainedThrough']).timestamp()*1000)
            if trained>=start:raise ValueError('Paper model contains outcomes unavailable at test start')
            scores={'legacy':score_buys(rows,original,start),'paper':score_buys(rows,paper,start)}
            results={}
            for slip in (0.,.01):
                cases={}
                specs=[('legacy_main_v0.4',original,'legacy',None,0,False,None,True,'WARN',.5),
                       ('legacy_with_new_safety',original,'legacy',100000,1000,True,.20,True,'WARN',.5),
                       ('corrected_lt100_comparison',paper,'paper',100000,1000,True,.20,True,'WARN',.5),
                       ('corrected_paper_preset',paper,'paper',50000,1000,True,.20,True,'WARN',.5),
                       ('corrected_filter50',paper,'paper',50000,1000,True,.20,True,'FILTER',.5),
                       ('corrected_filter40',paper,'paper',50000,1000,True,.20,True,'FILTER',.4),
                       ('rule_only_lt50',paper,'paper',50000,0,True,.20,False,'WARN',.5)]
                for name,payload,key,cap,minimum,strict,limit,forecast,mode,threshold in specs:
                    cases[name],ledger,audit=simulate(rows,payload,scores[key],start,mode,threshold,False,slip,cap,
                                                     minimum,strict,limit,forecast)
                results[f'slippage_{slip:g}']=cases
            report['periods'][period]=dict(model_id=paper['modelId'],model_trained_through=paper['trainedThrough'],results=results)
            print('completed',period,flush=True)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n')
    print(json.dumps(report,sort_keys=True,allow_nan=False))
if __name__=='__main__':main()
