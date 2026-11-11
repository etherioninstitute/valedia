"""Research-only archived simulated-action artifact -> query -> exact citation verification."""
from __future__ import annotations
import argparse,json,os,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mirofish_action_evidence import EvidenceRefused,encoded,load_verified_archive,read_snapshot,research_context,strict_json,verify_citation,require
ARTIFACT_ROOT=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/work/sol-foreman-qwen/valedia-action-grounding-20261108/artifacts')
def artifact_path(identifier):
    require(type(identifier) is str and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',identifier) is not None,'artifact ID must be a slug, never a path');return ARTIFACT_ROOT/(identifier+'.json')
def storage_guard():
    sys.path.insert(0,'/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-evaluation');from storage import guard;guard()
def save_artifact(path,value):
    storage_guard();require(all(not p.is_symlink() for p in [ARTIFACT_ROOT,*ARTIFACT_ROOT.parents]),'symlink artifact root');ARTIFACT_ROOT.mkdir(parents=True,exist_ok=True);raw=encoded(value)+b'\n'
    if path.exists():require(read_snapshot(path)==raw,'existing artifact differs; choose new slug');return
    directory=os.open(ARTIFACT_ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    finally:os.close(directory)
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['build','query','verify']);parser.add_argument('--acceptance-sha256',required=True);parser.add_argument('--artifact-id',default='archived-b161');parser.add_argument('--query',default='');parser.add_argument('--limit',type=int,default=4);parser.add_argument('--citation-json');args=parser.parse_args(argv)
    try:
        path=artifact_path(args.artifact_id);verified=load_verified_archive(args.acceptance_sha256);expected=verified.artifact()
        if args.command=='build':save_artifact(path,expected);result={'status':'RESEARCH_ARTIFACT_VERIFIED','artifact_id':args.artifact_id,'artifact':str(path),'bundle_id':verified.bundle_id,'actions':len(verified.actions),'excluded_seed_occurrences':verified.seed_occurrences_excluded,'scope':expected['scope']}
        else:
            artifact=strict_json(read_snapshot(path));require(encoded(artifact)==encoded(expected),'research artifact does not match reverified root archive')
            if args.command=='query':result=research_context(verified,args.query,args.limit)
            else:
                require(args.citation_json is not None and len(args.citation_json.encode())<=65536,'bounded citation JSON required');result=verify_citation(verified,strict_json(args.citation_json.encode()))
        print(json.dumps(result,ensure_ascii=False));return 0
    except (EvidenceRefused,OSError,KeyError,TypeError) as exc:print(json.dumps({'status':'REFUSED','reason':str(exc)}));return 2
if __name__=='__main__':raise SystemExit(main())
