import os,json,uuid,hashlib,shutil,math,datetime,logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,UploadFile,File,Form,Request
from fastapi.responses import FileResponse,RedirectResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,model_validator,ConfigDict
from .db import *
from . import media,worker,ai,social,publishing
from .publishing_api import router as publishing_router
from .publish_providers import PublishError
CATEGORIES=['GTA 6 gameplay','GTA 6 hidden details','GTA 6 funny moments','MrBeast gaming','IShowSpeed reactions','Gaming challenges','Gaming news and updates']
POLICY={'youtube':{'followers':1000,'views':10000000,'window_days':90,'min_video_seconds':0,'note':'Shorts ad revenue route; eligible regions, policy compliance, review and qualified views required. Alternative watch-hour route is separate.','source':'https://support.google.com/youtube/answer/72851'},'tiktok':{'followers':10000,'views':100000,'window_days':30,'min_video_seconds':60,'note':'Creator Rewards: eligible age, region, personal account and original videos longer than one minute. Meeting numeric thresholds does not guarantee acceptance.','source':'https://support.tiktok.com/en/business-and-creator/creator-rewards-program/creator-rewards-program'}}
class RedactOAuthAccess(logging.Filter):
 def filter(self,record):
  if isinstance(record.args,tuple) and len(record.args)>=3:
   args=list(record.args)
   if isinstance(args[2],str) and '/callback' in args[2]:args[2]=args[2].split('?')[0];record.args=tuple(args)
  return True
@asynccontextmanager
async def lifespan(app):
 logging.getLogger('uvicorn.access').addFilter(RedactOAuthAccess())
 init();social.ensure_key();worker.recover();publishing.start()
 yield
 publishing.stop()
app=FastAPI(title='Afterplay Creator API',lifespan=lifespan)
app.include_router(publishing_router)
@app.middleware('http')
async def local_guard(request:Request,call_next):
 host=request.headers.get('host','').split(':')[0]
 if host not in ('localhost','127.0.0.1','testserver'):return JSONResponse({'detail':'Local single-user service only'},status_code=403)
 origin=request.headers.get('origin')
 if origin and origin not in ('http://localhost:8787','http://127.0.0.1:8787','http://localhost:5173'):return JSONResponse({'detail':'Origin not allowed'},status_code=403)
 response=await call_next(request);response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin';return response
@app.exception_handler(PublishError)
async def provider_error(request,e):return JSONResponse({'detail':str(e)},status_code=503 if e.temporary else 422)
@app.exception_handler(ValueError)
async def validation_error(request,e):return JSONResponse({'detail':str(e)},status_code=422)
@app.exception_handler(Exception)
async def unexpected(request,e):
 logging.exception('Request failed');return JSONResponse({'detail':'Operation failed. Check the server log and retry.'},status_code=500)
def get(s,model,id):
 row=s.get(model,id)
 if not row:raise HTTPException(404,'Not found')
 return row
def active(s,pid):
 if s.query(Job).filter(Job.project_id==pid,Job.status.in_(['running','queued'])).first():raise HTTPException(409,'This project already has a job running')
def project_json(p):
 d=as_dict(p);d.pop('source');d.pop('fingerprint');d['transcript_words']=len(json.loads(d.pop('transcript')));d['safety']=json.loads(d['safety']);d['source_url']=f'/api/projects/{p.id}/source';return d
def clip_json(c):
 d=as_dict(c);d['metadata']=json.loads(d.pop('metadata_json'));d['captions']=json.loads(d['captions']);d['has_export']=bool(c.output and Path(c.output).exists());d.pop('output');d['preview_url']=f'/api/clips/{c.id}/video' if d['has_export'] else None;return d
@app.get('/api/health')
def health():return {'ok':True,'ai':ai.configured(),'storage':'PostgreSQL' if not url.startswith('sqlite') else 'SQLite','ffmpeg':Path(media.FFMPEG).exists() or bool(shutil.which(media.FFMPEG))}
@app.get('/api/categories')
def categories():return CATEGORIES
@app.get('/api/projects')
def projects():
 with Session() as s:return [project_json(p) for p in s.query(Project).order_by(Project.created.desc())]
@app.post('/api/projects')
async def upload(file:UploadFile=File(...),name:str=Form(''),category:str=Form(CATEGORIES[0]),rights:bool=Form(False)):
 if not rights:raise HTTPException(422,'Confirm you own or have permission to edit this video')
 if category not in CATEGORIES:raise HTTPException(422,'Unknown category')
 ext=Path(file.filename or '').suffix.lower()
 if ext not in ('.mp4','.mov','.mkv','.webm','.avi','.m4v'):raise HTTPException(422,'Upload MP4, MOV, MKV, WebM, AVI or M4V')
 pid=uuid.uuid4().hex;folder=DATA/'uploads';folder.mkdir(exist_ok=True);path=folder/(pid+ext);size=0;hash=hashlib.sha256()
 try:
  with path.open('wb') as out:
   while chunk:=await file.read(4*1024*1024):
    size+=len(chunk)
    if size>int(os.getenv('MAX_UPLOAD_BYTES',str(20*1024**3))):raise HTTPException(413,'Upload exceeds server limit')
    out.write(chunk);hash.update(chunk)
  # Probe outside the event loop: long uploads must not block job polling.
  import asyncio
  try:info=await asyncio.to_thread(media.probe,path)
  except (RuntimeError,ValueError):raise HTTPException(422,'Unable to read this video. Upload a valid playable video between 1 second and 6 hours.')
  with Session.begin() as s:
   duplicate=s.query(Project).filter_by(fingerprint=hash.hexdigest()).first()
   safety={'rights_confirmed':True,'duplicate':bool(duplicate),'music':'Not checked — no copyright fingerprint provider configured','originality':'Human review required. Add original commentary, narration and meaningful editing.','monetization':'AI clipping does not guarantee monetization eligibility.'}
   p=Project(id=pid,name=(name or Path(file.filename).stem)[:120],category=category,source=str(path),fingerprint=hash.hexdigest(),created=now(),safety=json.dumps(safety),duration=info['duration'],width=info['width'],height=info['height']);s.add(p)
  return project_json(p)
 except Exception:path.unlink(missing_ok=True);raise
 finally:await file.close()
@app.get('/api/projects/{pid}/source')
def source(pid):
 with Session() as s:p=get(s,Project,pid);return FileResponse(p.source)
@app.post('/api/projects/{pid}/transcript')
async def transcript(pid,file:UploadFile=File(...)):
 text=await file.read(5*1024*1024+1)
 if len(text)>5*1024*1024:raise HTTPException(413,'Transcript exceeds 5 MB')
 words=media.parse_srt(text.decode('utf-8-sig'))
 with Session.begin() as s:
  p=get(s,Project,pid);active(s,pid)
  if any(w['end']>p.duration+1 for w in words):raise HTTPException(422,'Transcript extends beyond the source video')
  p.transcript=json.dumps(words)
 return {'words':len(words)}
class Process(BaseModel):
 mode:str='chronological';minimum:float=Field(15,ge=15,le=90);maximum:float=Field(60,ge=15,le=90);target:float=Field(30,ge=15,le=90);count:int=Field(10,ge=1,le=20)
 music_check:bool=False
 transcribe:bool=True;ai_metadata:bool=False;render:bool=True;framing:str='fit';animated:bool=True;part_label:bool=True
 @model_validator(mode='after')
 def check(self):
  if not self.minimum<=self.target<=self.maximum:raise ValueError('Duration must satisfy minimum ≤ target ≤ maximum')
  if self.mode not in ['chronological','ai','audio'] or self.framing not in ['fit','crop']:raise ValueError('Unknown processing or framing mode')
  if (self.mode=='ai' or self.ai_metadata) and not ai.configured():raise ValueError('Configure the AI provider first, or use chronological / audio mode')
  return self
@app.post('/api/projects/{pid}/process')
def process(pid,body:Process):
 with Session() as s:get(s,Project,pid);active(s,pid)
 return {'job_id':worker.submit(pid,'process',body.model_dump())}
@app.get('/api/jobs')
def jobs():
 with Session() as s:return [as_dict(j) for j in s.query(Job).order_by(Job.created.desc()).limit(30)]
@app.post('/api/jobs/{jid}/retry')
def retry(jid):
 with Session() as s:
  j=get(s,Job,jid);active(s,j.project_id)
  if j.status!='failed':raise HTTPException(409,'Only failed jobs may be retried')
  return {'job_id':worker.submit(j.project_id,j.kind,json.loads(j.payload))}
@app.get('/api/clips')
def clips(project_id:str=''):
 with Session() as s:
  q=s.query(Clip)
  if project_id:q=q.filter_by(project_id=project_id)
  return [clip_json(c) for c in q.order_by(Clip.part)]
class Edit(BaseModel):
 start:float=Field(ge=0,allow_inf_nan=False);end:float=Field(gt=0,allow_inf_nan=False);title:str=Field(min_length=1,max_length=100)
 captions:list[dict]=Field(max_length=5000);metadata:dict
@app.patch('/api/clips/{cid}')
def edit(cid,body:Edit):
 with Session.begin() as s:
  c=get(s,Clip,cid);p=get(s,Project,c.project_id);active(s,p.id)
  if body.end>p.duration+.05 or not 15<=body.end-body.start<=90:raise HTTPException(422,'Clip must be 15–90 seconds and within the source')
  for w in body.captions:
   if set(w)!=set(['start','end','text']) or not isinstance(w['text'],str) or len(w['text'])>500:raise HTTPException(422,'Each caption must have start, end and text')
   if not all(isinstance(w[k],(int,float)) and math.isfinite(w[k]) for k in ['start','end']) or not 0<=w['start']<w['end']<=p.duration+.05:raise HTTPException(422,'Invalid caption timing')
  c.start=body.start;c.end=body.end;c.title=body.title;c.captions=json.dumps(body.captions);c.metadata_json=json.dumps(body.metadata);c.status='draft';c.output=''
 return {'ok':True}
class Export(BaseModel):framing:str='fit';animated:bool=True;part_label:bool=True
@app.post('/api/clips/{cid}/render')
def export(cid,body:Export):
 if body.framing not in ['fit','crop']:raise HTTPException(422,'Invalid framing')
 with Session() as s:c=get(s,Clip,cid);active(s,c.project_id);pid=c.project_id
 return {'job_id':worker.submit(pid,'render',dict(clip_ids=[cid],**body.model_dump()))}
@app.post('/api/clips/{cid}/metadata')
def generate_metadata(cid):
 with Session.begin() as s:
  c=get(s,Clip,cid);p=get(s,Project,c.project_id);active(s,p.id)
  meta=ai.metadata(p.name,p.category,json.loads(c.captions),c.part,ai.configured());c.title=meta['title'];c.metadata_json=json.dumps(meta)
 return meta
@app.get('/api/clips/{cid}/{asset}')
def asset(cid,asset:str):
 if asset not in ['video','thumbnail','captions','metadata']:raise HTTPException(404)
 with Session() as s:
  c=get(s,Clip,cid)
  if not c.output:raise HTTPException(404,'Export this clip first')
  p=Path(c.output).with_suffix({'video':'.mp4','thumbnail':'.jpg','captions':'.srt','metadata':'.json'}[asset])
  if not p.is_file():raise HTTPException(404,'Asset not available')
  return FileResponse(p,filename=p.name if asset!='thumbnail' else None)
@app.get('/api/settings')
def settings():
 with Session() as s:
  policies=s.get(Setting,'policies')
  return {'policies':json.loads(policies.value) if policies else POLICY,'ai_configured':ai.configured(),'music_configured':bool(os.getenv('AUDD_API_TOKEN')),'accounts':{p:{'configured':social.configured(p),'connected':bool(s.get(Account,p)),'authorization_pending':bool(s.get(PendingConnection,p)),'identity':json.loads(s.get(Account,p).details) if s.get(Account,p) else None,'callback':social.callback_url(p)} for p in social.CONFIG},'publishing_enabled':True,'copyright_detection':'Optional AudD excerpt recognition. No match is not proof of copyright clearance.'}
class Policies(BaseModel):policies:dict
@app.put('/api/settings/policies')
def policies(body:Policies):
 for p in ('youtube','tiktok'):
  item=body.policies.get(p,{})
  for k in ('followers','views','window_days','min_video_seconds'):
   if not isinstance(item.get(k),(int,float)) or not math.isfinite(item[k]) or item[k]<0:raise HTTPException(422,f'Invalid {p} {k}')
  if item['window_days']<1:raise HTTPException(422,'Window must be at least 1 day')
 with Session.begin() as s:
  row=s.get(Setting,'policies')
  if not row:row=Setting(key='policies',value='{}');s.add(row)
  row.value=json.dumps(body.policies)
 return {'ok':True}
@app.get('/api/accounts/{platform}/connect')
def connect(platform):return {'url':social.authorize(platform)}
@app.get('/api/accounts/{platform}/callback')
def callback(platform:str,code:str='',state:str='',error:str=''):
 if platform not in social.CONFIG:raise HTTPException(404)
 if error:raise HTTPException(400,'Platform declined authorization')
 social.exchange(platform,code,state);return RedirectResponse('/?connected='+platform)
@app.delete('/api/accounts/{platform}')
def disconnect(platform):
 with Session.begin() as s:
  a=s.get(Account,platform)
  if a:s.delete(a)
  pending=s.get(PendingConnection,platform)
  if pending:s.delete(pending)
 return {'ok':True}
@app.get('/api/analytics/{platform}')
def analytics(platform):
 if platform not in social.CONFIG:raise HTTPException(404)
 try:return social.analytics(platform)
 except ValueError as e:raise HTTPException(409,str(e))
 except Exception:raise HTTPException(502,'Official analytics request failed. Verify granted scopes, account permissions and reconnect.')
class Prepare(BaseModel):platform:str;scheduled_at:str=''
@app.post('/api/clips/{cid}/prepare')
def prepare(cid,body:Prepare):
 if body.platform not in social.CONFIG:raise HTTPException(422,'Unknown platform')
 if body.scheduled_at:
  if body.platform=='tiktok':raise HTTPException(422,'This TikTok adapter does not support scheduled publishing. Prepare a draft instead.')
  try:date=datetime.datetime.fromisoformat(body.scheduled_at.replace('Z','+00:00'))
  except ValueError:raise HTTPException(422,'Invalid schedule')
  if date.tzinfo is None or date<=datetime.datetime.now(datetime.timezone.utc):raise HTTPException(422,'Schedule must be a future time including timezone')
 with Session.begin() as s:
  c=get(s,Clip,cid)
  if c.status!='ready':raise HTTPException(409,'Render the latest clip edits first')
  meta=json.loads(c.metadata_json)
  payload={'snippet':{'title':c.title,'description':meta.get('description',''),'tags':meta.get('keywords',[]),'categoryId':'20'},'status':{'privacyStatus':'private',**({'publishAt':body.scheduled_at} if body.scheduled_at else {})}} if body.platform=='youtube' else {'post_info':{'title':meta.get('tiktok_caption',c.title),'privacy_level':'SELF_ONLY'},'source_info':{'source':'FILE_UPLOAD'}}
  d=Draft(id=uuid.uuid4().hex,clip_id=cid,platform=body.platform,scheduled_at=body.scheduled_at,payload=json.dumps(payload),status='awaiting_integration');s.add(d)
  return as_dict(d)
@app.get('/api/drafts')
def drafts():
 with Session() as s:return [as_dict(d) for d in s.query(Draft)]
@app.post('/api/drafts/{did}/publish')
def publish(did):raise HTTPException(409,'Open Publishing, review current account and privacy settings, then use the explicit publish action. Legacy drafts are not automatically sent.')
DIST=ROOT/'frontend'/'dist'
if DIST.exists():app.mount('/',StaticFiles(directory=DIST,html=True),name='frontend')
