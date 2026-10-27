"""Durable batch ownership and before/after mutation journal. No network calls."""
import hashlib, json, os, threading, uuid
from pathlib import Path


def fingerprint(path):
    p=Path(path).resolve();h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return {'path':str(p),'name':p.name,'bytes':p.stat().st_size,'sha256':h.hexdigest()}


def atomic_json(path,value):
    path=Path(path);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.pending')
    fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


class Ledger:
    def __init__(self,path,scope,files):
        self.path=Path(path);self.lock=threading.RLock()
        wanted=[fingerprint(p) for p in files]
        if not wanted or len({p['name'] for p in wanted})!=len(wanted):raise ValueError('Nonempty batch with unique names required')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.path.exists():
            self.data=json.loads(self.path.read_text())
            if self.data.get('scope')!=scope or self.data.get('files')!=wanted:raise ValueError('Ledger scope/content mismatch; never reuse for another batch')
        else:
            self.data={'schema':'delivery-ledger/v1','scope':scope,'files':wanted,'entries':{p['name']:{} for p in wanted}}
            atomic_json(self.path,self.data)
    def row(self,name):return self.data['entries'][name]
    def update(self,name,**values):
        with self.lock:
            self.row(name).update(values);atomic_json(self.path,self.data)
    def intent(self,name,step):
        with self.lock:
            if self.row(name).get(step) in ('dispatching','unknown'):
                raise RuntimeError('Unknown prior outcome; reconcile recorded '+step+' intent for '+name)
            self.update(name,**{step:'dispatching'})
    def failed(self,name,step,error):self.update(name,**{step:'unknown',step+'_error':str(error)})
