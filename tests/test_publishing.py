"""No live uploads: actual protocol code is exercised through an HTTP mock transport."""
import os,tempfile,json,time,uuid,datetime
from pathlib import Path
import pytest,httpx
os.environ['CREATOR_SCHEDULER_ENABLED']='false'
os.environ['CREATOR_DATA_DIR']=tempfile.mkdtemp(prefix='afterplay-publishing-tests-')
from backend.db import Session,Project,Clip,Account,Publication,PublishEvent,Setting,init,now,DATA
from backend import social,publishing as pub,publish_providers as providers
from backend.main import app
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def isolated_publishing(monkeypatch):
 init();social.ensure_key()
 monkeypatch.setenv('TIKTOK_DIRECT_POST_ENABLED','false');monkeypatch.setenv('TIKTOK_SCHEDULING_APPROVED','false');monkeypatch.setenv('TIKTOK_APP_AUDITED','false')
 with Session.begin() as s:
  s.query(PublishEvent).delete();s.query(Publication).delete();s.query(Account).delete();s.query(Setting).filter(Setting.key.in_(['publishing','oauth_client_youtube','oauth_client_tiktok'])).delete()
 pub.ACTIVE.clear()
 yield

def seed(platforms=('youtube','tiktok')):
 id=uuid.uuid4().hex;source=DATA/(id+'.mp4');source.write_bytes(b'media-'+id.encode())
 with Session.begin() as s:
  p=Project(id=id,name='Original test video',category='Gaming challenges',source=str(source),duration=30,width=1080,height=1920,fingerprint=id,created=now());s.add(p);s.flush()
  c=Clip(id=id,project_id=id,part=1,start=0,end=30,title='Original commentary',status='ready',output=str(source),metadata_json='{}',captions='[]');s.add(c)
  for platform in platforms:
   token=social.cipher().encrypt(json.dumps({'access_token':'unit-access','refresh_token':'unit-refresh','expires_at':time.time()+3600}).encode()).decode()
   s.add(Account(platform=platform,token=token,details=json.dumps({'id':platform+'-account','name':platform+' creator'})))
 return id

def bodies():
 return {'youtube':{'snippet':{'title':'Original gaming story','description':'Context #gaming','tags':['gaming'],'categoryId':'20'},'status':{'privacyStatus':'private'}},'tiktok':{'title':'Original story #gaming','privacy_level':'SELF_ONLY','disable_comment':True,'disable_duet':True,'disable_stitch':True,'brand_content_toggle':False,'brand_organic_toggle':False,'is_aigc':False,'_creator_info':info(),'_creator_username':'unitcreator'}}
def info():return {'creator_nickname':'Unit Creator','creator_username':'unitcreator','privacy_level_options':['SELF_ONLY','PUBLIC_TO_EVERYONE'],'max_video_post_duration_sec':60,'comment_disabled':True,'duet_disabled':True,'stitch_disabled':False}
def get(pid):
 with Session() as s:return s.get(Publication,pid)
def queued(platform='youtube',monkeypatch=None):
 cid=seed();payload=bodies();return pub.enqueue(cid,[platform],payload,'',True)['publications'][0]['id']

def test_dual_queue_dedupe_snapshot_and_manual_fallback():
 cid=seed();r=pub.enqueue(cid,['youtube','tiktok'],bodies(),'',True);assert len(r['publications'])==2
 yt,tt=r['publications'];assert yt['state']=='queued' and tt['state']=='manual_required'
 again=pub.enqueue(cid,['youtube','tiktok'],bodies(),'',True);assert all(p['duplicate'] for p in again['publications'])
 with Session() as s:
  original=Path(s.get(Clip,cid).output);frozen=Path(s.get(Publication,yt['id']).asset_path)
 before=frozen.read_bytes();original.write_bytes(b'edited source');assert frozen.read_bytes()==before
 with TestClient(app) as client:
  m=client.get('/api/publishing/history/'+tt['id']+'/manual').json();assert m['url']=='https://www.tiktok.com/tiktokstudio/upload'
  assert 'cannot confirm' in m['message'];assert client.get(m['video_url']).content==before
  assert 'session_secret' not in client.get('/api/publishing/history').json()[0]

def test_consent_missing_account_and_explicit_tiktok_privacy(monkeypatch):
 cid=seed(platforms=())
 with pytest.raises(ValueError,match='consent'):pub.enqueue(cid,['youtube'],bodies(),'',False)
 with pytest.raises(ValueError,match='Connect'):pub.enqueue(cid,['youtube'],bodies(),'',True)
 with TestClient(app) as client:
  assert client.post('/api/publishing/queue',json={'clip_id':cid,'platforms':['tiktok'],'consent':False}).status_code==422
  assert client.post('/api/publishing/queue',json={'clip_id':cid,'platforms':['youtube'],'consent':True}).status_code==422
 with pytest.raises(providers.PublishError,match='privacy'):providers.tt_validate(info(),{'privacy_level':''},30)
 with pytest.raises(providers.PublishError,match='Unaudited'):providers.tt_validate(info(),{'privacy_level':'PUBLIC_TO_EVERYONE'},30)
 adjusted=providers.tt_validate(info(),{'privacy_level':'SELF_ONLY','disable_comment':False},30);assert adjusted['disable_comment']
 with pytest.raises(providers.PublishError,match='duration'):providers.tt_validate(info(),{'privacy_level':'SELF_ONLY'},61)

def test_youtube_receipt_processing_and_publication(monkeypatch):
 pid=queued();calls=[]
 monkeypatch.setattr(providers,'yt_init',lambda body,size:calls.append(body) or 'https://www.googleapis.com/upload/session')
 monkeypatch.setattr(providers,'yt_probe',lambda *a:(0,''))
 monkeypatch.setattr(providers,'yt_upload',lambda url,path,offset,save:save(Path(path).stat().st_size) or 'youtube123')
 pub.process(pid);p=get(pid);assert p.state=='processing' and p.remote_id=='youtube123' and not p.published_url
 assert calls[0]['snippet']['tags']==['gaming']
 monkeypatch.setattr(providers,'yt_status',lambda id:{'state':'published','message':'Confirmed public','url':'https://www.youtube.com/watch?v='+id})
 pub.process(pid);assert get(pid).state=='published' and get(pid).published_url.endswith('youtube123')
 assert len(calls)==1

def test_youtube_resumable_protocol(monkeypatch,tmp_path):
 payload=bodies()['youtube'];path=tmp_path/'clip.mp4';path.write_bytes(b'abcdef');calls=[]
 def handler(req):
  calls.append(req)
  if req.method=='POST':
   assert json.loads(req.content)['snippet']['title'];return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/unit'})
  if req.headers.get('content-range')=='bytes */6':return httpx.Response(308,headers={'Range':'bytes=0-2'})
  assert req.headers['content-range']=='bytes 3-5/6' and req.content==b'def';return httpx.Response(200,json={'id':'vid123'})
 client=httpx.Client(transport=httpx.MockTransport(handler));monkeypatch.setattr(social,'access',lambda p:'fake-token')
 monkeypatch.setattr(providers.httpx,'post',client.post);monkeypatch.setattr(providers.httpx,'put',client.put)
 session=providers.yt_init(payload,6);offset,vid=providers.yt_probe(session,6);assert offset==3 and not vid
 progress=[];assert providers.yt_upload(session,path,offset,progress.append)=='vid123';assert progress==[6]

def test_tiktok_chunk_protocol_and_confirmed_status(monkeypatch,tmp_path):
 size=25*1024*1024+123;path=tmp_path/'clip.mp4';path.write_bytes(b'x'*size);ranges=[]
 def handler(req):
  if req.url.path.endswith('init/'):
   j=json.loads(req.content);assert j['source_info']['total_chunk_count']==2;assert j['post_info']['title']=='caption #gaming'
   return httpx.Response(200,json={'error':{'code':'ok'},'data':{'publish_id':'publish-id','upload_url':'https://upload.us.tiktokapis.com/video/unit'}})
  if req.method=='PUT':
   ranges.append(req.headers['content-range']);assert len(req.content)==int(req.headers['content-length'])
   return httpx.Response(206 if len(ranges)==1 else 201)
  return httpx.Response(200,json={'error':{'code':'ok'},'data':{'status':'PUBLISH_COMPLETE','publicaly_available_post_id':['987']}})
 client=httpx.Client(transport=httpx.MockTransport(handler));monkeypatch.setattr(social,'access',lambda p:'fake-token');monkeypatch.setattr(providers.httpx,'post',client.post);monkeypatch.setattr(providers.httpx,'put',client.put)
 remote,url,chunk,count=providers.tt_init({'title':'caption #gaming'},size);progress=[];providers.tt_upload(url,path,0,chunk,count,progress.append)
 assert ranges==[f'bytes 0-{chunk-1}/{size}',f'bytes {chunk}-{size-1}/{size}'];assert progress[-1]==size
 assert providers.tt_status(remote)['state']=='published'

def test_tiktok_upload_is_not_publication(monkeypatch):
 monkeypatch.setenv('TIKTOK_DIRECT_POST_ENABLED','true');pid=queued('tiktok');monkeypatch.setattr(pub,'clip_duration',lambda p:30)
 monkeypatch.setattr(providers,'creator_info',info);monkeypatch.setattr(providers,'tt_init',lambda payload,size:('tt123','https://upload.us.tiktokapis.com/unit',size,1))
 monkeypatch.setattr(providers,'tt_status',lambda id:{'state':'processing','message':'Processing','uploaded_bytes':0});monkeypatch.setattr(providers,'tt_upload',lambda url,path,offset,chunk,count,save:save(Path(path).stat().st_size))
 pub.process(pid);assert get(pid).state=='processing' and not get(pid).published_url
 monkeypatch.setattr(providers,'tt_status',lambda id:{'state':'published','message':'TikTok confirms','post_ids':['123']});pub.process(pid)
 assert get(pid).state=='published' and get(pid).published_url=='https://www.tiktok.com/@unitcreator/video/123'

def test_retry_safety_and_restart_recovery(monkeypatch):
 pid=queued()
 def lost(*a):raise httpx.ReadTimeout('Do not expose signed URL')
 monkeypatch.setattr(providers,'yt_init',lost);pub.process(pid);assert get(pid).state=='needs_review'
 with pytest.raises(ValueError,match='cannot be retried'):pub.action(pid,'retry')
 pub.record(pid,'uploading','test interruption');pub.recover();assert get(pid).state=='needs_review'
 pub.session_set(pid,{'url':'https://www.googleapis.com/upload/session'});pub.record(pid,'uploading','test session saved');pub.recover();assert get(pid).state=='retry_wait'


def test_temporary_rejection_retries_without_new_duplicate(monkeypatch):
 pid=queued()
 def limited(*a):raise providers.PublishError('HTTP 429',temporary=True)
 monkeypatch.setattr(providers,'yt_init',limited);pub.process(pid);p=get(pid)
 assert p.state=='retry_wait' and not p.upload_attempted and p.due>time.time()
 pub.action(pid,'pause');assert get(pid).state=='paused';pub.action(pid,'resume');assert get(pid).state=='queued';pub.action(pid,'cancel');assert get(pid).state=='cancelled'

def test_native_youtube_schedule_and_account_binding(monkeypatch):
 cid=seed();payload=bodies();payload['youtube']['status']['privacyStatus']='public';scheduled=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1)).isoformat()
 r=pub.enqueue(cid,['youtube','tiktok'],payload,scheduled,True);p=get(r['publications'][0]['id']);assert json.loads(p.payload)['status']['publishAt']==scheduled;assert json.loads(p.payload)['status']['privacyStatus']=='private';assert p.due<time.time()+2;assert r['publications'][1]['state']=='manual_required'
 with Session.begin() as s:s.get(Account,'youtube').details=json.dumps({'id':'different-channel','name':'Other channel'})
 pub.process(p.id);assert get(p.id).state=='needs_connection'

def test_encrypted_credentials_tokens_and_oauth_state(monkeypatch):
 with TestClient(app) as client:
  r=client.post('/api/accounts/youtube/configure',json={'client_id':'unit-client','client_secret':'unit-secret'});assert r.status_code==200
  with Session() as s:encrypted=s.get(Setting,'oauth_client_youtube').value;assert 'unit-secret' not in encrypted
  assert social.credentials('youtube')==('unit-client','unit-secret')
  r=client.get('/api/accounts/youtube/connect');assert r.status_code==200 and 'accounts.google.com' in r.json()['url']
  assert client.get('/api/accounts/youtube/callback?code=x&state=wrong').status_code==422
  assert 'unit-secret' not in json.dumps(client.get('/api/settings').json())

def test_secure_token_refresh_preserves_refresh_token(monkeypatch):
 seed(platforms=('youtube',));social.save_credentials('youtube','client-unit','secret-unit')
 with Session.begin() as s:
  a=s.get(Account,'youtube');a.token=social.cipher().encrypt(json.dumps({'access_token':'expired','refresh_token':'keep-me','expires_at':0}).encode()).decode()
 def handler(req):
  assert b'refresh_token=keep-me' in req.content
  return httpx.Response(200,json={'access_token':'new-token','expires_in':3600})
 client=httpx.Client(transport=httpx.MockTransport(handler));monkeypatch.setattr(social.httpx,'post',client.post)
 assert social.access('youtube')=='new-token'
 with Session() as s:
  stored=s.get(Account,'youtube').token;assert 'new-token' not in stored;assert json.loads(social.cipher().decrypt(stored.encode()))['refresh_token']=='keep-me'

def test_pause_limits_and_independent_platform_dispatch(monkeypatch):
 monkeypatch.setenv('TIKTOK_DIRECT_POST_ENABLED','true');cid=seed();r=pub.enqueue(cid,['youtube','tiktok'],bodies(),'',True)
 tasks=[]
 class Pool:
  def submit(self,fn):tasks.append(fn)
 monkeypatch.setattr(pub,'POOL',Pool());pub.save_config({**pub.DEFAULTS,'paused':True});pub.tick();assert not tasks
 pub.save_config({**pub.DEFAULTS,'paused':False});monkeypatch.setattr(pub,'limits_allow',lambda p,c:p=='tiktok');pub.tick();assert len(tasks)==1 and pub.ACTIVE=={'tiktok'}
 pub.ACTIVE.clear()
 with TestClient(app) as client:
  assert client.put('/api/publishing/config',json={'daily_limits':{'youtube':0,'tiktok':3}}).status_code==422

def test_external_registered_callback_state_and_encryption(monkeypatch):
 from urllib.parse import urlparse,parse_qs,urlencode
 social.save_credentials('tiktok','unit-client','unit-secret','https://creator.example/oauth/callback')
 assert social.callback_url('tiktok')=='https://creator.example/oauth/callback'
 url=social.authorize('tiktok');state=parse_qs(urlparse(url).query)['state'][0]
 def exchange(req):
  assert b'redirect_uri=https%3A%2F%2Fcreator.example%2Foauth%2Fcallback' in req.content
  return httpx.Response(200,json={'access_token':'issued-token','refresh_token':'rotating-refresh','expires_in':3600})
 transport=httpx.Client(transport=httpx.MockTransport(exchange));monkeypatch.setattr(social.httpx,'post',transport.post);monkeypatch.setattr(social,'identity',lambda p,t:{'id':'approved-creator','name':'My creator'})
 with TestClient(app) as client:
  assert client.post('/api/accounts/tiktok/complete',json={'callback_url':'https://wrong.example/?code=unit&state='+state}).status_code==422
  callback='https://creator.example/oauth/callback?'+urlencode({'code':'one-time-code','state':state})
  r=client.post('/api/accounts/tiktok/complete',json={'callback_url':callback});assert r.status_code==200 and r.json()['identity']['id']=='approved-creator'
  assert client.post('/api/accounts/tiktok/complete',json={'callback_url':callback}).status_code==422
  with Session() as s:assert 'issued-token' not in s.get(Account,'tiktok').token

def test_ambiguous_init_server_errors_never_auto_resubmit(monkeypatch):
 def handler(req):return httpx.Response(503,json={'error':{'message':'temporarily unavailable'}})
 client=httpx.Client(transport=httpx.MockTransport(handler));monkeypatch.setattr(providers.httpx,'post',client.post);monkeypatch.setattr(social,'access',lambda p:'fake')
 for fn,args in [(providers.yt_init,(bodies()['youtube'],100)),(providers.tt_init,({'title':'test'},100))]:
  with pytest.raises(providers.PublishError) as e:fn(*args)
  assert e.value.ambiguous

def test_youtube_native_schedule_pause_resume_cancel(monkeypatch):
 cid=seed();payload=bodies();payload['youtube']['status'].update(privacyStatus='public',selfDeclaredMadeForKids=False)
 when=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1)).isoformat()
 pid=pub.enqueue(cid,['youtube'],payload,when,True)['publications'][0]['id'];pub.record(pid,'platform_scheduled','Provider confirms schedule',remote_id='native-schedule-id')
 sent=[]
 def handler(req):sent.append(json.loads(req.content));return httpx.Response(200,json={'id':'native-schedule-id'})
 client=httpx.Client(transport=httpx.MockTransport(handler));monkeypatch.setattr(providers.httpx,'put',client.put);monkeypatch.setattr(social,'access',lambda p:'fake')
 pub.action(pid,'pause');assert get(pid).state=='paused';assert sent[-1]['status']['privacyStatus']=='private' and 'publishAt' not in sent[-1]['status']
 pub.action(pid,'resume');assert get(pid).state=='processing';assert sent[-1]['status']['publishAt']
 pub.record(pid,'platform_scheduled','Confirmed again');pub.action(pid,'cancel');assert get(pid).state=='cancelled';assert sent[-1]['status']['privacyStatus']=='private' and 'publishAt' not in sent[-1]['status']

def test_provider_upload_destinations_are_restricted():
 for url in ['http://www.googleapis.com/upload','https://www.googleapis.com.attacker.example/upload','https://localhost/upload']:
  with pytest.raises(providers.PublishError):providers.valid_session(url,'youtube')
 for url in ['https://tiktokapis.com.attacker.example/video','http://upload.us.tiktokapis.com/video']:
  with pytest.raises(providers.PublishError):providers.valid_session(url,'tiktok')

def test_oauth_state_survives_reload_and_is_single_use(monkeypatch):
 import importlib
 from urllib.parse import urlparse,parse_qs
 from backend.db import OAuthAttempt
 social.save_credentials('youtube','unit-client','unit-secret')
 state=parse_qs(urlparse(social.authorize('youtube')).query)['state'][0]
 with Session() as s:
  attempt=s.query(OAuthAttempt).filter_by(platform='youtube').first();assert attempt.id!=state
 importlib.reload(social)
 def response(*a,**kw):return httpx.Response(200,request=httpx.Request('POST','https://oauth2.googleapis.com/token'),json={'access_token':'unit-token','expires_in':3600})
 monkeypatch.setattr(social.httpx,'post',response);monkeypatch.setattr(social,'identity',lambda *a:{'id':'channel','name':'Test channel'})
 social.exchange('youtube','one-time-code',state)
 assert social.account_details('youtube')['id']=='channel'
 with pytest.raises(ValueError,match='expired or invalid'):social.exchange('youtube','one-time-code',state)

def test_expired_or_changed_oauth_context_is_rejected(monkeypatch):
 from urllib.parse import urlparse,parse_qs
 from backend.db import OAuthAttempt
 social.save_credentials('youtube','unit-client','unit-secret')
 state=parse_qs(urlparse(social.authorize('youtube')).query)['state'][0]
 with Session.begin() as s:
  for row in s.query(OAuthAttempt):row.expires=0
 with pytest.raises(ValueError,match='expired or invalid'):social.exchange('youtube','code',state)
 state=parse_qs(urlparse(social.authorize('youtube')).query)['state'][0]
 social.save_credentials('youtube','different-client','unit-secret')
 with pytest.raises(ValueError,match='expired or invalid'):social.exchange('youtube','code',state)

def test_disabled_api_retains_encrypted_authorization_for_finish(monkeypatch):
 from urllib.parse import urlparse,parse_qs
 from backend.db import PendingConnection
 social.save_credentials('youtube','unit-client','unit-secret')
 state=parse_qs(urlparse(social.authorize('youtube')).query)['state'][0]
 enabled=False
 def handler(req):
  if req.method=='POST':return httpx.Response(200,json={'access_token':'preserved-token','refresh_token':'preserved-refresh','expires_in':3600})
  if not enabled:return httpx.Response(403,json={'error':{'errors':[{'reason':'accessNotConfigured'}]}})
  return httpx.Response(200,json={'items':[{'id':'my-channel','snippet':{'title':'My channel'}}]})
 client=httpx.Client(transport=httpx.MockTransport(handler));monkeypatch.setattr(social.httpx,'post',client.post);monkeypatch.setattr(social.httpx,'get',client.get)
 with pytest.raises(ValueError,match='YouTube Data API v3 is disabled'):social.exchange('youtube','one-time-code',state)
 with Session() as s:
  assert s.get(Account,'youtube') is None
  assert 'preserved-token' not in s.get(PendingConnection,'youtube').token
 enabled=True
 with TestClient(app) as api_client:
  assert api_client.get('/api/settings').json()['accounts']['youtube']['authorization_pending']
  r=api_client.post('/api/accounts/youtube/finish');assert r.status_code==200 and r.json()['identity']['id']=='my-channel'
 with Session() as s:assert s.get(PendingConnection,'youtube') is None
