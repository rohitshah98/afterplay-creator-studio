"""Official wire protocols; upload receipt is distinct from publication confirmation."""
import json,os,time,re,urllib.parse
from pathlib import Path
import httpx
from . import social

class PublishError(Exception):
 def __init__(self,message,temporary=False,ambiguous=False,fallback=False):
  super().__init__(message);self.temporary=temporary;self.ambiguous=ambiguous;self.fallback=fallback

def check(r):
 if r.is_error:
  # Do not include signed upload URLs, bearer tokens or secrets in user-visible errors.
  message=f'Platform returned HTTP {r.status_code}'
  try:
   e=r.json().get('error',{});message+=': '+str(e.get('message',''))[:300]
  except Exception:pass
  raise PublishError(message,temporary=r.status_code in (408,429,500,502,503,504))
 return r

def tt_call(path,payload=None):
 r=httpx.post('https://open.tiktokapis.com/v2/'+path,headers={'Authorization':'Bearer '+social.access('tiktok')},json=payload or {},timeout=45)
 if path.endswith('video/init/') and r.status_code>=500:raise PublishError('TikTok initialization returned a server error; verify provider status before a new upload.',ambiguous=True)
 check(r);j=r.json();e=j.get('error',{})
 if e.get('code') not in (None,'ok'):
  code=e['code'];raise PublishError('TikTok: '+code,temporary=code in ['rate_limit_exceeded','internal_error','spam_risk_too_many_posts'],ambiguous=code=='internal_error' and path.endswith('video/init/'),fallback=code in ['scope_not_authorized','unaudited_client_can_only_post_to_private_accounts','unaudited_client_can_only_post_to_private_users'])
 return j.get('data',{})
def creator_info():return tt_call('post/publish/creator_info/query/')
def tt_validate(info,payload,duration):
 if payload.get('privacy_level') not in info.get('privacy_level_options',[]):raise PublishError('Choose a privacy option returned by TikTok for this creator')
 if duration>info.get('max_video_post_duration_sec',0):raise PublishError('Clip exceeds this creator’s TikTok duration limit')
 if not os.getenv('TIKTOK_APP_AUDITED','').lower()=='true' and payload['privacy_level']!='SELF_ONLY':raise PublishError('Unaudited TikTok apps are restricted to private posts',fallback=True)
 if payload.get('brand_content_toggle') and payload['privacy_level']=='SELF_ONLY':raise PublishError('TikTok branded content cannot use private visibility')
 for name in ('comment','duet','stitch'):
  if info.get(name+'_disabled'):payload['disable_'+name]=True
 return payload

def valid_session(url,platform):
 u=urllib.parse.urlparse(url);host=(u.hostname or '').lower()
 allowed=(host=='www.googleapis.com' or host.endswith('.googleapis.com')) if platform=='youtube' else (host=='tiktokapis.com' or host.endswith('.tiktokapis.com'))
 if u.scheme!='https' or not allowed:raise PublishError('Provider returned an unexpected upload destination')
 return url

def yt_init(payload,size):
 r=httpx.post('https://www.googleapis.com/upload/youtube/v3/videos',params={'uploadType':'resumable','part':'snippet,status'},headers={'Authorization':'Bearer '+social.access('youtube'),'X-Upload-Content-Length':str(size),'X-Upload-Content-Type':'video/mp4'},json=payload,timeout=60)
 if r.status_code>=500:raise PublishError('YouTube initialization returned a server error; verify provider state before retrying.',ambiguous=True)
 check(r)
 if not r.headers.get('Location'):raise PublishError('YouTube did not return a resumable session')
 return valid_session(r.headers['Location'],'youtube')
def yt_probe(session,size):
 r=httpx.put(session,headers={'Authorization':'Bearer '+social.access('youtube'),'Content-Length':'0','Content-Range':f'bytes */{size}'},content=b'',timeout=60)
 if r.status_code in (200,201):return size,r.json().get('id','')
 if r.status_code==308:
  match=re.search(r'bytes=0-(\d+)',r.headers.get('Range',''));return int(match[1])+1 if match else 0,''
 if r.status_code in (404,410):raise PublishError('YouTube upload session expired. Verify YouTube Studio before retrying; a duplicate upload was not started.',ambiguous=True)
 check(r);raise PublishError('Unexpected YouTube upload status')
def yt_upload(session,path,offset,save):
 size=Path(path).stat().st_size;chunk=8*1024*1024
 with open(path,'rb') as f:
  f.seek(offset)
  while offset<size:
   data=f.read(min(chunk,size-offset))
   r=httpx.put(session,headers={'Authorization':'Bearer '+social.access('youtube'),'Content-Type':'video/mp4','Content-Length':str(len(data)),'Content-Range':f'bytes {offset}-{offset+len(data)-1}/{size}'},content=data,timeout=180)
   if r.status_code in (200,201):
    vid=r.json().get('id')
    if not vid:raise PublishError('YouTube response has no video ID',ambiguous=True)
    save(size);return vid
   if r.status_code!=308:check(r);raise PublishError('Unexpected YouTube upload response')
   match=re.search(r'bytes=0-(\d+)',r.headers.get('Range',''))
   if not match:raise PublishError('YouTube omitted the acknowledged range; reconciling the existing session.',temporary=True)
   next_offset=int(match[1])+1
   if next_offset<=offset:raise PublishError('YouTube upload made no progress',temporary=True)
   offset=next_offset;save(offset);f.seek(offset)
 raise PublishError('Upload ended without confirmation; resumable status must be checked',temporary=True)
def yt_status(vid):
 r=check(httpx.get('https://www.googleapis.com/youtube/v3/videos',params={'part':'status,processingDetails','id':vid},headers={'Authorization':'Bearer '+social.access('youtube')},timeout=45));items=r.json().get('items',[])
 if not items:return {'state':'processing','message':'Waiting for YouTube video status'}
 v=items[0];s=v.get('status',{});proc=v.get('processingDetails',{}).get('processingStatus')
 if s.get('uploadStatus') in ['failed','rejected','deleted'] or proc in ['failed','terminated']:return {'state':'failed','message':'YouTube: '+str(s.get('rejectionReason') or s.get('failureReason') or proc or s.get('uploadStatus'))}
 if s.get('uploadStatus')=='processed' or proc=='succeeded':
  if s.get('publishAt') and s.get('privacyStatus')=='private':return {'state':'platform_scheduled','message':'YouTube confirms a scheduled private video'}
  return {'state':'published' if s.get('privacyStatus')=='public' else 'available','message':'YouTube confirms processed video · '+s.get('privacyStatus','unknown visibility'),'url':'https://www.youtube.com/watch?v='+vid}
 return {'state':'processing','message':'YouTube is processing the video'}
def tt_init(payload,size):
 chunk=min(size,10*1024*1024);count=max(1,size//chunk)
 d=tt_call('post/publish/video/init/',{'post_info':payload,'source_info':{'source':'FILE_UPLOAD','video_size':size,'chunk_size':chunk,'total_chunk_count':count}})
 if not d.get('publish_id') or not d.get('upload_url'):raise PublishError('TikTok did not return an upload session',ambiguous=True)
 return d['publish_id'],valid_session(d['upload_url'],'tiktok'),chunk,count

def tt_upload(session,path,offset,chunk,count,save):
 size=Path(path).stat().st_size
 with open(path,'rb') as f:
  index=offset//chunk;f.seek(offset)
  while offset<size:
   length=size-offset if index==count-1 else chunk;data=f.read(length)
   r=httpx.put(session,headers={'Content-Type':'video/mp4','Content-Length':str(len(data)),'Content-Range':f'bytes {offset}-{offset+len(data)-1}/{size}'},content=data,timeout=180)
   if r.status_code not in (201,206):
    if r.status_code in (403,404):raise PublishError('TikTok upload session expired. No replacement upload was started.',ambiguous=True)
    check(r);raise PublishError('Unexpected TikTok chunk response',ambiguous=True)
   offset+=len(data);index+=1;save(offset)

def tt_status(publish_id):
 d=tt_call('post/publish/status/fetch/',{'publish_id':publish_id});state=d.get('status')
 if state=='FAILED':return {'state':'failed','message':'TikTok: '+d.get('fail_reason','Upload rejected')}
 if state=='PUBLISH_COMPLETE':
  ids=d.get('publicaly_available_post_id',[])
  return {'state':'published','message':'TikTok confirms publication','post_ids':[str(i) for i in ids]}
 return {'state':'processing','message':'TikTok: '+str(state or 'awaiting status'),'uploaded_bytes':int(d.get('uploaded_bytes',0))}
