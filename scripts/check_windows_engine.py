"""Real local engine checks. A Mac result cannot pass the Windows platform gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workflow.orchestration.process import run
from workflow.platforms.files import atomic_json
from workflow.platforms.locks import acquire


def _font(explicit):
    if explicit:return Path(explicit).resolve()
    candidates=[Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/arial.ttf',
                Path('/System/Library/Fonts/Supplemental/Arial.ttf'),
                Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    for candidate in candidates:
        if candidate.is_file():return candidate
    raise ValueError('未找到诊断字体，请传入 --font 的绝对文件路径')


def check(output, *, font=None):
    started=time.perf_counter()
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    report={'platform':platform.system(),'machine':platform.machine(),
            'python_version':platform.python_version(),'status':'failed',
            'windows_platform_executed':os.name=='nt','checks':{}}
    if sys.version_info<(3,12):
        report['error']='requires_python_3_12_or_newer'
        atomic_json(output/'engine.json',report)
        return report
    try:
        import imageio_ffmpeg
        from scripts.produce_video import render_plan
        from workflow.production.preparation import _overlays
        digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory(prefix='员工 中文 空格-',dir=output) as temporary:
            work=Path(temporary)
            lock=work/'task.lock'
            contender="import sys; sys.path.insert(0,%r); from workflow.platforms.locks import acquire; f=open(sys.argv[1],'a+b'); acquire(f,blocking=False)" % str(ROOT)
            with lock.open('a+b') as stream:
                acquire(stream,blocking=False)
                denied=run([sys.executable,'-c',contender,str(lock)],cwd=work,timeout=10)
                if denied.returncode==0 or 'BlockingIOError' not in denied.stderr:
                    raise RuntimeError('cross_process_lock_failed')
            accepted=run([sys.executable,'-c',contender,str(lock)],cwd=work,timeout=10)
            if accepted.returncode:raise RuntimeError('lock_release_failed')
            report['checks']['cross_process_lock']='passed'
            utf8=run([sys.executable,'-c',"import sys; print(sys.stdin.read())"],
                     cwd=work,timeout=10,input='中文 配音')
            if utf8.returncode or utf8.stdout.strip()!='中文 配音':raise RuntimeError('utf8_failed')
            report['checks']['utf8_process']='passed'
            ready=work/'child-started';orphan=work/'orphan'
            child="from pathlib import Path; import time; Path(%r).write_text('ready'); time.sleep(4); Path(%r).write_text('orphan')" % (str(ready),str(orphan))
            parent="import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); time.sleep(10)" % child
            try:run([sys.executable,'-c',parent],cwd=work,timeout=2)
            except subprocess.TimeoutExpired:pass
            else:raise RuntimeError('timeout_not_enforced')
            time.sleep(3)
            if not ready.exists() or orphan.exists():raise RuntimeError('process_tree_cleanup_failed')
            report['checks']['process_tree_timeout']='passed'
            source=work/'中文 原片.mp4';voice=work/'配音.wav'
            for arguments in [
                ['-f','lavfi','-i','testsrc2=size=180x320:rate=60','-t','1','-c:v','libx264','-preset','ultrafast',str(source)],
                ['-f','lavfi','-i','anullsrc=r=48000:cl=mono','-t','1',str(voice)],
            ]:
                result=run([ffmpeg,'-hide_banner','-v','error','-nostdin',*arguments],cwd=work,timeout=30)
                if result.returncode:raise RuntimeError('media_fixture_failed')
            font_path=_font(font)
            style={'line':1,'font':str(font_path),'font_sha256':digest(font_path),
                   'font_size':20,'x':8,'y':220}
            overlays=_overlays(work,{'packaging':[style],'width':180,'height':320},
                               {'segments':[{'visible_text':'Teste BR','window':[0,1]}]})
            plan={'source':str(source),'source_sha256':digest(source),
                  'voice_audio':str(voice),'voice_audio_sha256':digest(voice),
                  'windows':[{'source_start':0,'source_end':1,'timeline_start':0,'timeline_end':1}],
                  'voice_windows':[[0,1]],'duration':1,
                  'audio_cues':[{'source_start':0,'source_end':1,'delay_ms':0,'measured_start':0,'measured_end':1,'text':'Teste'}],
                  'subtitle_cues':[[0,1,'中文测试字幕']],'overlays':overlays,
                  'output':str(work/'测试 成片.mp4'),'srt':str(work/'中文 字幕.srt')}
            render_started=time.perf_counter();result=render_plan(plan)
            report['synthetic_render_seconds']=round(time.perf_counter()-render_started,3)
            decoded=run([ffmpeg,'-v','error','-nostdin','-i',result['output'],'-f','null','-'],cwd=work,timeout=30)
            if decoded.returncode:raise RuntimeError('render_decode_failed')
            if '中文测试字幕' not in Path(result['srt']).read_text(encoding='utf-8'):
                raise RuntimeError('subtitle_utf8_failed')
            report['checks']['font_hash_packaging_libx264_encode_decode']='passed'
            report['checks']['chinese_space_paths_and_subtitles']='passed'
            report['diagnostic_font_sha256']=digest(font_path)
        report['status']='host_checks_passed'
    except Exception as error:
        report['error_type']=type(error).__name__
        # This diagnostic never reads keys. Internal errors remain local.
        report['error']=str(error)[:1000]
    report['elapsed_seconds']=round(time.perf_counter()-started,3)
    atomic_json(output/'engine.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'.runtime/windows-engine-check')
    parser.add_argument('--font',type=Path)
    parser.add_argument('--require-windows',action='store_true')
    args=parser.parse_args(argv)
    report=check(args.output,font=args.font)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if report['status']!='host_checks_passed':return 1
    if args.require_windows and not report['windows_platform_executed']:return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
