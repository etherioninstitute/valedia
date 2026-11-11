import copy
import contextlib
import importlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from research_desk.core import DeskError, review, validate_event, regular_json, persist_advisory, utc
from research_desk.forecast import aggregate_paths, export_forecast, evaluate_origins, KronosAdapter

FIX=HERE/'research_desk/fixtures/offline_bundle.json'


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.b=regular_json(FIX)

    def test_cli_assembled_offline(self):
        with tempfile.TemporaryDirectory() as d:
            p=subprocess.run([sys.executable,str(HERE/'research_desk_cli.py'),'--input',str(FIX),
                              '--output-dir',d],capture_output=True,text=True,check=True)
            response=json.loads(p.stdout)
            self.assertEqual(response['decision'],'HOLD')
            report=json.loads(Path(response['outputs']['report']).read_text())
            self.assertEqual(report['ownership'],'UNOWNED')
            self.assertFalse(report['performance_eligible'])
            self.assertNotIn('approved',report)
            self.assertIn('sample range',Path(response['outputs']['forecast']['svg']).read_text())
            self.assertEqual(json.loads(Path(response['outputs']['evaluation']).read_text())['status'],'SYNTHETIC_DIAGNOSTIC_ONLY')
            self.assertEqual(set(response['artifact_receipts']),
                             {Path(x).name for x in [response['outputs']['report'],response['outputs']['evaluation'],
                               response['outputs']['receipt'],*response['outputs']['forecast'].values()]})
            self.assertEqual(response['receipt'],json.loads(Path(response['outputs']['receipt']).read_text()))
            saved=Path(d)/'saved-receipt.json';saved.write_text(json.dumps(response['receipt']))
            verified=subprocess.run([sys.executable,str(HERE/'research_desk_cli.py'),'--verify-report',
                                     str(saved)],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(verified.stdout)['report_sha256'],report['report_sha256'])

    def test_receipt_rejects_altered_report_and_late_alias(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);out=base/'out';out.mkdir()
            p=subprocess.run([sys.executable,str(HERE/'research_desk_cli.py'),'--input',str(FIX),
                              '--output-dir',str(out)],capture_output=True,text=True,check=True)
            response=json.loads(p.stdout);paths=response['outputs']
            saved=base/'saved-receipt.json';saved.write_text(json.dumps(response['receipt']))
            receipt_data=copy.deepcopy(response['receipt'])
            receipt_data['artifacts']['report']['size']=True
            invalid=base/'invalid-receipt.json';invalid.write_text(json.dumps(receipt_data))
            check=subprocess.run([sys.executable,str(HERE/'research_desk_cli.py'),'--verify-report',
                                  str(invalid)],capture_output=True,text=True)
            self.assertEqual(check.returncode,2)
            report=Path(paths['report']);report.write_text('forged report')
            check=subprocess.run([sys.executable,str(HERE/'research_desk_cli.py'),'--verify-report',
                                  str(saved)],capture_output=True,text=True)
            self.assertEqual(check.returncode,2)
            out.rename(base/'out-old');other=base/'other';other.mkdir();out.symlink_to(other,target_is_directory=True)
            check=subprocess.run([sys.executable,str(HERE/'research_desk_cli.py'),'--verify-report',
                                  str(saved)],capture_output=True,text=True)
            self.assertEqual(check.returncode,2)

    def test_cli_refuses_returned_path_after_output_swap(self):
        import research_desk_cli as cli
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);chosen=base/'chosen';chosen.mkdir();other=base/'other';other.mkdir()
            (other/'research-report.json').write_text('forged report')
            real=cli.open_directory;swapped=[False]
            def open_then_swap(path):
                fd=real(path)
                if not swapped[0]:
                    chosen.rename(base/'chosen-old')
                    chosen.symlink_to(other,target_is_directory=True)
                    swapped[0]=True
                return fd
            output=io.StringIO()
            with patch.object(cli,'open_directory',side_effect=open_then_swap),contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as raised:
                    cli.main(['--input',str(FIX),'--output-dir',str(chosen)])
            self.assertEqual(raised.exception.code,2)
            self.assertEqual(output.getvalue(),'')
            self.assertTrue((base/'chosen-old'/'research-report.json').exists())
            self.assertEqual((other/'research-report.json').read_text(),'forged report')

    def test_finite_chronology_gap_duplicates(self):
        for change in (
            lambda e:e['candles'][0].update(open=True),
            lambda e:e['candles'][0].update(close=float('nan')),
            lambda e:e['candles'][1].update(at=e['candles'][0]['at']),
            lambda e:e['candles'][2].update(at='2026-01-01T09:00:00Z'),
            lambda e:e.update(available_at='2026-01-01T09:00:00Z'),
            lambda e:e.update(expires_at=e['decision_at']),
        ):
            e=copy.deepcopy(self.b['event']);change(e)
            with self.assertRaises(DeskError):validate_event(e)

    def test_unavailable_stale_claim_and_digest(self):
        self.assertEqual(review(self.b['event'],self.b['roles'])['decision'],'HOLD')
        roles=copy.deepcopy(self.b['roles'])
        roles['news']={'status':'available','observed_at':self.b['event']['decision_at'],'summary':'claim','sources':[{'uri':'https://example.test/story','published_at':'2026-01-01T00:00:00Z','available_at':'2026-01-01T04:00:00Z','expires_at':'2026-01-01T05:00:00Z','sha256':'a'*64}]}
        with self.assertRaises(DeskError):review(self.b['event'],roles)
        with self.assertRaises(DeskError):review(self.b['event'],self.b['roles'],digest={'kind':'cached_scenario','generated_at':'2026-01-01T00:00:00Z','expires_at':'2026-01-01T01:00:00Z','summary':'old','source_sha256':'b'*64})

    def test_scanner_short_circuit_and_provider_failure(self):
        called=[]
        def scanner(_):
            return {'status':'available','relevance':'noise','observed_at':self.b['event']['decision_at'],
                    'summary':'duplicate noise','sources':[]}
        def other(_):
            called.append(1);raise AssertionError('should not run')
        providers={name:other for name in ('setup','news','insider','risk_context','synthesis')}
        providers['scanner']=scanner
        report=review(self.b['event'],providers=providers)
        self.assertEqual(report['decision'],'HOLD')
        self.assertEqual(called,[])
        self.assertEqual(report['roles']['news']['status'],'not_applicable')
        providers['scanner']=other
        self.assertEqual(review(self.b['event'],providers=providers)['roles']['scanner']['status'],'unavailable')

    def test_event_without_candles(self):
        event=copy.deepcopy(self.b['event']);event['market_type']='sports';event['candles']=[]
        self.assertEqual(review(event,self.b['roles'])['decision'],'HOLD')

    def test_required_roles_for_research_only(self):
        roles=copy.deepcopy(self.b['roles'])
        for name in ('scanner','setup','risk_context','synthesis'):
            roles[name].update(status='available',observed_at=self.b['event']['decision_at'],
                               summary='Documented offline fixture evidence',
                               sources=[{'uri':'fixture:'+name,'published_at':self.b['event']['decision_at'],
                                         'available_at':self.b['event']['decision_at'],
                                         'expires_at':self.b['event']['expires_at'],'sha256':'a'*64}])
        roles['scanner']['relevance']='relevant'
        for name in ('news','insider'):
            roles[name].update(status='not_applicable',summary='no applicable source for fixture')
        self.assertEqual(review(self.b['event'],roles)['decision'],'RESEARCH_ONLY')
        roles['risk_context']['status']='not_applicable'
        roles['risk_context']['sources']=[]
        self.assertEqual(review(self.b['event'],roles)['decision'],'HOLD')
        roles['risk_context'].update(status='available',sources=roles['setup']['sources'])
        roles['setup']['summary']='   '
        self.assertEqual(review(self.b['event'],roles)['decision'],'HOLD')
        roles['setup']['summary']='Documented offline fixture evidence'
        roles['synthesis']['sources']=[]
        self.assertEqual(review(self.b['event'],roles)['decision'],'HOLD')

    def test_malformed_scanner_never_calls_enrichment(self):
        calls=[]
        for malformed in (None,{'status':'available','relevance':'relevant'},
                          {'status':'available','relevance':'unknown','observed_at':self.b['event']['decision_at'],
                           'summary':'bad relevance','sources':[]}):
            providers={'scanner':lambda _,r=malformed:r}
            for name in ('setup','news','insider','risk_context','synthesis'):
                providers[name]=lambda _,n=name:calls.append(n)
            self.assertEqual(review(self.b['event'],providers=providers)['decision'],'HOLD')
            self.assertEqual(calls,[])

    def test_delayed_decision_forecast_grid_and_pine_anchor(self):
        e=copy.deepcopy(self.b['event']);e.update(decision_at='2026-01-01T03:10:00Z',
                                           published_at='2026-01-01T03:10:00Z',
                                           available_at='2026-01-01T03:10:00Z',
                                           expires_at='2026-01-01T06:00:00Z')
        f=copy.deepcopy(self.b['forecast']);f['as_of']=e['decision_at']
        summary=aggregate_paths(f)
        self.assertEqual(summary['rows'][0]['at'],'2026-01-01T04:00:00Z')
        with tempfile.TemporaryDirectory() as d:
            pine=Path(export_forecast(summary,d,'aligned')['pine']).read_text()
            origin_ms=int(utc(e['candles'][-1]['at']).timestamp()*1000)
            first_ms=int(utc(summary['rows'][0]['at']).timestamp()*1000)
            self.assertIn(f'if time_close == {origin_ms}',pine)
            self.assertIn(f'box.new(left={origin_ms}',pine)
            self.assertIn(f'right={first_ms}',pine)
        shifted=copy.deepcopy(f);shifted['timestamps']=['2026-01-01T04:10:00Z','2026-01-01T05:10:00Z']
        with self.assertRaises(DeskError):aggregate_paths(shifted)

    def test_identity_duplicate_vs_distinct(self):
        first=review(self.b['event'],self.b['roles'])
        self.assertEqual(first['event_id'],review(self.b['event'],self.b['roles'])['event_id'])
        second=copy.deepcopy(self.b['event']);second['source_sha256']='b'*64
        self.assertNotEqual(first['event_id'],review(second,self.b['roles'])['event_id'])

    def test_persistence_existing_temp_evidencedb(self):
        source=Path('/Users/burtonchatelain/.openclaw/workspace-v3/scripts/integrations')
        sys.path.insert(0,str(source))
        from poly_research_intake import EvidenceDB
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'research.sqlite'
            db=EvidenceDB(path)
            report=review(self.b['event'],self.b['roles'])
            one=persist_advisory(db.db,report)
            two=persist_advisory(db.db,report)
            self.assertTrue(one['inserted']);self.assertFalse(two['inserted'])
            rows=db.rows()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['ownership'],'UNOWNED')
            self.assertIsNone(rows[0]['candidate_sha256'])
            self.assertEqual(rows[0]['is_fill'],0)
            self.assertFalse(json.loads(rows[0]['snapshot'])['performance_eligible'])
            distinct=copy.deepcopy(self.b['event']);distinct['source_sha256']='b'*64
            self.assertTrue(persist_advisory(db.db,review(distinct,self.b['roles']))['inserted'])
            tampered=copy.deepcopy(report);tampered['approved']=True
            from research_desk.core import sha
            tampered['report_sha256']=sha({k:v for k,v in tampered.items() if k!='report_sha256'})
            with self.assertRaises(DeskError):persist_advisory(db.db,tampered)
            db.close()

    def test_persistence_requires_owner_connection_and_no_parent_transaction(self):
        source=Path('/Users/burtonchatelain/.openclaw/workspace-v3/scripts/integrations')
        sys.path.insert(0,str(source))
        from poly_research_intake import EvidenceDB
        with tempfile.TemporaryDirectory() as d:
            absent=Path(d)/'absent.sqlite'
            report=review(self.b['event'],self.b['roles'])
            with self.assertRaises(DeskError):persist_advisory(absent,report)
            self.assertFalse(absent.exists())
            unrelated=Path(d)/'unrelated.sqlite'
            db=sqlite3.connect(unrelated);db.execute('CREATE TABLE other (x TEXT)');db.commit();db.close()
            before=unrelated.read_bytes()
            db=sqlite3.connect(unrelated)
            with self.assertRaises(DeskError):persist_advisory(db,report)
            db.close()
            self.assertEqual(unrelated.read_bytes(),before)
            owner=EvidenceDB(Path(d)/'research.sqlite')
            owner.db.execute('BEGIN IMMEDIATE')
            owner.db.execute('INSERT INTO intake_refusals (source_path,file_sha256,line_no,reason,captured_at) VALUES (?,?,?,?,?)',
                             ('fixture','a'*64,0,'parent transaction','2026-01-01T00:00:00Z'))
            with self.assertRaises(DeskError):persist_advisory(owner.db,report)
            self.assertTrue(owner.db.in_transaction)
            self.assertEqual(owner.db.execute('SELECT COUNT(*) FROM intake_refusals').fetchone()[0],1)
            self.assertEqual(owner.db.execute('SELECT COUNT(*) FROM source_snapshots').fetchone()[0],0)
            owner.db.rollback();owner.close()

    def test_export_hostile_and_no_replace(self):
        f=copy.deepcopy(self.b['forecast']);f['symbol']='BAD<svg'
        with self.assertRaises(DeskError):aggregate_paths(f)
        f=copy.deepcopy(self.b['forecast']);f['model']['revision']='a\nplotshape(true)'
        with self.assertRaises(DeskError):aggregate_paths(f)
        summary=aggregate_paths(self.b['forecast'])
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(DeskError):export_forecast(summary,d,'../escape')
            export_forecast(summary,d,'safe')
            with self.assertRaises(DeskError):export_forecast(summary,d,'safe')
            os.symlink(Path(d)/'target',Path(d)/'link.pine')
            with self.assertRaises(DeskError):export_forecast(summary,d,'link')
            self.assertFalse((Path(d)/'target').exists())

    def test_evaluation_costs_and_future_leak_spy(self):
        e=self.b['evaluation'];seen=[]
        def spy(history,decision):
            seen.append((len(history),decision))
            self.assertTrue(all(utc(x['available_at'])<=utc(decision) for x in history))
            return history[-1]['close']+1
        result=evaluate_origins(e['candles'],e['origins'],predictor=spy,fee_bps=10,slippage_bps=5)
        self.assertEqual([x[0] for x in seen],[3,4])
        self.assertEqual(result['origins'],2)
        self.assertAlmostEqual(result['trades'][0]['net_return'],result['trades'][0]['gross_return']-.003)
        self.assertEqual(result['no_change_equity'],1.0)
        bad=copy.deepcopy(e['candles']);bad[1]['available_at']='2026-01-01T09:00:00Z'
        recorded={int(k):v for k,v in e['recorded'].items()}
        with self.assertRaises(DeskError):evaluate_origins(bad,e['origins'],recorded=recorded,fee_bps=10,slippage_bps=5)
        with self.assertRaises(DeskError):evaluate_origins(e['candles'],[2,2],recorded=recorded,fee_bps=10,slippage_bps=5)
        future=copy.deepcopy(recorded);future[2]['available_at']='2026-01-01T09:00:00Z'
        with self.assertRaises(DeskError):evaluate_origins(e['candles'],e['origins'],recorded=future,fee_bps=10,slippage_bps=5)

    def test_lazy_adapter_and_no_execution_imports(self):
        self.assertEqual(KronosAdapter().paths([],[],horizon=1,sample_count=1,T=1,top_p=.9)['status'],'unavailable')
        with self.assertRaises(DeskError):KronosAdapter(object(),model='Kronos-small',tokenizer='wrong',revision='abc',max_context=512)
        import ast
        for name in ('research_desk/core.py','research_desk/forecast.py','research_desk_cli.py'):
            tree=ast.parse((HERE/name).read_text())
            imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
            imports += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
            self.assertFalse(any(any(x in imp.lower() for x in ('broker','order','alpaca','mercury','httpx','requests','urllib')) for imp in imports))

    def test_injected_predictor_paired_official_shape(self):
        import pandas as pd
        class Fake:
            calls=0
            def predict(self, *, df,x_timestamp,y_timestamp,pred_len,T,top_p,sample_count):
                self.calls+=1
                self_sample_count=sample_count
                assert self_sample_count==1 and pred_len==2 and len(df)==3 and len(x_timestamp)==3 and len(y_timestamp)==2
                return pd.DataFrame([{'open':104,'high':106,'low':103,'close':105,'volume':10,'amount':999},
                                     {'open':105,'high':107,'low':104,'close':106,'volume':10,'amount':999}])
        fake=Fake()
        adapter=KronosAdapter(fake,model='Kronos-small',tokenizer='Kronos-Tokenizer-base',
                              revision='fixture-revision',max_context=512)
        times=['2026-01-01T03:00:00Z','2026-01-01T04:00:00Z']
        result=adapter.paths(self.b['event']['candles'][:3],times,
                             as_of='2026-01-01T02:00:00Z',horizon=2,sample_count=2,T=1,top_p=.9)
        self.assertEqual(fake.calls,2)
        self.assertEqual(set(result['paths'][0][0]),{'open','high','low','close','volume'})
        with self.assertRaises(DeskError):adapter.paths(self.b['event']['candles'][:3],times,
                             as_of='2026-01-01T01:00:00Z',horizon=2,sample_count=1,T=1,top_p=.9)

    def test_json_regular_duplicate_and_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.json';p.write_text('{"x":1,"x":2}')
            with self.assertRaises(DeskError):regular_json(p)
            p.write_text('{"x":NaN}')
            with self.assertRaises(DeskError):regular_json(p)
            link=Path(d)/'link';link.symlink_to(p)
            with self.assertRaises(DeskError):regular_json(link)

    def test_parent_symlink_rejected_for_input_and_output(self):
        source=Path('/Users/burtonchatelain/.openclaw/workspace-v3/scripts/integrations')
        sys.path.insert(0,str(source))
        from poly_research_intake import EvidenceDB
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);physical=base/'physical';physical.mkdir()
            (physical/'out').mkdir()
            (physical/'in.json').write_text('{"x":1}')
            alias=base/'alias';alias.symlink_to(physical,target_is_directory=True)
            with self.assertRaises(DeskError):regular_json(alias/'in.json')
            with self.assertRaises(DeskError):export_forecast(aggregate_paths(self.b['forecast']),alias/'out','blocked')
            self.assertFalse((physical/'out'/'blocked.csv').exists())

    def test_directory_swap_keeps_input_and_exports_on_held_inode(self):
        import research_desk.core as core
        import research_desk.forecast as forecast
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);selected=base/'selected';other=base/'other'
            selected.mkdir();other.mkdir()
            (selected/'input.json').write_text('{"from":"selected"}')
            (other/'input.json').write_text('{"from":"other"}')
            real=core.open_directory
            def swap(path):
                fd=real(path)
                selected.rename(base/'selected-old')
                selected.symlink_to(other,target_is_directory=True)
                return fd
            with patch.object(core,'open_directory',side_effect=swap):
                self.assertEqual(core.regular_json(selected/'input.json'),{'from':'selected'})
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);selected=base/'selected';other=base/'other'
            selected.mkdir();other.mkdir()
            real=forecast.open_directory
            def swap(path):
                fd=real(path)
                selected.rename(base/'selected-old')
                selected.symlink_to(other,target_is_directory=True)
                return fd
            with patch.object(forecast,'open_directory',side_effect=swap):
                forecast.export_forecast(aggregate_paths(self.b['forecast']),selected,'held')
            self.assertTrue((base/'selected-old'/'held.csv').exists())
            self.assertTrue((base/'selected-old'/'held.svg').exists())
            self.assertTrue((base/'selected-old'/'held.pine').exists())
            self.assertFalse((other/'held.csv').exists())

    def test_cli_rejects_db_path_option(self):
        with tempfile.TemporaryDirectory() as d:
            p=subprocess.run([sys.executable,str(HERE/'research_desk_cli.py'),'--input',str(FIX),
                              '--output-dir',d,'--evidence-db',str(Path(d)/'ledger.sqlite')],
                             capture_output=True,text=True)
            self.assertEqual(p.returncode,2)
            self.assertFalse((Path(d)/'ledger.sqlite').exists())


if __name__=='__main__':unittest.main()
