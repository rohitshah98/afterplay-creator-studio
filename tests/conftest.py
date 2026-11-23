"""Configure isolated storage and the caption-capable FFmpeg before test collection."""
import os,tempfile
from pathlib import Path
TEST_STORAGE=tempfile.TemporaryDirectory(prefix='afterplay-suite-')
os.environ['CREATOR_DATA_DIR']=TEST_STORAGE.name
os.environ.pop('DATABASE_URL',None)
os.environ['CREATOR_SCHEDULER_ENABLED']='false'
for name,exe in [('FFMPEG_PATH','ffmpeg'),('FFPROBE_PATH','ffprobe')]:
 path=Path('/opt/homebrew/opt/ffmpeg-full/bin')/exe
 if path.exists():os.environ[name]=str(path)

import pytest
@pytest.fixture(autouse=True)
def no_account_leak_between_tests():
 from backend.db import init,Session,Account,Setting
 init()
 with Session.begin() as s:
  s.query(Account).delete()
  s.query(Setting).filter(Setting.key.in_(['oauth_client_youtube','oauth_client_tiktok'])).delete()
