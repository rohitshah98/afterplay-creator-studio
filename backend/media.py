import os, json, subprocess, shutil, math, re, tempfile
from pathlib import Path
FFMPEG=os.getenv('FFMPEG_PATH') or shutil.which('ffmpeg') or '/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg'
FFPROBE=os.getenv('FFPROBE_PATH') or shutil.which('ffprobe') or '/opt/homebrew/opt/ffmpeg-full/bin/ffprobe'
def command(args,timeout=7200):
 r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
 if r.returncode: raise RuntimeError(r.stderr[-2500:] or 'Media command failed')
 return r.stdout
def probe(path):
 j=json.loads(command([FFPROBE,'-v','error','-show_format','-show_streams','-of','json',str(path)],60))
 v=next((s for s in j['streams'] if s['codec_type']=='video'),None)
 if not v: raise ValueError('This file does not contain a video stream')
 duration=float(j['format'].get('duration',v.get('duration',0)))
 if not math.isfinite(duration) or duration<=0 or duration>21600: raise ValueError('Video must be between 1 second and 6 hours')
 return dict(duration=duration,width=v['width'],height=v['height'],has_audio=any(s['codec_type']=='audio' for s in j['streams']))
def chronological(duration,minimum,maximum,target):
 if duration<minimum: raise ValueError(f'Source is shorter than {minimum} seconds')
 lower,upper=math.ceil(duration/maximum),math.floor(duration/minimum)
 if lower>upper:raise ValueError('This source cannot be fully divided within the selected duration range. Choose the 15–60 second range or trim the source.')
 n=max(lower,min(round(duration/target) or 1,upper))
 if n>100: raise ValueError('More than 100 parts requested. Choose a longer duration or upload a shorter source.')
 size=duration/n
 return [(round(i*size,3),round(min(duration,(i+1)*size),3)) for i in range(n)]
def transcribe(path,cache_dir,progress):
 from faster_whisper import WhisperModel
 progress('Loading local speech model (first run downloads the model)')
 m=WhisperModel(os.getenv('WHISPER_MODEL','base'),device='cpu',compute_type='int8',download_root=str(cache_dir))
 segments,_=m.transcribe(str(path),word_timestamps=True,vad_filter=True)
 out=[]
 for s in segments:
  out.extend(dict(start=round(w.start,3),end=round(w.end,3),text=w.word.strip()) for w in (s.words or []) if w.word.strip())
 return out
def parse_srt(text):
 out=[]
 for block in re.split(r'\n\s*\n',text.replace('\r','').strip()):
  m=re.search(r'(\d\d):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d\d):(\d\d):(\d\d)[,.](\d{3})\n(.+)',block,re.S)
  if not m: continue
  g=m.groups();sec=lambda v:int(v[0])*3600+int(v[1])*60+int(v[2])+int(v[3])/1000
  a,b=sec(g[:4]),sec(g[4:8]);words=re.sub('<[^>]+>','',g[8]).split()
  if b<=a or not words: continue
  for i,w in enumerate(words):out.append(dict(start=a+(b-a)*i/len(words),end=a+(b-a)*(i+1)/len(words),text=w))
 if not out: raise ValueError('No valid SRT cues found')
 return out
def ass_time(s):
 s=max(0,s);return f'{int(s//3600)}:{int(s%3600//60):02}:{s%60:05.2f}'
def safe_text(s): return str(s).replace('\\','').replace('{','').replace('}','').replace('\n',' ').replace('\r',' ')[:500]
def write_ass(path,words,start,end,part,animated=True,label=True):
 header='''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,72,&H00FFFFFF,&H0000E7FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,75,75,380,1
Style: Part,Arial,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,16,0,8,70,70,120,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
 lines=[header]
 if label:lines.append(f'Dialogue: 0,0:00:00.00,{ass_time(end-start)},Part,,0,0,0,,PART {part}\n')
 for i in range(0,len(words),4):
  group=words[i:i+4];a=max(start,group[0]['start']);b=min(end,group[-1]['end'])
  if b<=a:continue
  if animated:
   text=' '.join('{\\kf'+str(max(1,round((min(end,w['end'])-max(start,w['start']))*100)))+'}'+safe_text(w['text']) for w in group)
  else:text=' '.join(safe_text(w['text']) for w in group)
  lines.append(f'Dialogue: 1,{ass_time(a-start)},{ass_time(b-start)},Caption,,0,0,0,,{text}\n')
 path.write_text(''.join(lines))
def srt(words,start,end):
 lines=[]
 def ts(s): return ass_time(s).replace('.',',').rjust(11,'0')+'0'
 for i in range(0,len(words),4):
  g=words[i:i+4]
  lines.append(f'{i//4+1}\n{ts(max(0,g[0]["start"]-start))} --> {ts(min(end,g[-1]["end"])-start)}\n'+ ' '.join(x['text'] for x in g))
 return '\n\n'.join(lines)+'\n'
def render(source,out,start,end,words,part,options):
 out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
 width,height=int(options.get('width',1080)),int(options.get('height',1920))
 if (width,height)!=(1080,1920):raise ValueError('Export must be 1080 × 1920')
 selected=[w for w in words if w['end']>start and w['start']<end]
 ass=out.with_suffix('.ass');write_ass(ass,selected,start,end,part,options.get('animated',True),options.get('part_label',True))
 if options.get('framing','fit')=='crop': vf='scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1'
 else: vf='split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[blur];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[front];[blur][front]overlay=(W-w)/2:(H-h)/2,setsar=1'
 # Relative filenames avoid filter escaping and filesystem path injection.
 vf+=',ass='+ass.name
 tmp=out.with_name(out.stem+'.partial.mp4')
 args=[FFMPEG,'-y','-v','error','-ss',str(start),'-i',str(source),'-t',str(end-start),'-vf',vf,'-map','0:v:0','-map','0:a:0?','-c:v','libx264','-preset','veryfast','-crf','21','-r','30','-threads','2','-c:a','aac','-b:a','192k','-af','loudnorm=I=-14:TP=-1.5:LRA=11','-movflags','+faststart',tmp.name]
 r=subprocess.run(args,cwd=out.parent,capture_output=True,text=True,timeout=7200)
 if r.returncode:tmp.unlink(missing_ok=True);raise RuntimeError(r.stderr[-2500:])
 tmp.replace(out)
 command([FFMPEG,'-y','-v','error','-ss',str(min(1,(end-start)/2)),'-i',str(out),'-frames:v','1',str(out.with_suffix('.jpg'))],60)
 out.with_suffix('.srt').write_text(srt(selected,start,end))
 actual=probe(out)
 if actual['width']!=1080 or actual['height']!=1920:raise RuntimeError('Output failed resolution check')
 return dict(duration=actual['duration'],captions=len(selected),resolution='1080×1920',status='PASS' if selected else 'WARN',warnings=[] if selected else ['No speech captions: attach a transcript or enable transcription.'])
def audio_peaks(path,duration,minimum,maximum,target,count):
 # Offline fallback scores audio energy and speech punctuation; never presented as AI.
 import numpy as np
 r=subprocess.run([FFMPEG,'-v','error','-i',str(path),'-vn','-ac','1','-ar','8000','-f','f32le','-'],capture_output=True,timeout=1800)
 if r.returncode:raise RuntimeError('Could not analyze audio')
 a=np.frombuffer(r.stdout,dtype=np.float32); candidates=[]
 for t in range(0,max(1,int(duration-target)+1),max(5,int(target/3))):
  win=a[t*8000:int(min(duration,t+target))*8000]
  candidates.append((float(np.sqrt(np.mean(win**2))) if len(win) else 0,t,min(duration,t+target)))
 chosen=[]
 for _,s,e in sorted(candidates,reverse=True):
  if e-s>=minimum and all(min(e,b)<=max(s,a) for a,b in chosen):chosen.append((s,e))
  if len(chosen)>=count:break
 return sorted(chosen)
