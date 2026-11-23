"""Official OAuth, encrypted client credentials, token refresh and analytics."""
import os,secrets,time,json,urllib.parse,httpx,threading,hashlib
from sqlalchemy import delete
from pathlib import Path
from cryptography.fernet import Fernet
from .db import Session,Account,Setting,DATA,OAuthAttempt,PendingConnection,now
TOKEN_LOCK=threading.RLock()
def ensure_key():
 if os.getenv('TOKEN_ENCRYPTION_KEY'):return
 path=DATA/'oauth.key'
 if not path.exists():
  try:
   fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
  except FileExistsError:pass
 os.environ['TOKEN_ENCRYPTION_KEY']=path.read_text().strip()
def credentials(p):
 if p not in CONFIG:raise ValueError('Unknown platform')
 client_id=os.getenv(p.upper()+'_CLIENT_ID','');secret=os.getenv(p.upper()+'_CLIENT_SECRET','')
 if client_id and secret:return client_id,secret
 with Session() as s:row=s.get(Setting,'oauth_client_'+p)
 if row:
  j=json.loads(cipher().decrypt(row.value.encode()));return j['client_id'],j['client_secret']
 return '',''
def save_credentials(p,client_id,client_secret,redirect_uri=""):
 if p not in CONFIG:raise ValueError('Unknown platform')
 ensure_key()
 if redirect_uri:
  u=urllib.parse.urlparse(redirect_uri)
  if u.query or u.fragment or u.username or not (u.scheme=='https' and u.hostname or u.scheme=='http' and u.hostname in ['localhost','127.0.0.1']):raise ValueError('Use an exact registered HTTPS callback or supported localhost HTTP callback, without query or fragment')
 value=cipher().encrypt(json.dumps(dict(client_id=client_id,client_secret=client_secret,redirect_uri=redirect_uri)).encode()).decode()
 with Session.begin() as s:
  row=s.get(Setting,'oauth_client_'+p)
  if not row:row=Setting(key='oauth_client_'+p,value=value);s.add(row)
  else:row.value=value
CONFIG={
 'youtube':dict(auth='https://accounts.google.com/o/oauth2/v2/auth',token='https://oauth2.googleapis.com/token',scope='https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly https://www.googleapis.com/auth/yt-analytics-monetary.readonly https://www.googleapis.com/auth/youtube.upload'),
 'tiktok':dict(auth='https://www.tiktok.com/v2/auth/authorize/',token='https://open.tiktokapis.com/v2/oauth/token/',scope='user.info.basic,user.info.stats,video.list,video.publish')}
def configured(p):return p in CONFIG and all(credentials(p)) and bool(os.getenv('TOKEN_ENCRYPTION_KEY'))
def cipher():return Fernet(os.environ['TOKEN_ENCRYPTION_KEY'].encode())
def callback_url(p):
 if os.getenv(p.upper()+'_REDIRECT_URI'):return os.environ[p.upper()+'_REDIRECT_URI']
 with Session() as s:row=s.get(Setting,'oauth_client_'+p)
 if row:
  custom=json.loads(cipher().decrypt(row.value.encode())).get('redirect_uri')
  if custom:return custom
 return os.getenv('APP_URL','http://localhost:8787')+'/api/accounts/'+p+'/callback'
def authorize(p):
 if not configured(p):raise ValueError('Set '+p.upper()+'_CLIENT_ID, '+p.upper()+'_CLIENT_SECRET and TOKEN_ENCRYPTION_KEY in the server environment. Register the callback URL shown in Settings.')
 state=secrets.token_urlsafe(32);redirect=callback_url(p);client_id=credentials(p)[0]
 with Session.begin() as s:
  s.execute(delete(OAuthAttempt).where(OAuthAttempt.expires<=time.time()))
  s.add(OAuthAttempt(id=hashlib.sha256(state.encode()).hexdigest(),platform=p,expires=time.time()+600,client_id=client_id,redirect_uri=redirect))
 params=dict(response_type='code',scope=CONFIG[p]['scope'],redirect_uri=redirect,state=state)
 params['client_id' if p=='youtube' else 'client_key']=credentials(p)[0]
 if p=='youtube':params.update(access_type='offline',prompt='consent')
 return CONFIG[p]['auth']+'?'+urllib.parse.urlencode(params)
def exchange(p,code,state):
 if p not in CONFIG or not state or len(state)>512 or not code:raise ValueError('OAuth state expired or invalid. Start a fresh connection from Afterplay.')
 digest=hashlib.sha256(state.encode()).hexdigest();client_id=credentials(p)[0];redirect=callback_url(p)
 # Atomic consumption keeps state single-use across workers and restarts.
 with Session.begin() as s:
  valid=s.execute(delete(OAuthAttempt).where(OAuthAttempt.id==digest,OAuthAttempt.platform==p,OAuthAttempt.expires>time.time(),OAuthAttempt.client_id==client_id,OAuthAttempt.redirect_uri==redirect).returning(OAuthAttempt.id)).first()
 if not valid:raise ValueError('OAuth state expired or invalid. This sign-in request expired, was already used, or its app configuration changed. Return to Afterplay and click Connect again.')
 data=dict(code=code,grant_type='authorization_code',redirect_uri=callback_url(p),client_secret=credentials(p)[1])
 data['client_id' if p=='youtube' else 'client_key']=credentials(p)[0]
 r=httpx.post(CONFIG[p]['token'],data=data,timeout=30);r.raise_for_status();tokens=r.json()
 if 'access_token' not in tokens:raise ValueError('Platform rejected account connection')
 tokens['expires_at']=time.time()+tokens.get('expires_in',3600)
 # Save the token receipt encrypted before downstream channel lookup can fail.
 with Session.begin() as s:
  pending=s.get(PendingConnection,p)
  if not pending:pending=PendingConnection(platform=p,token='',client_id=client_id,created=now());s.add(pending)
  pending.token=cipher().encrypt(json.dumps(tokens).encode()).decode();pending.client_id=client_id;pending.created=now()
 complete_pending(p)
def complete_pending(p):
 with TOKEN_LOCK:
  with Session() as s:pending=s.get(PendingConnection,p)
  if not pending:raise ValueError('No pending authorization. Click Connect to begin a fresh sign-in.')
  if pending.client_id!=credentials(p)[0]:raise ValueError('Developer app changed. Start a fresh connection.')
  tokens=json.loads(cipher().decrypt(pending.token.encode()))
  if tokens.get('expires_at',0)<time.time()+120:
   if not tokens.get('refresh_token'):raise ValueError('Pending authorization expired. Start a fresh connection.')
   data={'grant_type':'refresh_token','refresh_token':tokens['refresh_token'],'client_secret':credentials(p)[1]}
   data['client_id' if p=='youtube' else 'client_key']=credentials(p)[0]
   r=httpx.post(CONFIG[p]['token'],data=data,timeout=30);r.raise_for_status();new=r.json()
   if not new.get('access_token'):raise ValueError('Pending authorization could not be refreshed. Reconnect.')
   tokens.update(new);tokens['expires_at']=time.time()+new.get('expires_in',3600)
   with Session.begin() as s:s.get(PendingConnection,p).token=cipher().encrypt(json.dumps(tokens).encode()).decode()
  details=identity(p,tokens['access_token'])
  with Session.begin() as s:
   a=s.get(Account,p)
   if not a:a=Account(platform=p,token='');s.add(a)
   if a.token and not tokens.get('refresh_token') and json.loads(a.details).get('id')==details.get('id'):
    tokens['refresh_token']=json.loads(cipher().decrypt(a.token.encode())).get('refresh_token','')
   a.token=cipher().encrypt(json.dumps(tokens).encode()).decode();a.details=json.dumps(details)
   s.delete(s.get(PendingConnection,p))
  return details

def access(p):
 with TOKEN_LOCK:return _access(p)
def _access(p):
 with Session() as s:a=s.get(Account,p)
 if not a:raise ValueError('Connect your '+p+' account first')
 t=json.loads(cipher().decrypt(a.token.encode()))
 if t.get('expires_at',0)<time.time()+120:
  if not t.get('refresh_token'):raise ValueError('Account authorization expired. Reconnect.')
  data=dict(grant_type='refresh_token',refresh_token=t['refresh_token'],client_secret=credentials(p)[1])
  data['client_id' if p=='youtube' else 'client_key']=credentials(p)[0]
  r=httpx.post(CONFIG[p]['token'],data=data,timeout=30);r.raise_for_status();new=r.json()
  if not new.get('access_token'):raise ValueError('Account refresh failed. Reconnect.')
  t.update(new);t['expires_at']=time.time()+new.get('expires_in',3600)
  with Session.begin() as s:s.get(Account,p).token=cipher().encrypt(json.dumps(t).encode()).decode()
 return t['access_token']
def identity(p,token):
 h={'Authorization':'Bearer '+token}
 if p=='youtube':
  r=httpx.get('https://www.googleapis.com/youtube/v3/channels',params={'part':'id,snippet','mine':'true'},headers=h,timeout=30)
  if r.is_error:
   try:error=r.json().get('error',{});reasons=[x.get('reason') for x in error.get('errors',[])]+[x.get('reason') for x in error.get('details',[])]
   except Exception:reasons=[]
   if 'accessNotConfigured' in reasons or 'SERVICE_DISABLED' in reasons:raise ValueError('Google authorization received, but YouTube Data API v3 is disabled for this OAuth project. Enable it in Google Cloud, then click Finish connection in Afterplay. Your authorization is saved encrypted.')
   if r.status_code==403:raise ValueError('Google authorization received, but channel access was denied. Check YouTube Data API activation and granted permissions, then click Finish connection. Your authorization is saved encrypted.')
   raise ValueError(f'Google channel lookup returned HTTP {r.status_code}. Authorization is saved encrypted; retry Finish connection.')
  items=r.json().get('items',[])
  if not items:raise ValueError('This Google account does not have a YouTube channel')
  return {'id':items[0]['id'],'name':items[0]['snippet']['title']}
 r=httpx.get('https://open.tiktokapis.com/v2/user/info/',params={'fields':'open_id,display_name'},headers=h,timeout=30);r.raise_for_status();u=r.json().get('data',{}).get('user',{})
 if not u.get('open_id'):raise ValueError('TikTok did not return an account identity')
 return {'id':u['open_id'],'name':u.get('display_name','TikTok creator')}
def account_details(p):
 with Session() as s:a=s.get(Account,p)
 return json.loads(a.details) if a else None
def analytics(p):
 import datetime
 h={'Authorization':'Bearer '+access(p)}
 if p=='youtube':
  r=httpx.get('https://www.googleapis.com/youtube/v3/channels',params={'part':'statistics,snippet','mine':'true'},headers=h,timeout=30);r.raise_for_status();items=r.json().get('items',[])
  if not items:raise ValueError('No YouTube channel on this account')
  c=items[0];st=c['statistics'];today=datetime.date.today();start=today-datetime.timedelta(days=28)
  base={'ids':'channel==MINE','startDate':str(start),'endDate':str(today),'dimensions':'day','metrics':'views,estimatedMinutesWatched,averageViewPercentage,likes,comments,shares,subscribersGained','filters':'creatorContentType==SHORTS'}
  r=httpx.get('https://youtubeanalytics.googleapis.com/v2/reports',params=base,headers=h,timeout=30);r.raise_for_status();report=r.json()
  rows=[dict(zip([x['name'] for x in report.get('columnHeaders',[])],row)) for row in report.get('rows',[])]
  revenue=None
  rr=httpx.get('https://youtubeanalytics.googleapis.com/v2/reports',params={**base,'dimensions':'','metrics':'estimatedRevenue'},headers=h,timeout=30)
  if rr.is_success and rr.json().get('rows'):revenue=rr.json()['rows'][0][0]
  return dict(platform=p,source='YouTube official APIs',period='28 days',followers=int(st.get('subscriberCount',0)),views=sum(x.get('views',0) for x in rows),watch_minutes=sum(x.get('estimatedMinutesWatched',0) for x in rows),retention=(sum(x.get('averageViewPercentage',0)*x.get('views',0) for x in rows)/max(1,sum(x.get('views',0) for x in rows))),engagement=sum(x.get('likes',0)+x.get('comments',0)+x.get('shares',0) for x in rows)/max(1,sum(x.get('views',0) for x in rows))*100,revenue=revenue,series=rows,qualified_views=None,note='Analytics views are not verified YPP eligibility views. Revenue is null if the monetary report is unavailable.')
 r=httpx.get('https://open.tiktokapis.com/v2/user/info/',params={'fields':'display_name,follower_count,likes_count,video_count'},headers=h,timeout=30);r.raise_for_status();j=r.json()
 if j.get('error',{}).get('code') not in (None,'ok'):raise ValueError('TikTok rejected analytics access; verify approved scopes')
 u=j.get('data',{}).get('user',{})
 r=httpx.post('https://open.tiktokapis.com/v2/video/list/',params={'fields':'id,title,view_count,like_count,comment_count,share_count'},headers=h,json={'max_count':20},timeout=30);r.raise_for_status();j=r.json()
 if j.get('error',{}).get('code') not in (None,'ok'):raise ValueError('TikTok rejected video analytics access')
 vids=j.get('data',{}).get('videos',[]);views=sum(x.get('view_count',0) for x in vids)
 return dict(platform=p,source='TikTok Display API',period='Latest 20 videos, lifetime counts (not a 30-day total)',followers=u.get('follower_count'),views=views,watch_minutes=None,retention=None,revenue=None,qualified_views=None,engagement=sum(x.get('like_count',0)+x.get('comment_count',0)+x.get('share_count',0) for x in vids)/max(views,1)*100,series=vids,note='Watch time, retention and Creator Rewards revenue are unavailable through this Display API adapter. No estimated earnings are fabricated.')
