import os, json, datetime
from pathlib import Path
from sqlalchemy import create_engine, String, Text, Float, Integer, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.getenv('CREATOR_DATA_DIR', str(ROOT/'data'))).resolve(); DATA.mkdir(parents=True,exist_ok=True)
url=os.getenv('DATABASE_URL') or f'sqlite:///{DATA / "creator.db"}'
engine=create_engine(url,connect_args={'check_same_thread':False,'timeout':30} if url.startswith('sqlite') else {})
Session=sessionmaker(engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
class Project(Base):
 __tablename__='projects'
 id:Mapped[str]=mapped_column(String,primary_key=True)
 name:Mapped[str]=mapped_column(String)
 category:Mapped[str]=mapped_column(String)
 source:Mapped[str]=mapped_column(String)
 duration:Mapped[float]=mapped_column(Float)
 width:Mapped[int]=mapped_column(Integer)
 height:Mapped[int]=mapped_column(Integer)
 fingerprint:Mapped[str]=mapped_column(String)
 created:Mapped[str]=mapped_column(String)
 transcript:Mapped[str]=mapped_column(Text,default='[]')
 safety:Mapped[str]=mapped_column(Text,default='{}')
class Clip(Base):
 __tablename__='clips'
 id:Mapped[str]=mapped_column(String,primary_key=True)
 project_id:Mapped[str]=mapped_column(ForeignKey('projects.id'))
 part:Mapped[int]=mapped_column(Integer)
 start:Mapped[float]=mapped_column(Float)
 end:Mapped[float]=mapped_column(Float)
 status:Mapped[str]=mapped_column(String,default='draft')
 title:Mapped[str]=mapped_column(String)
 metadata_json:Mapped[str]=mapped_column(Text,default='{}')
 captions:Mapped[str]=mapped_column(Text,default='[]')
 output:Mapped[str]=mapped_column(String,default='')
 error:Mapped[str]=mapped_column(Text,default='')
class Job(Base):
 __tablename__='jobs'
 id:Mapped[str]=mapped_column(String,primary_key=True)
 project_id:Mapped[str]=mapped_column(ForeignKey('projects.id'))
 kind:Mapped[str]=mapped_column(String)
 status:Mapped[str]=mapped_column(String,default='queued')
 progress:Mapped[int]=mapped_column(Integer,default=0)
 message:Mapped[str]=mapped_column(Text,default='Waiting for worker')
 payload:Mapped[str]=mapped_column(Text,default='{}')
 created:Mapped[str]=mapped_column(String)
class Setting(Base):
 __tablename__='settings'
 key:Mapped[str]=mapped_column(String,primary_key=True)
 value:Mapped[str]=mapped_column(Text)
class Draft(Base):
 __tablename__='drafts'
 id:Mapped[str]=mapped_column(String,primary_key=True)
 clip_id:Mapped[str]=mapped_column(ForeignKey('clips.id'))
 platform:Mapped[str]=mapped_column(String)
 status:Mapped[str]=mapped_column(String,default='prepared')
 scheduled_at:Mapped[str]=mapped_column(String,default='')
 payload:Mapped[str]=mapped_column(Text)
class Account(Base):
 __tablename__='accounts'
 platform:Mapped[str]=mapped_column(String,primary_key=True)
 token:Mapped[str]=mapped_column(Text)
 details:Mapped[str]=mapped_column(Text,default='{}')
def init(): Base.metadata.create_all(engine)
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def as_dict(row): return {c.name:getattr(row,c.name) for c in row.__table__.columns}

class Publication(Base):
 __tablename__='publications'
 id:Mapped[str]=mapped_column(String,primary_key=True)
 batch_id:Mapped[str]=mapped_column(String,index=True)
 clip_id:Mapped[str]=mapped_column(ForeignKey('clips.id'))
 platform:Mapped[str]=mapped_column(String,index=True)
 account_id:Mapped[str]=mapped_column(String)
 dedupe_key:Mapped[str]=mapped_column(String,unique=True)
 asset_path:Mapped[str]=mapped_column(String)
 payload:Mapped[str]=mapped_column(Text)
 state:Mapped[str]=mapped_column(String,default='queued',index=True)
 due:Mapped[float]=mapped_column(Float,index=True)
 attempts:Mapped[int]=mapped_column(Integer,default=0)
 progress:Mapped[int]=mapped_column(Integer,default=0)
 remote_id:Mapped[str]=mapped_column(String,default='')
 published_url:Mapped[str]=mapped_column(String,default='')
 session_secret:Mapped[str]=mapped_column(Text,default='')
 offset:Mapped[int]=mapped_column(Integer,default=0)
 upload_attempted:Mapped[int]=mapped_column(Integer,default=0)
 started_at:Mapped[float]=mapped_column(Float,default=0)
 next_check:Mapped[float]=mapped_column(Float,default=0)
 error:Mapped[str]=mapped_column(Text,default='')
 created:Mapped[str]=mapped_column(String)
 updated:Mapped[str]=mapped_column(String)
class PublishEvent(Base):
 __tablename__='publish_events'
 id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
 publication_id:Mapped[str]=mapped_column(ForeignKey('publications.id'),index=True)
 state:Mapped[str]=mapped_column(String)
 message:Mapped[str]=mapped_column(Text)
 created:Mapped[str]=mapped_column(String)

class OAuthAttempt(Base):
 __tablename__='oauth_attempts'
 id:Mapped[str]=mapped_column(String,primary_key=True)
 platform:Mapped[str]=mapped_column(String)
 expires:Mapped[float]=mapped_column(Float,index=True)
 client_id:Mapped[str]=mapped_column(String)
 redirect_uri:Mapped[str]=mapped_column(String)

class PendingConnection(Base):
 __tablename__='pending_connections'
 platform:Mapped[str]=mapped_column(String,primary_key=True)
 token:Mapped[str]=mapped_column(Text)
 client_id:Mapped[str]=mapped_column(String)
 created:Mapped[str]=mapped_column(String)
