import json,uuid,threading,logging
from concurrent.futures import ThreadPoolExecutor
from .db import Session,Project,Clip,Job,as_dict,DATA,now
from . import media,ai,safety
pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='creator-worker')
def update(jid,**values):
 with Session.begin() as s:
  j=s.get(Job,jid)
  for k,v in values.items():setattr(j,k,v)
queue_lock=threading.Lock()
def submit(project_id,kind,payload):
 with queue_lock:
  with Session() as s:
   if s.query(Job).filter(Job.project_id==project_id,Job.status.in_(['queued','running'])).first():raise ValueError('This project already has a job running')
  return _submit(project_id,kind,payload)
def _submit(project_id,kind,payload):
 with Session.begin() as s:
  j=Job(id=uuid.uuid4().hex,project_id=project_id,kind=kind,payload=json.dumps(payload),created=now());s.add(j);jid=j.id
 pool.submit(run,jid);return jid
def run(jid):
 try:
  with Session() as s:
   j=s.get(Job,jid);p=s.get(Project,j.project_id);payload=json.loads(j.payload);kind=j.kind
  update(jid,status='running',progress=2,message='Preparing media')
  if kind=='render' or payload.get('prepared'): ids=payload['clip_ids']
  else:
   if payload.get('music_check'):
    update(jid,progress=3,message='Checking sampled music with AudD')
    report=safety.music_scan(p.source,p.duration)
    with Session.begin() as s:
     row=s.get(Project,p.id);existing=json.loads(row.safety);existing.update(report);row.safety=json.dumps(existing)
   words=json.loads(p.transcript)
   if payload.get('transcribe') and not words:
    words=media.transcribe(p.source,DATA/'models',lambda m:update(jid,message=m,progress=6))
    with Session.begin() as s:s.get(Project,p.id).transcript=json.dumps(words)
   update(jid,progress=25,message='Selecting complete moments')
   if payload['mode']=='chronological':windows=media.chronological(p.duration,payload['minimum'],payload['maximum'],payload['target'])
   elif payload['mode']=='ai':windows=ai.highlights(p,words,payload)
   else:windows=media.audio_peaks(p.source,p.duration,payload['minimum'],payload['maximum'],payload['target'],payload['count'])
   if not windows:raise ValueError('No usable moments found; try chronological mode')
   ids=[]
   for i,(a,b) in enumerate(windows,1):
    chosen=[w for w in words if w['end']>a and w['start']<b]
    meta=ai.metadata(p.name,p.category,chosen,i,payload.get('ai_metadata',False))
    with Session.begin() as s:
     c=Clip(id=uuid.uuid4().hex,project_id=p.id,part=i,start=a,end=b,title=meta['title'],metadata_json=json.dumps(meta),captions=json.dumps(chosen));s.add(c);ids.append(c.id)
   update(jid,payload=json.dumps({**payload,'prepared':True,'clip_ids':ids}))
   if not payload.get('render',True):update(jid,status='completed',progress=100,message=f'{len(ids)} editable clips prepared');return
  for i,cid in enumerate(ids):
   with Session() as s:c=s.get(Clip,cid)
   update(jid,progress=30+int(65*i/len(ids)),message=f'Rendering Part {c.part} ({i+1}/{len(ids)})')
   with Session.begin() as s:s.get(Clip,cid).status='rendering'
   try:
    out=(DATA/'exports'/f'{cid}.mp4').resolve()
    result=media.render(p.source,out,c.start,c.end,json.loads(c.captions),c.part,payload)
    with Session.begin() as s:
     row=s.get(Clip,cid);meta=json.loads(row.metadata_json);meta['qc']=result;row.metadata_json=json.dumps(meta);row.output=str(out);row.status='ready';row.error=''
    out.with_suffix('.json').write_text(json.dumps(meta,indent=2))
   except Exception as e:
    with Session.begin() as s:s.get(Clip,cid).status='failed';s.get(Clip,cid).error=str(e)[:2500]
    raise
  update(jid,status='completed',progress=100,message=f'{len(ids)} MP4 exports ready')
 except Exception as e:
  logging.exception('Job failed')
  update(jid,status='failed',message=str(e)[:2500])
def recover():
 with Session.begin() as s:
  for j in s.query(Job).filter(Job.status.in_(['queued','running'])):j.status='failed';j.message='Server stopped during processing. Retry this job.'
  for c in s.query(Clip).filter(Clip.status=='rendering'):c.status='failed';c.error='Render interrupted; export again.'
