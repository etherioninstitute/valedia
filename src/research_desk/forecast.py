"""Lazy Kronos bridge, recorded sample envelope, safe research exports and toy evaluator."""
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import secrets
import stat
from pathlib import Path

from .core import DeskError, canonical, num, open_directory, sha, utc, validate_event

PAIR = {'Kronos-mini': ('Kronos-Tokenizer-2k', 2048),
        'Kronos-small': ('Kronos-Tokenizer-base', 512),
        'Kronos-base': ('Kronos-Tokenizer-base', 512)}


class KronosAdapter:
    """Use an already loaded official KronosPredictor; no model loading here."""
    def __init__(self, predictor=None, *, model=None, tokenizer=None, revision=None, max_context=None):
        self.predictor = predictor
        self.meta = {'model': model, 'tokenizer': tokenizer, 'revision': revision, 'max_context': max_context}
        if predictor is not None:
            if model not in PAIR or tokenizer != PAIR[model][0] or not isinstance(revision, str) or not revision or max_context != PAIR[model][1]:
                raise DeskError('actual paired model, tokenizer, revision and context required')

    def paths(self, history, timestamps, *, as_of=None, horizon, sample_count, T, top_p):
        if self.predictor is None:
            return {'status': 'unavailable', 'reason': 'no loaded predictor injected'}
        if (not isinstance(history,list) or not 2 <= len(history) <= self.meta['max_context'] or
            isinstance(horizon,bool) or not isinstance(horizon,int) or not 1 <= horizon <= 500 or
            isinstance(sample_count,bool) or not isinstance(sample_count,int) or not 1 <= sample_count <= 50):
            raise DeskError('forecast dimensions outside bounds')
        if len(timestamps) != horizon or not 0 < num(T) <= 5 or not 0 < num(top_p) <= 1:
            raise DeskError('invalid forecast parameters')
        decision=utc(as_of)
        step=None
        for j,b in enumerate(history):
            if not isinstance(b,dict) or set(b)!={'at','available_at','open','high','low','close','volume'}:
                raise DeskError('invalid history candle')
            at,available=utc(b['at']),utc(b['available_at'])
            if not at<=available<=decision:raise DeskError('unclosed or unavailable candle')
            o,h,l,c=(num(b[k],positive=True) for k in ('open','high','low','close'))
            num(b['volume'],nonnegative=True)
            if l>min(o,c) or h<max(o,c):raise DeskError('invalid history OHLC')
            if j:
                delta=(at-utc(history[j-1]['at'])).total_seconds()
                if delta<=0 or step is not None and delta!=step:raise DeskError('history gap or duplicate')
                step=delta
        prior=utc(history[-1]['at'])
        for stamp in timestamps:
            at=utc(stamp)
            if (at-prior).total_seconds()!=step or at<=decision:
                raise DeskError('forecast time alignment invalid')
            prior=at
        try:
            import pandas as pd
        except ImportError as exc:
            raise DeskError('pandas required only for injected Kronos inference') from exc
        frame = pd.DataFrame([{k: b[k] for k in ('open','high','low','close','volume')} for b in history])
        x_time = pd.Series(pd.to_datetime([b['at'] for b in history], utc=True))
        y_time = pd.Series(pd.to_datetime(timestamps, utc=True))
        paths = []
        for _ in range(sample_count):
            result = self.predictor.predict(df=frame, x_timestamp=x_time, y_timestamp=y_time,
                                            pred_len=horizon, T=float(T), top_p=float(top_p), sample_count=1)
            raw=result.to_dict('records')
            if len(raw)!=horizon:raise DeskError('predictor horizon mismatch')
            path=[]
            for b in raw:
                if not isinstance(b,dict) or not all(k in b for k in ('open','high','low','close','volume')):
                    raise DeskError('predictor OHLCV missing')
                item={k:num(b[k],positive=(k!='volume'),nonnegative=(k=='volume')) for k in ('open','high','low','close','volume')}
                if item['low']>min(item['open'],item['close']) or item['high']<max(item['open'],item['close']):
                    raise DeskError('predictor OHLC invalid')
                path.append(item)
            paths.append(path)
        return {'status': 'available', 'paths': paths, 'model': self.meta}


def aggregate_paths(record):
    expected = ('schema','venue','symbol','interval_seconds','origin_at','as_of','last_close',
                'source_sha256','model','timestamps','paths')
    if not isinstance(record, dict) or set(record) != set(expected) or record['schema'] != 'valedia.research-desk.forecast.v1':
        raise DeskError('invalid forecast record')
    if not isinstance(record['venue'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}', record['venue']):
        raise DeskError('invalid venue')
    if not isinstance(record['symbol'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}', record['symbol']):
        raise DeskError('invalid symbol')
    if not isinstance(record['source_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}',record['source_sha256']):
        raise DeskError('invalid source hash')
    model = record['model']
    if not isinstance(model, dict) or set(model) != {'model','tokenizer','revision','max_context'}:
        raise DeskError('model provenance required')
    if model['model'] not in PAIR or model['tokenizer'] != PAIR[model['model']][0] or model['max_context'] != PAIR[model['model']][1] or not isinstance(model['revision'],str) or not re.fullmatch(r'[A-Za-z0-9._/-]{1,128}', model['revision']):
        raise DeskError('model provenance invalid')
    step = record['interval_seconds']
    if isinstance(step,bool) or not isinstance(step,int) or not 1 <= step <= 86400:
        raise DeskError('invalid interval')
    origin = utc(record['origin_at'])
    decision=utc(record['as_of'])
    if origin>decision:raise DeskError('origin after decision')
    last_close=num(record['last_close'],positive=True)
    times = record['timestamps']
    paths = record['paths']
    if not isinstance(times,list) or not 1 <= len(times) <= 500 or not isinstance(paths,list) or not 1 <= len(paths) <= 50:
        raise DeskError('forecast bounds exceeded')
    prev = origin
    for s in times:
        at=utc(s)
        if (at-prev).total_seconds() != step or at<=decision:
            raise DeskError('forecast timestamps not contiguous after origin')
        prev=at
    for path in paths:
        if not isinstance(path,list) or len(path) != len(times):
            raise DeskError('path horizon mismatch')
        for bar in path:
            if not isinstance(bar,dict) or set(bar) != {'open','high','low','close','volume'}:
                raise DeskError('invalid path bar')
            o,h,l,c=(num(bar[k],positive=True) for k in ('open','high','low','close'))
            num(bar['volume'],nonnegative=True)
            if l > min(o,c) or h < max(o,c):
                raise DeskError('invalid path OHLC')
    summary=[]
    for j,t in enumerate(times):
        vals=[float(p[j]['close']) for p in paths]
        summary.append({'at':t,'mean':sum(vals)/len(vals),'min':min(vals),'max':max(vals)})
    return {'schema':'valedia.research-desk.forecast-summary.v1','venue':record['venue'],
            'symbol':record['symbol'],'interval_seconds':step,'origin_at':record['origin_at'],
            'as_of':record['as_of'],'last_close':last_close,
            'source_sha256':record['source_sha256'],'model':model,'sample_count':len(paths),
            'horizon':len(times),'label':f'{len(paths)}-sample range (research; uncalibrated)',
            'rows':summary,'paths':paths,'record_sha256':sha(record)}


def _atomic_new(directory, name, data, *, directory_fd=None, receipt_sink=None):
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}',name) or name in ('.','..'):
        raise DeskError('safe output directory/name required')
    if not getattr(os,'O_NOFOLLOW',0) or not {os.open,os.link,os.unlink}.issubset(os.supports_dir_fd):
        raise DeskError('descriptor-safe output unavailable')
    held=directory_fd if directory_fd is not None else open_directory(directory)
    own=directory_fd is None
    tmp='.research-desk-'+secrets.token_hex(16)
    created=False
    try:
        if not stat.S_ISDIR(os.fstat(held).st_mode):raise DeskError('directory handle required')
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=held)
        created=True
        with os.fdopen(fd,'wb') as f:
            f.write(data)
            f.flush(); os.fsync(f.fileno())
        # Atomic no-replace on local POSIX filesystem.
        os.link(tmp,name,src_dir_fd=held,dst_dir_fd=held,follow_symlinks=False)
        if receipt_sink is not None:
            info=os.stat(name,dir_fd=held,follow_symlinks=False)
            receipt_sink[name]={'path':str(Path(directory).absolute()/name),'sha256':hashlib.sha256(data).hexdigest(),
                                'size':len(data),'dev':info.st_dev,'ino':info.st_ino}
    except FileExistsError as exc:
        raise DeskError('output exists') from exc
    except OSError as exc:
        raise DeskError('output write failed') from exc
    finally:
        if created:os.unlink(tmp,dir_fd=held)
        if own:os.close(held)
    return str(Path(directory)/name)


def export_forecast(summary, directory, stem='forecast', *, directory_fd=None, receipt_sink=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',stem):
        raise DeskError('unsafe file stem')
    rows=summary['rows']; n=summary['sample_count']
    buf=io.StringIO()
    writer=csv.writer(buf)
    fields=('open','high','low','close','volume')
    writer.writerow(['at','mean_close','min_close','max_close']+
                    [f'path_{i+1}_{field}' for i in range(n) for field in fields])
    for j,row in enumerate(rows):
        writer.writerow([row['at'],row['mean'],row['min'],row['max']]+
                        [p[j][field] for p in summary['paths'] for field in fields])
    csv_data=buf.getvalue().encode()
    vals=[summary['last_close']]+[v for row in rows for v in (row['min'],row['max'])]
    lo,hi=min(vals),max(vals)
    span=hi-lo or max(abs(hi)*.01,1)
    x=lambda i: 40+i*max(1,720/len(rows))
    y=lambda v: 280-(v-lo)/span*230
    line=lambda key:' '.join(f'{x(i+1):.1f},{y(row[key]):.1f}' for i,row in enumerate(rows))
    polygon=line('max')+' '+' '.join(f'{x(i+1):.1f},{y(rows[i]["min"]):.1f}' for i in reversed(range(len(rows))))
    title=html.escape(summary['symbol']+' '+summary['label'],quote=True)
    subtitle=html.escape(f"as_of {summary['as_of']} · {summary['model']['model']} / {summary['model']['tokenizer']} · revision {summary['model']['revision']} · source {summary['source_sha256']}",quote=True)
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="320" viewBox="0 0 800 320"><rect width="800" height="320" fill="white"/><text x="40" y="25">{title}</text><text x="40" y="310" font-size="10">{subtitle}</text><polygon points="{polygon}" fill="#bcd" opacity="0.7"/><polyline points="{x(0):.1f},{y(summary["last_close"]):.1f} {line("mean")}" fill="none" stroke="#135" stroke-width="2"/></svg>'
    # Immutable xloc.bar_time anchor prevents the forecast from sliding to each new last bar.
    if len(rows)>100:
        raise DeskError('Pine export limited to 100 future bars')
    origin_ms=int(utc(summary['origin_at']).timestamp()*1000)
    step_ms=summary['interval_seconds']*1000
    pine=['//@version=5','indicator("Kronos forecast (research sample range)", overlay=true, max_lines_count=300, max_boxes_count=100)',
          f'// {n}-sample range; not a confidence interval',
          f'// origin {summary["origin_at"]}; as_of {summary["as_of"]}; model {summary["model"]["model"]}; tokenizer {summary["model"]["tokenizer"]}',
          f'// revision {summary["model"]["revision"]}; source {summary["source_sha256"]}',
          f'if time_close == {origin_ms} and barstate.isconfirmed']
    for i,row in enumerate(rows,1):
        left=origin_ms+(i-1)*step_ms;right=origin_ms+i*step_ms
        pine.append(f'    box.new(left={left}, top={row["max"]:.12g}, right={right}, bottom={row["min"]:.12g}, xloc=xloc.bar_time, bgcolor=color.new(color.blue, 85), border_color=color.new(color.blue, 100))')
        prior=summary['last_close'] if i==1 else rows[i-2]['mean']
        pine.append(f'    line.new(x1={left}, y1={prior:.12g}, x2={right}, y2={row["mean"]:.12g}, xloc=xloc.bar_time, color=color.blue)')
    held=directory_fd if directory_fd is not None else open_directory(directory)
    own=directory_fd is None
    outputs={}
    try:
        for ext,data in (('csv',csv_data),('svg',svg.encode()),('pine','\n'.join(pine).encode())):
            outputs[ext]=_atomic_new(directory,f'{stem}.{ext}',data,directory_fd=held,receipt_sink=receipt_sink)
    finally:
        if own:os.close(held)
    return outputs


def evaluate_origins(candles, origins, *, predictor=None, recorded=None, fee_bps, slippage_bps):
    """One nonoverlapping next-bar open-to-close paper trade per origin; toy diagnostics only."""
    fee=num(fee_bps,nonnegative=True)/10000
    slip=num(slippage_bps,nonnegative=True)/10000
    if fee+slip>=.5 or not isinstance(candles,list) or not 3<=len(candles)<=10000:
        raise DeskError('invalid evaluator inputs')
    if not isinstance(origins,list) or not origins or len(origins)>1000 or origins!=sorted(set(origins)):
        raise DeskError('unique sorted origins required')
    if predictor is None and recorded is None:
        raise DeskError('origin predictor or recorded forecasts required')
    step=None
    for j,bar in enumerate(candles):
        if not isinstance(bar,dict) or set(bar)!={'at','available_at','open','high','low','close','volume'}:
            raise DeskError('invalid evaluation candle')
        at,available=utc(bar['at']),utc(bar['available_at'])
        if available<at:raise DeskError('candle available before its interval')
        o,h,l,c=(num(bar[k],positive=True) for k in ('open','high','low','close'))
        num(bar['volume'],nonnegative=True)
        if l>min(o,c) or h<max(o,c):raise DeskError('invalid evaluation OHLC')
        if j:
            delta=(at-utc(candles[j-1]['at'])).total_seconds()
            if delta<=0 or step is not None and delta!=step:raise DeskError('evaluation candle gap or duplicate')
            step=delta
    result=[]; last_target=-1
    for i in origins:
        if isinstance(i,bool) or not isinstance(i,int) or i<1 or i+1>=len(candles):
            raise DeskError('invalid origin')
        if i+1<=last_target:
            raise DeskError('overlapping target bar')
        decision=utc(candles[i]['at'])
        history=[]
        for bar in candles[:i+1]:
            if utc(bar['at'])>decision or utc(bar['available_at'])>decision:
                raise DeskError('future/revised candle in origin history')
            history.append(dict(bar))
        target=candles[i+1]
        if utc(target['at'])<=decision:
            raise DeskError('nonfuture target')
        if predictor:
            forecast=predictor(history, decision.isoformat())
        else:
            item=recorded.get(i)
            if not isinstance(item,dict) or set(item)!={'close','generated_at','available_at','source_sha256','history_sha256','target_at'}:
                raise DeskError('origin-specific recorded forecast provenance required')
            if not isinstance(item['source_sha256'],str) or not re.fullmatch(r'[0-9a-f]{64}',item['source_sha256']):
                raise DeskError('forecast source hash required')
            if not utc(item['generated_at'])<=utc(item['available_at'])<=decision:
                raise DeskError('future/revised recorded forecast')
            if item['history_sha256']!=sha(history) or utc(item['target_at'])!=utc(target['at']):
                raise DeskError('forecast origin/history identity mismatch')
            forecast=item['close']
        predicted=num(forecast,positive=True)
        last_close=num(history[-1]['close'],positive=True)
        actual_close=num(target['close'],positive=True)
        entry=num(target['open'],positive=True)
        direction=1 if predicted>last_close else -1 if predicted<last_close else 0
        realized=1 if actual_close>last_close else -1 if actual_close<last_close else 0
        gross=direction*(actual_close/entry-1) if direction else 0
        net=gross-2*(fee+slip) if direction else 0
        result.append({'origin':i,'decision_at':decision.isoformat(),'forecast_close':predicted,
                       'actual_close':actual_close,'direction':direction,'actual_direction':realized,
                       'direction_correct':direction==realized,'absolute_error':abs(predicted-actual_close),
                       'gross_return':gross,'net_return':net,'no_change_baseline_return':0.0})
        last_target=i+1
    equity=1.0
    streak=longest=0
    for r in result:
        equity*=1+r['net_return'];r['equity']=equity
        streak=streak+1 if r['net_return']<0 else 0
        longest=max(longest,streak)
    return {'schema':'valedia.research-desk.evaluation.v1','status':'SYNTHETIC_DIAGNOSTIC_ONLY',
            'origins':len(result),'direction_accuracy':sum(r['direction_correct'] for r in result)/len(result),
            'mae_close':sum(r['absolute_error'] for r in result)/len(result),
            'mean_absolute_percent_error_close':sum(100*r['absolute_error']/r['actual_close'] for r in result)/len(result),
            'no_change_direction_accuracy':sum(r['actual_direction']==0 for r in result)/len(result),
            'no_change_mae_close':sum(abs(r['actual_close']-candles[r['origin']]['close']) for r in result)/len(result),
            'paper_equity':equity,'no_change_equity':1.0,'longest_losing_streak':longest,
            'fee_bps_each_side':fee_bps,'slippage_bps_each_side':slippage_bps,
            'fill_assumption':'next bar open, zero latency, one bar hold; no liquidity or market calendar simulation',
            'tie_rule':'flat','trades':result}
