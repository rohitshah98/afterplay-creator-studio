import os,json,time,uuid,hashlib,shutil,threading,datetime,logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import update as sql_update
from .db import Session,Publication,PublishEvent,Setting,Clip,Account,DATA,now,as_dict
from . import social,publish_providers as providers

LOCK=threading.RLock();STOP=threading.Event();ACTIVE=set();POOL=ThreadPoolExecutor(max_workers=2,thread_name_prefix='publisher');THREAD=None
PENDING=['queued','retry_wait','uploading','processing','platform_scheduled','needs_connection','needs_review','paused']
DEFAULTS={'paused':False,'daily_limits':{'youtube':3,'tiktok':3},'timezone':'UTC','max_attempts':4}
def config():
 with Session() as s:r=s.get(Setting,'publishing')
 return {**DEFAULTS,**(json.loads(r.value) if r else {})}
def save_config(value):
 with Session.begin() as s:
  r=s.get(Setting,'publishing')
  if not r:r=Setting(key='publishing',value=json.dumps(value));s.add(r)
  else:r.value=json.dumps(value)
def record(pid,state,message='',**fields):
 with Session.begin() as s:
  p=s.get(Publication,pid)
  if not p:return
  p.state=state;p.updated=now()
  for k,v in fields.items():setattr(p,k,v)
  s.add(PublishEvent(publication_id=pid,state=state,message=message[:1000],created=now()))
def session_get(p):return json.loads(social.cipher().decrypt(p.session_secret.encode())) if p.session_secret else None
def session_set(pid,data,remote_id=None):
 with Session.begin() as s:
  p=s.get(Publication,pid);p.session_secret=social.cipher().encrypt(json.dumps(data).encode()).decode()
  if remote_id is not None:p.remote_id=remote_id
def serialize(p):
 d=as_dict(p);d.pop('session_secret');d.pop('asset_path');d['payload']=json.loads(d['payload']);return d

def snapshot(c):
 path=Path(c.output)
 if c.status!='ready' or not path.is_file():raise ValueError('Render the latest clip edits before publishing')
 h=hashlib.sha256();folder=DATA/'publishing';folder.mkdir(exist_ok=True);tmp=folder/(uuid.uuid4().hex+'.tmp')
 try:
  with path.open('rb') as source,tmp.open('wb') as out:
   while data:=source.read(4*1024*1024):h.update(data);out.write(data)
  digest=h.hexdigest();frozen=folder/(digest+'.mp4')
  if not frozen.exists():tmp.replace(frozen);frozen.chmod(0o400)
 finally:tmp.unlink(missing_ok=True)
 return str(frozen),digest

def enqueue(clip_id,targets,payloads,scheduled,consent):
 if not consent:raise ValueError('Review the content and explicitly confirm upload consent')
 if not targets or len(set(targets))!=len(targets) or any(p not in ['youtube','tiktok'] for p in targets):raise ValueError('Choose valid publishing platforms')
 timestamp=time.time()
 if scheduled:
  dt=datetime.datetime.fromisoformat(scheduled.replace('Z','+00:00'))
  if dt.tzinfo is None or dt.timestamp()<=timestamp:raise ValueError('Choose a future date and time with a timezone')
  due=dt.timestamp()
 else:due=timestamp
 batch=uuid.uuid4().hex;result=[]
 with LOCK,Session.begin() as s:
  c=s.get(Clip,clip_id)
  if not c:raise ValueError('Clip not found')
  asset,digest=snapshot(c)
  for platform in targets:
   detail=social.account_details(platform);payload=payloads[platform]
   fallback=platform=='tiktok' and (not direct_enabled() or bool(payload.get('_manual_reason')))
   if platform=='youtube' and not detail:raise ValueError('Connect your YouTube channel before queueing a YouTube upload')
   if platform=='tiktok' and not fallback and not detail:raise ValueError('Connect TikTok before using Direct Post')
   if platform=='tiktok' and due>timestamp and not tiktok_scheduling_enabled():fallback=True
   account_id=detail['id'] if detail else 'manual'
   key=hashlib.sha256((platform+':'+account_id+':'+digest).encode()).hexdigest()
   existing=s.query(Publication).filter_by(dedupe_key=key).first()
   if existing:result.append({'id':existing.id,'platform':platform,'duplicate':True,'state':existing.state});continue
   if platform=='youtube':
    st=payload.setdefault('status',{})
    if st.get('privacyStatus') not in ['private','unlisted','public']:raise ValueError('Choose a valid YouTube privacy setting')
    if scheduled:
     if st['privacyStatus']!='public':raise ValueError('YouTube scheduled release requires Public visibility; it remains private until publishAt')
     payload['_requested_privacy']='public';st['privacyStatus']='private';st['publishAt']=scheduled
     # Native YouTube scheduling: upload now, platform releases at the requested time.
     platform_due=timestamp
    else:platform_due=due
   else:
    platform_due=due
    if not fallback:
     payload=providers.tt_validate(payloads[platform]['_creator_info'],payload,(c.end-c.start));payload.pop('_creator_info',None)
   pid=uuid.uuid4().hex;state='manual_required' if fallback else 'queued'
   message='Use the official TikTok uploader; automatic publication is unavailable for this setup.' if fallback else 'User authorized upload; waiting for scheduler'
   p=Publication(id=pid,batch_id=batch,clip_id=c.id,platform=platform,account_id=account_id,dedupe_key=key,asset_path=asset,payload=json.dumps(payload),state=state,due=platform_due,created=now(),updated=now(),error=message if fallback else '')
   s.add(p);s.flush();s.add(PublishEvent(publication_id=pid,state=state,message=message,created=now()));result.append({'id':pid,'platform':platform,'duplicate':False,'state':state})
 return {'batch_id':batch,'publications':result}
def direct_enabled():return os.getenv('TIKTOK_DIRECT_POST_ENABLED','false').lower()=='true'
def tiktok_scheduling_enabled():return direct_enabled() and os.getenv('TIKTOK_SCHEDULING_APPROVED','false').lower()=='true'

def daily_usage():
 day=datetime.datetime.now(datetime.timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
 with Session() as s:
  return {platform:s.query(Publication).filter(Publication.platform==platform,Publication.started_at>=day).count() for platform in ['youtube','tiktok']}
def limits_allow(platform,cfg):
 return daily_usage()[platform]<cfg['daily_limits'][platform]
def current_account(p):
 detail=social.account_details(p.platform)
 if not detail or detail.get('id')!=p.account_id:raise ValueError('Original publishing account is disconnected or changed. Reconnect that account.')

def poll(p):
 current_account(p)
 status=providers.yt_status(p.remote_id) if p.platform=='youtube' else providers.tt_status(p.remote_id)
 if p.platform=='tiktok' and status['state']=='processing' and p.session_secret and p.progress<100:
  size=Path(p.asset_path).stat().st_size;offset=max(p.offset,status.get('uploaded_bytes',0));session=session_get(p)
  if offset<size:
   if offset%session['chunk']!=0:record(p.id,'needs_review','Provider reports a partial upload chunk; automatic resend is blocked.',error='Partial chunk requires provider reconciliation');return
   record(p.id,'retry_wait','Continuing the existing TikTok upload session',offset=offset,due=time.time()+5);return
  with Session.begin() as s:s.get(Publication,p.id).offset=size;s.get(Publication,p.id).progress=100
 fields={'next_check':time.time()+(60 if status['state']=='platform_scheduled' else 20),'error':''}
 if status.get('url'):fields['published_url']=status['url']
 if p.platform=='tiktok' and status.get('post_ids'):
  payload=json.loads(p.payload);payload['post_ids']=status['post_ids'];fields['payload']=json.dumps(payload)
  username=payload.get('_creator_username','')
  if username:fields['published_url']='https://www.tiktok.com/@'+username+'/video/'+status['post_ids'][0]
 if status['state']=='failed':fields['error']=status['message']
 record(p.id,status['state'],status['message'],**fields)

def process(pid):
 with Session() as s:p=s.get(Publication,pid)
 if not p:return
 try:
  if p.state in ['processing','platform_scheduled']:
   poll(p);return
  current_account(p)
  if p.platform=='tiktok' and not direct_enabled():record(pid,'manual_required','Direct Post is not enabled. Complete publication on TikTok.',error='Official manual publishing required');return
  with Session.begin() as s:
   row=s.get(Publication,pid)
   if row.state not in ['queued','retry_wait']:return
   row.state='uploading';row.attempts+=1;row.updated=now()
   if not row.started_at:row.started_at=time.time()
  with Session() as s:p=s.get(Publication,pid)
  payload=json.loads(p.payload);size=Path(p.asset_path).stat().st_size;session=session_get(p)
  if not session:
   if p.upload_attempted:
    record(pid,'needs_review','An earlier upload initialization has an uncertain outcome. Check platform before retrying.',error='Uncertain initialization; duplicate prevention stopped a new upload.');return
   record(pid,'uploading','Initializing official upload',upload_attempted=1)
   if p.platform=='youtube':
    session={'url':providers.yt_init({k:v for k,v in payload.items() if not k.startswith('_')},size)};session_set(pid,session)
   else:
    info=providers.creator_info();providers.tt_validate(info,payload,clip_duration(p))
    remote,url,chunk,count=providers.tt_init({k:v for k,v in payload.items() if not k.startswith('_')},size)
    session={'url':url,'chunk':chunk,'count':count};session_set(pid,session,remote_id=remote);record(pid,'uploading','TikTok upload initialized',remote_id=remote);p.remote_id=remote
  offset=p.offset
  if p.platform=='youtube':
   offset,existing=providers.yt_probe(session['url'],size)
   if existing:vid=existing
   else:vid=providers.yt_upload(session['url'],p.asset_path,offset,lambda n:save_offset(pid,n,size))
   record(pid,'processing','YouTube accepted the upload; publication not yet confirmed',remote_id=vid,progress=100,error='',next_check=time.time()+5)
  else:
   status=providers.tt_status(p.remote_id)
   if status['state'] in ['published','failed']:
    poll(p);return
   offset=max(offset,status.get('uploaded_bytes',0))
   if offset<size:
    if offset%session['chunk']!=0:raise providers.PublishError('TikTok reported a partial chunk. Check platform status; a duplicate upload was not started.',ambiguous=True)
    providers.tt_upload(session['url'],p.asset_path,offset,session['chunk'],session['count'],lambda n:save_offset(pid,n,size))
   record(pid,'processing','TikTok accepted the bytes; waiting for publication confirmation',progress=100,error='',next_check=time.time()+5)
 except Exception as e:handle_error(pid,e)

def clip_duration(p):
 from .media import probe
 return probe(p.asset_path)['duration']
def save_offset(pid,n,total):
 with Session.begin() as s:
  p=s.get(Publication,pid);p.offset=n;p.progress=min(99,int(n/total*100));p.updated=now()
def handle_error(pid,e):
 with Session() as s:p=s.get(Publication,pid)
 if not p:return
 if isinstance(e,ValueError):record(pid,'needs_connection',str(e),error=str(e));return
 if isinstance(e,providers.PublishError):
  if e.fallback and p.platform=='tiktok' and not p.remote_id:record(pid,'manual_required',str(e),error=str(e));return
  if e.ambiguous:record(pid,'needs_review',str(e),error=str(e));return
  temporary=e.temporary;message=str(e)
 else:
  # Transport errors may have happened after the provider accepted a request.
  import httpx
  temporary=isinstance(e,(httpx.TransportError,TimeoutError));message='Network interrupted; checking resumable provider state.' if temporary else 'Publishing operation failed. Review account permissions and provider status.'
 if p.state in ['processing','platform_scheduled'] and temporary:
  record(pid,p.state,message,error=message,next_check=time.time()+60);return
 if temporary and p.attempts<config()['max_attempts']:
  if not p.session_secret and p.upload_attempted and not isinstance(e,providers.PublishError):record(pid,'needs_review','Upload initialization response was lost; check the platform before retrying.',error='Uncertain provider outcome; no new upload was started.');return
  # A definitive rejected initialization is safe to retry; transport uncertainty is not.
  reset=0 if not p.session_secret and isinstance(e,providers.PublishError) else p.upload_attempted
  record(pid,'retry_wait',message,error=message,due=time.time()+min(900,15*2**p.attempts),upload_attempted=reset);return
 record(pid,'failed',message,error=message)

def recover():
 with Session() as s:rows=s.query(Publication).filter_by(state='uploading').all()
 for p in rows:
  if p.session_secret:record(p.id,'retry_wait','Resuming persisted upload after restart',due=time.time())
  elif p.upload_attempted:record(p.id,'needs_review','Interrupted before upload session was saved. Verify the platform before retrying.',error='Uncertain upload initialization')
  else:record(p.id,'queued','Recovered queued upload')
def tick():
 cfg=config()
 with Session() as s:
  rows=s.query(Publication).filter(Publication.state.in_(['queued','retry_wait','processing','platform_scheduled'])).order_by(Publication.due).all()
 for p in rows:
  polling=p.state in ['processing','platform_scheduled']
  if polling and p.next_check>time.time():continue
  if not polling and (cfg['paused'] or p.due>time.time()):continue
  if not polling and not p.started_at and not limits_allow(p.platform,cfg):continue
  with LOCK:
   if p.platform in ACTIVE:continue
   ACTIVE.add(p.platform)
  def task(pid=p.id,platform=p.platform):
   try:process(pid)
   finally:
    with LOCK:ACTIVE.discard(platform)
  POOL.submit(task)
def loop():
 while not STOP.is_set():
  try:tick()
  except Exception:logging.error('Publishing scheduler tick failed')
  STOP.wait(5)
def start():
 global THREAD
 social.ensure_key();recover();STOP.clear()
 if os.getenv('CREATOR_SCHEDULER_ENABLED','true').lower()!='true':return
 THREAD=threading.Thread(target=loop,name='publishing-scheduler',daemon=True);THREAD.start()
def stop():STOP.set()

def yt_visibility(p,hold=False):
 import httpx
 payload=json.loads(p.payload);status={k:v for k,v in payload['status'].items() if k!='publishAt'};status['privacyStatus']='private' if hold else payload.get('_requested_privacy',payload['status']['privacyStatus'])
 if not hold and payload['status'].get('publishAt'):
  dt=datetime.datetime.fromisoformat(payload['status']['publishAt'].replace('Z','+00:00'))
  if dt.timestamp()>time.time():status.update(privacyStatus='private',publishAt=dt.isoformat())
 providers.check(httpx.put('https://www.googleapis.com/youtube/v3/videos',params={'part':'status'},headers={'Authorization':'Bearer '+social.access('youtube')},json={'id':p.remote_id,'status':status},timeout=45))

def action(pid,kind):
 with LOCK,Session() as s:
  p=s.get(Publication,pid)
  if not p:raise ValueError('Publishing job not found')
  if p.platform in ACTIVE:raise ValueError('This platform is busy. Wait for the current request to finish, then retry.')
  if kind in ['pause','cancel']:
   if p.state not in ['queued','retry_wait','paused','needs_connection','manual_required','platform_scheduled','needs_review','failed']:raise ValueError('An upload already sent to the platform cannot be cancelled locally. Manage the post on the platform.')
   if p.state=='platform_scheduled':current_account(p);yt_visibility(p,hold=True)
   record(pid,'paused' if kind=='pause' else 'cancelled','User '+kind+'d the job; any uploaded YouTube video is retained privately.');return
  if kind in ['resume','retry']:
   if p.state not in ['paused','retry_wait','failed','needs_connection']:raise ValueError('This job cannot be retried automatically. Review uncertain outcomes or use the manual fallback.')
   if p.remote_id and not (p.platform=='tiktok' and p.session_secret and p.offset<Path(p.asset_path).stat().st_size):
    if p.platform=='youtube' and p.state=='paused':yt_visibility(p)
    record(pid,'processing','Checking existing provider upload; no duplicate upload created',error='',next_check=0);return
   if p.upload_attempted and not p.session_secret:raise ValueError('Upload result is uncertain. Verify the platform; automatic re-upload is blocked.')
   record(pid,'queued','User requested retry',error='',due=time.time());return
  if kind=='check':
   if not p.remote_id and not p.session_secret:raise ValueError('No remote upload exists to reconcile')
   record(pid,'processing' if p.remote_id else 'retry_wait','Checking existing upload',next_check=0,due=time.time());return
  raise ValueError('Unknown publishing action')
