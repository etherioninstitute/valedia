"""Parent-owned TA-1 run ledger. Trusted boundary: this user's protected filesystem and runner.

The sandboxed graph cannot read/write this directory. This is NOT an attestation
against someone who controls the trusted parent process or the filesystem owner.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .adapter import TAError, _canonical

DEFAULT_REGISTRY = Path.home()/'.hermes/profiles/valedia/cache/scratch/ta-1/owned-runs'


def read_regular(path: Path, limit: int) -> bytes:
    """No symlink components or swapped inode; bound bytes from the opened fd."""
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise TAError('receipt/source symlink path rejected')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise TAError('receipt/source not a bounded regular file')
            raw = os.read(fd, limit+1)
            after = os.fstat(fd)
            current = path.stat(follow_symlinks=False)
            if len(raw)>limit or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)!=(after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or (current.st_dev,current.st_ino)!=(before.st_dev,before.st_ino):
                raise TAError('receipt/source file changed during read')
            return raw
        finally: os.close(fd)
    except (OSError, ValueError) as exc: raise TAError('receipt/source path invalid') from exc


class OwnedRunRegistry:
    def __init__(self, root=DEFAULT_REGISTRY, *, create=True):
        self.root=Path(root).absolute()
        if self.root.is_symlink() or any(p.is_symlink() for p in self.root.parents):
            raise TAError('registry symlink path')
        if create:self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.is_dir() or self.root.stat().st_mode & 0o077:
            raise TAError('private owned-run registry required')

    def _path(self, run_id):
        try:
            if str(uuid.UUID(run_id)) != run_id: raise ValueError()
        except (TypeError,ValueError) as exc: raise TAError('invalid owned run identity') from exc
        return self.root/(run_id+'.json')

    def load(self, run_id):
        return json.loads(read_regular(self._path(run_id),16_384))

    def _create(self,path,value):
        raw=_canonical(value)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            with os.fdopen(fd,'wb') as f:
                f.write(raw);f.flush();os.fsync(f.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self._sync_dir()

    def _sync_dir(self):
        fd=os.open(self.root,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def allocate(self,req,snapshot_digest,source,code_hash,model_evidence,snapshot_hash):
        """One identity per request; preallocate durable provenance before child launch."""
        if snapshot_digest!=req['source_sha256'] or not re.fullmatch('[a-f0-9]{64}',snapshot_hash):
            raise TAError('snapshot source hash mismatch')
        key=hashlib.sha256(_canonical(req)).hexdigest()
        run_id=str(uuid.uuid4())
        record={'run_id':run_id,'state':'PREPARED','request_sha256':key,
                'snapshot_sha256':snapshot_digest,'source':source,'code_sha256':code_hash,
                'model_evidence':model_evidence,'model_evidence_sha256':hashlib.sha256(_canonical(model_evidence)).hexdigest(),
                'snapshot_file_sha256':snapshot_hash}
        # The index stops two different successful receipts for the same request.
        index=self.root/('request-'+key+'.json')
        self._create(index,{'run_id':run_id,'request_sha256':key})
        try: self._create(self._path(run_id),record)
        except BaseException:
            index.unlink(missing_ok=True)
            raise
        return run_id

    def complete(self,run_id,receipt_path,receipt_hash,*,exit_code=None):
        """Called only by the parent runner after its owned Popen returned 0."""
        record=self.load(run_id)
        if record['state']!='PREPARED': raise TAError('owned run completion conflict')
        if exit_code != 0: raise TAError('owned runner did not exit successfully')
        raw=read_regular(receipt_path,300_000)
        if hashlib.sha256(raw).hexdigest()!=receipt_hash:
            raise TAError('receipt hash conflict')
        receipt=json.loads(raw)
        if receipt.get('run_id')!=run_id or receipt.get('request_sha256')!=record['request_sha256'] or receipt.get('snapshot_sha256')!=record['snapshot_sha256']:
            raise TAError('receipt provenance conflict')
        record.update(state='COMPLETED',receipt_sha256=receipt_hash,
                      receipt_path=str(Path(receipt_path).absolute()))
        target=self._path(run_id)
        # Replace only the record we own; directory is private to this parent.
        if target.is_symlink(): raise TAError('registry record symlink swap')
        temp=self.root/('.'+run_id+'.'+uuid.uuid4().hex)
        self._create(temp,record)
        os.replace(temp,target)
        self._sync_dir()
        return record
