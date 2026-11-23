import os,tempfile,httpx
from pathlib import Path
from .media import command,FFMPEG

def music_scan(source,duration):
 """Sample-based recognition, NOT a rights clearance or exhaustive Content ID scan."""
 token=os.getenv('AUDD_API_TOKEN')
 if not token:raise ValueError('Configure AUDD_API_TOKEN to enable music recognition')
 matches=[];samples=[]
 with tempfile.TemporaryDirectory() as tmp:
  for offset in sorted(set(round(max(0,duration-12)*f,2) for f in [0,.25,.5,.75,1])):
   sample=Path(tmp)/'sample.mp3'
   command([FFMPEG,'-y','-v','error','-ss',str(offset),'-i',str(source),'-t','12','-vn','-ac','1','-ar','22050','-c:a','libmp3lame',str(sample)],90)
   with sample.open('rb') as file:
    r=httpx.post('https://api.audd.io/',data={'api_token':token},files={'file':('sample.mp3',file,'audio/mpeg')},timeout=60)
   r.raise_for_status();j=r.json()
   if j.get('status')!='success':raise ValueError('Music recognition provider rejected the request. Check credentials and quota.')
   samples.append(offset)
   if j.get('result'):
    m=j['result'];matches.append({'source_seconds':offset,'artist':m.get('artist'),'title':m.get('title'),'link':m.get('song_link')})
 return {'music':f'Potential copyrighted music: {len(matches)} matching samples. Review licenses.' if matches else 'No matches in sampled excerpts. Copyright status is still unverified.','music_matches':matches,'music_samples':samples,'music_scope':'Five 12-second samples at most. Recognition does not establish ownership, permission or monetization eligibility.'}
