import os,tempfile,time,json,subprocess,pytest
from pathlib import Path
TEST_DIR=tempfile.TemporaryDirectory(prefix='afterplay-test-')
os.environ['CREATOR_DATA_DIR']=TEST_DIR.name
os.environ.pop('DATABASE_URL',None)
os.environ['CREATOR_SCHEDULER_ENABLED']='false'
os.environ['FFMPEG_PATH']='/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg'
os.environ['FFPROBE_PATH']='/opt/homebrew/opt/ffmpeg-full/bin/ffprobe'
from fastapi.testclient import TestClient
from backend.main import app
from backend.media import chronological,parse_srt,probe,FFMPEG

def wait(client,jid):
 for _ in range(400):
  job=next(j for j in client.get('/api/jobs').json() if j['id']==jid)
  if job['status'] in ['completed','failed']:
   assert job['status']=='completed',job['message'];return
  time.sleep(.2)
 raise AssertionError('Job did not finish within 80 seconds')
def test_split_boundaries():
 for duration in [15,31,61,125,601]:
  windows=chronological(duration,15,60,30)
  assert windows[0][0]==0 and windows[-1][1]==duration
  assert all(15-.01<=b-a<=60+.01 for a,b in windows)
  assert all(windows[i][1]==windows[i+1][0] for i in range(len(windows)-1))
 for duration in [60,180,240]:
  windows=chronological(duration,60,90,75)
  assert all(60-.01<=b-a<=90+.01 for a,b in windows)

 with pytest.raises(ValueError,match='cannot be fully divided'):chronological(91,60,90,75)

def test_complete_workflow():
 source=Path(TEST_DIR.name)/'test.mp4'
 subprocess.run([FFMPEG,'-y','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=12','-f','lavfi','-i','sine=frequency=440:sample_rate=16000','-t','31','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(source)],check=True)
 # Exercise Whisper's real decoder without downloading a speech model.
 # PyAV 19 removed an argument used here by faster-whisper 1.2.
 from faster_whisper.audio import decode_audio
 audio=decode_audio(str(source))
 assert audio.ndim==1 and 30*16000<=len(audio)<=32*16000
 with TestClient(app) as client:
  assert client.get('/api/health').json()['ok']
  with source.open('rb') as f:assert client.post('/api/projects',files={'file':('test.mp4',f)},data={'rights':'false'}).status_code==422
  with source.open('rb') as f:r=client.post('/api/projects',files={'file':('../../test.mp4',f)},data={'rights':'true','name':'Workflow test','category':'Gaming challenges'})
  assert r.status_code==200,r.text
  project=r.json();pid=project['id'];assert project['duration']>=31
  assert client.post('/api/projects/'+pid+'/transcript',files={'file':('bad.srt',b'not captions')}).status_code==422
  srt='1\n00:00:00,000 --> 00:00:15,000\nThis is an original gaming challenge with a complete story and a surprising ending.\n\n2\n00:00:15,500 --> 00:00:31,000\nHere is the second part with commentary and a new challenge to finish.'
  assert client.post('/api/projects/'+pid+'/transcript',files={'file':('words.srt',srt.encode())}).status_code==200
  assert client.post('/api/projects/'+pid+'/process',json={'minimum':60,'maximum':15}).status_code==422
  r=client.post('/api/projects/'+pid+'/process',json={'transcribe':False,'mode':'chronological','target':15.5,'render':True})
  assert r.status_code==200,r.text;wait(client,r.json()['job_id'])
  clips=client.get('/api/clips',params={'project_id':pid}).json();assert len(clips)==2
  for c in clips:
   assert c['status']=='ready',c
   assert c['metadata']['qc']['resolution']=='1080×1920'
   for asset in ['video','thumbnail','captions','metadata']:
    r=client.get('/api/clips/'+c['id']+'/'+asset);assert r.status_code==200 and len(r.content)>10
  c=clips[0]
  body={k:c[k] for k in ['start','end','title','captions','metadata']};body['title']='Edited original commentary'
  assert client.patch('/api/clips/'+c['id'],json={**body,'end':9999}).status_code==422
  assert client.patch('/api/clips/'+c['id'],json=body).status_code==200
  assert client.get('/api/clips/'+c['id']+'/video').status_code==404
  r=client.post('/api/clips/'+c['id']+'/render',json={'framing':'crop'});wait(client,r.json()['job_id'])
  r=client.post('/api/clips/'+c['id']+'/prepare',json={'platform':'youtube'});assert r.status_code==200 and r.json()['status']=='awaiting_integration'
  assert client.post('/api/drafts/'+r.json()['id']+'/publish').status_code==409
  assert client.post('/api/clips/'+c['id']+'/prepare',json={'platform':'tiktok','scheduled_at':'2099-01-01T00:00:00Z'}).status_code==422
  assert client.get('/api/analytics/youtube').status_code==409
  assert client.get('/api/accounts/youtube/connect').status_code==422
  assert client.get('/api/accounts/youtube/callback?code=x&state=wrong').status_code==422
  assert client.get('/api/health',headers={'host':'attacker.com'}).status_code==403
  assert client.post('/api/projects',headers={'origin':'https://attacker.com'}).status_code==403
  policies=client.get('/api/settings').json()['policies'];policies['youtube']['followers']=1234
  assert client.put('/api/settings/policies',json={'policies':policies}).status_code==200
  assert client.get('/api/settings').json()['policies']['youtube']['followers']==1234

def test_ai_window_validation(monkeypatch):
 from backend import ai
 from types import SimpleNamespace
 monkeypatch.setattr(ai,'ask',lambda *args:{'clips':[{'start':-1,'end':20},{'start':0,'end':20},{'start':10,'end':35},{'start':40,'end':200},{'start':25,'end':45}]})
 selected=ai.highlights(SimpleNamespace(duration=90),[{'start':0,'end':1,'text':'original commentary'}],{'minimum':15,'maximum':60,'count':10})
 assert selected==[(0.,20.),(25.,45.)]
 monkeypatch.setattr(ai,'ask',lambda *args:{'clips':[{'start':0,'end':2}]})
 with pytest.raises(ValueError,match='no valid'):ai.highlights(SimpleNamespace(duration=90),[{'start':0,'end':1,'text':'test'}],{'minimum':15,'maximum':60,'count':10})

def test_interrupted_job_recovery():
 from backend.db import Session,Project,Job,now,init
 init()
 from backend.worker import recover
 with Session.begin() as s:
  p=Project(id='recovery-project',name='Recovery test',category='Gaming challenges',source='unused-test-source',duration=30,width=320,height=180,fingerprint='recovery',created=now())
  s.add(p);s.flush()
  s.add(Job(id='interrupted-job',project_id=p.id,kind='process',created=now(),status='running'))
 recover()
 with Session() as s:
  j=s.get(Job,'interrupted-job');assert j.status=='failed' and 'Retry' in j.message
