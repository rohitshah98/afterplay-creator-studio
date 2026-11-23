import os,json,time,re,datetime,urllib.parse
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,model_validator
from .db import Session,Publication,PublishEvent,Account,Clip,as_dict
from . import social,publishing as pub,publish_providers as providers
router=APIRouter(prefix='/api')
class Credentials(BaseModel):
 client_id:str=Field(min_length=5,max_length=1000)
 client_secret:str=Field(min_length=5,max_length=2000)
 redirect_uri:str=Field('',max_length=2000)
@router.post('/accounts/{platform}/configure')
def configure(platform:str,body:Credentials):
 if platform not in social.CONFIG:raise HTTPException(404)
 with Session() as s:
  if s.get(Account,platform):raise HTTPException(409,'Disconnect the account before replacing developer-app credentials')
 social.save_credentials(platform,body.client_id.strip(),body.client_secret.strip(),body.redirect_uri.strip())
 return {'configured':True,'callback':social.callback_url(platform)}
class CompleteAuthorization(BaseModel):
 callback_url:str=Field(min_length=10,max_length=8000)
@router.post('/accounts/{platform}/complete')
def complete_authorization(platform:str,body:CompleteAuthorization):
 if platform not in social.CONFIG:raise HTTPException(404)
 supplied=urllib.parse.urlparse(body.callback_url);expected=urllib.parse.urlparse(social.callback_url(platform))
 if (supplied.scheme,supplied.netloc,supplied.path)!=(expected.scheme,expected.netloc,expected.path):raise HTTPException(422,'This URL does not match your configured registered callback')
 params=urllib.parse.parse_qs(supplied.query)
 if params.get('error'):raise HTTPException(422,'Platform declined authorization')
 code=params.get('code',[''])[0];state=params.get('state',[''])[0]
 if not code or not state:raise HTTPException(422,'Authorized callback URL must contain code and state')
 social.exchange(platform,code,state)
 return {'connected':True,'identity':social.account_details(platform)}
@router.post('/accounts/{platform}/finish')
def finish_connection(platform:str):
 if platform not in social.CONFIG:raise HTTPException(404)
 return {'connected':True,'identity':social.complete_pending(platform)}
@router.get('/publishing/config')
def configuration():
 return {**pub.config(),'daily_usage':pub.daily_usage(),'daily_reset_at':(datetime.datetime.now(datetime.timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)+datetime.timedelta(days=1)).isoformat(),'direct_tiktok':pub.direct_enabled(),'tiktok_scheduling':pub.tiktok_scheduling_enabled(),'tiktok_audited':os.getenv('TIKTOK_APP_AUDITED','false').lower()=='true','manual_tiktok_url':'https://www.tiktok.com/tiktokstudio/upload','accounts':{p:social.account_details(p) for p in ['youtube','tiktok']}}
class QueueConfig(BaseModel):
 paused:bool=False
 daily_limits:dict[str,int]
 max_attempts:int=Field(4,ge=1,le=8)
 @model_validator(mode='after')
 def check(self):
  if set(self.daily_limits)!=set(['youtube','tiktok']) or any(not 1<=n<=100 for n in self.daily_limits.values()):raise ValueError('Daily limits must be 1–100 for each platform; platform limits may be lower')
  return self
@router.put('/publishing/config')
def save_configuration(body:QueueConfig):pub.save_config({**body.model_dump(),'timezone':'UTC'});return configuration()
@router.get('/publishing/tiktok/creator')
def creator():
 if not pub.direct_enabled():return {'available':False,'manual_url':'https://www.tiktok.com/tiktokstudio/upload','reason':'Direct Post is not enabled for this developer app. Use the official manual publishing flow.'}
 try:return {'available':True,**providers.creator_info()}
 except (ValueError,providers.PublishError) as e:return {'available':False,'manual_url':'https://www.tiktok.com/tiktokstudio/upload','reason':str(e)}
class QueueRequest(BaseModel):
 clip_id:str
 platforms:list[Literal['youtube','tiktok']]=Field(min_length=1,max_length=2)
 scheduled_at:str=''
 consent:bool=False
 youtube_title:str=Field('',max_length=100)
 youtube_description:str=Field('',max_length=5000)
 youtube_tags:list[str]=Field(default_factory=list,max_length=30)
 youtube_privacy:Literal['private','unlisted','public']='private'
 made_for_kids:bool=False
 contains_synthetic_media:bool=False
 tiktok_caption:str=Field('',max_length=2200)
 tiktok_privacy:str=''
 allow_comment:bool=False
 allow_duet:bool=False
 allow_stitch:bool=False
 brand_organic:bool=False
 brand_content:bool=False
 is_aigc:bool=False
 tiktok_terms_consent:bool=False
@router.post('/publishing/queue')
def enqueue(body:QueueRequest):
 if not body.consent:raise HTTPException(422,'Explicit upload consent is required')
 if len(body.tiktok_caption.encode('utf-16-le'))//2>2200:raise HTTPException(422,'TikTok caption exceeds 2200 UTF-16 units')
 if sum(len(t)+2 for t in body.youtube_tags)>500:raise HTTPException(422,'YouTube tags exceed 500 characters')
 if 'youtube' in body.platforms and not body.youtube_title.strip():raise HTTPException(422,'YouTube title is required')
 payloads={'youtube':{'snippet':{'title':body.youtube_title,'description':body.youtube_description,'tags':body.youtube_tags,'categoryId':'20'},'status':{'privacyStatus':body.youtube_privacy,'selfDeclaredMadeForKids':body.made_for_kids,'containsSyntheticMedia':body.contains_synthetic_media}},'tiktok':{'title':body.tiktok_caption,'privacy_level':body.tiktok_privacy,'disable_comment':not body.allow_comment,'disable_duet':not body.allow_duet,'disable_stitch':not body.allow_stitch,'brand_organic_toggle':body.brand_organic,'brand_content_toggle':body.brand_content,'is_aigc':body.is_aigc}}
 if 'tiktok' in body.platforms and pub.direct_enabled() and (not body.scheduled_at or pub.tiktok_scheduling_enabled()):
  try:info=providers.creator_info()
  except (ValueError,providers.PublishError) as e:
   if isinstance(e,providers.PublishError) and e.temporary:raise HTTPException(429,'TikTok is temporarily unavailable or has reached its current cap. Try again later.')
   payloads['tiktok']['_manual_reason']=str(e)
  else:
   if not body.tiktok_terms_consent:raise HTTPException(422,'Review TikTok’s Music Usage Confirmation and applicable Branded Content Policy before authorizing Direct Post')
   payloads['tiktok']['_creator_info']=info;payloads['tiktok']['_creator_username']=info.get('creator_username','')
 return pub.enqueue(body.clip_id,body.platforms,payloads,body.scheduled_at,body.consent)
@router.get('/publishing/history')
def history():
 with Session() as s:return [pub.serialize(p) for p in s.query(Publication).order_by(Publication.created.desc()).limit(200)]
@router.get('/publishing/history/{pid}/events')
def events(pid:str):
 with Session() as s:return [as_dict(e) for e in s.query(PublishEvent).filter_by(publication_id=pid).order_by(PublishEvent.id)]
@router.post('/publishing/history/{pid}/{action}')
def control(pid:str,action:str):pub.action(pid,action);return {'ok':True}
@router.get('/publishing/history/{pid}/manual')
def manual(pid:str):
 with Session() as s:
  p=s.get(Publication,pid)
  if not p or p.platform!='tiktok':raise HTTPException(404)
  payload=json.loads(p.payload)
  return {'url':'https://www.tiktok.com/tiktokstudio/upload','video_url':'/api/publishing/history/'+pid+'/file','caption':payload.get('title',''),'message':'Download the finished video, copy the caption and complete the official TikTok uploader. This app cannot confirm manual publication.'}
@router.get('/publishing/history/{pid}/file')
def manual_file(pid:str):
 from fastapi.responses import FileResponse
 with Session() as s:
  p=s.get(Publication,pid)
  if not p:raise HTTPException(404)
  return FileResponse(p.asset_path,filename='creator-'+pid[:8]+'.mp4',media_type='video/mp4')
