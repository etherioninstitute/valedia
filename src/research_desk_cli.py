"""Manual offline research desk. No server, scheduler, provider or execution path."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from research_desk import review, aggregate_paths, evaluate_origins, export_forecast
from research_desk.core import DeskError, _read_json_at, open_directory, regular_json, verify_report
from research_desk.forecast import _atomic_new


def main(argv=None):
    p=argparse.ArgumentParser(description='Offline Valedia research advisory')
    p.add_argument('--input',help='bounded regular JSON bundle')
    p.add_argument('--output-dir',help='existing output directory')
    p.add_argument('--stem',default='research')
    p.add_argument('--verify-report',help='read-only verification using a saved receipt JSON')
    args=p.parse_args(argv)
    try:
        if args.verify_report:
            if args.input or args.output_dir:raise DeskError('verification accepts only a receipt path')
            print(json.dumps(verify_report(args.verify_report),sort_keys=True,allow_nan=False))
            return 0
        if not args.input or not args.output_dir:raise DeskError('--input and --output-dir required')
        bundle=regular_json(args.input)
        if not isinstance(bundle,dict) or set(bundle)!={'event','roles','digest','forecast','evaluation'}:
            raise DeskError('bundle fields required')
        report=review(bundle['event'],bundle['roles'],digest=bundle['digest'])
        output_fd=open_directory(args.output_dir)
        try:
            outputs,receipts,receipt=_write_outputs(bundle,report,args,output_fd)
            _verify_outputs(args.output_dir,output_fd,receipts)
        finally:os.close(output_fd)
        print(json.dumps({'decision':report['decision'],'event_id':report['event_id'],
                          'outputs':outputs,'artifact_receipts':receipts,'receipt':receipt},sort_keys=True))
        return 0
    except (DeskError,ValueError,TypeError,KeyError,OSError) as exc:
        p.exit(2,f'research desk rejected input: {exc}\n')


def _write_outputs(bundle,report,args,output_fd):
        outputs={}
        by_name={}
        artifacts={}
        if bundle['forecast'] is not None:
            if report['event']['market_type'] != 'continuous' or len(report['event']['candles']) < 2:
                raise DeskError('candle forecast requires continuous market and candle history')
            summary=aggregate_paths(bundle['forecast'])
            if (summary['venue']!=report['event']['venue'] or summary['symbol']!=report['event']['symbol'] or
                summary['as_of']!=report['event']['decision_at'] or
                summary['origin_at']!=report['event']['candles'][-1]['at'] or
                summary['interval_seconds']!=report['event']['interval_seconds'] or
                summary['last_close']!=report['event']['candles'][-1]['close'] or
                summary['source_sha256']!=report['event']['source_sha256']):
                raise DeskError('forecast/event identity mismatch')
            outputs['forecast']=export_forecast(summary,args.output_dir,args.stem+'-forecast',directory_fd=output_fd,receipt_sink=by_name)
            for ext,path in outputs['forecast'].items():artifacts[ext]=by_name[Path(path).name]
        if bundle['evaluation'] is not None:
            e=bundle['evaluation']
            if not isinstance(e,dict) or set(e)!={'candles','origins','recorded','fee_bps','slippage_bps'}:
                raise DeskError('invalid evaluation bundle')
            recorded={int(k):v for k,v in e['recorded'].items()}
            score=evaluate_origins(e['candles'],e['origins'],recorded=recorded,
                                   fee_bps=e['fee_bps'],slippage_bps=e['slippage_bps'])
            outputs['evaluation']=_atomic_new(args.output_dir,args.stem+'-evaluation.json',json.dumps(score,sort_keys=True,allow_nan=False,indent=2).encode(),directory_fd=output_fd,receipt_sink=by_name)
            artifacts['evaluation']=by_name[Path(outputs['evaluation']).name]
        outputs['report']=_atomic_new(args.output_dir,args.stem+'-report.json',json.dumps(report,sort_keys=True,allow_nan=False,indent=2).encode(),directory_fd=output_fd,receipt_sink=by_name)
        artifacts['report']=by_name[Path(outputs['report']).name]
        receipt={'schema':'valedia.research-desk.receipt.v1','event_id':report['event_id'],'artifacts':artifacts}
        outputs['receipt']=_atomic_new(args.output_dir,args.stem+'-receipt.json',json.dumps(receipt,sort_keys=True,allow_nan=False,indent=2).encode(),directory_fd=output_fd,receipt_sink=by_name)
        return outputs,by_name,receipt


def _verify_outputs(directory,held_fd,by_name):
    reopened=open_directory(directory)
    try:
        held,recheck=os.fstat(held_fd),os.fstat(reopened)
        if (held.st_dev,held.st_ino)!=(recheck.st_dev,recheck.st_ino):
            raise DeskError('output directory identity changed')
        for name,receipt in by_name.items():
            raw,info=_read_json_at(reopened,name,receipt['size'],with_stat=True)
            if (len(raw)!=receipt['size'] or hashlib.sha256(raw).hexdigest()!=receipt['sha256'] or
                (info.st_dev,info.st_ino)!=(receipt['dev'],receipt['ino'])):
                raise DeskError('output artifact identity changed')
    finally:os.close(reopened)


if __name__=='__main__':
    raise SystemExit(main())
