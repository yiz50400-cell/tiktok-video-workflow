"""Check rendered media, asset binding and timing for packaging layers."""
from array import array
import hashlib
from pathlib import Path
import subprocess
import imageio_ffmpeg
import pytest
from scripts.produce_video import render_plan, validate_render_input


def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def run(args): return subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error',*args],check=True,capture_output=True).stdout

@pytest.fixture
def plan(tmp_path):
    source=tmp_path/'source.mp4'; voice=tmp_path/'voice.wav'; overlay=tmp_path/'overlay.ppm'; sfx=tmp_path/'sfx.wav'
    run(['-f','lavfi','-i','color=black:size=180x320:rate=60','-t','1','-c:v','libx264','-preset','ultrafast',str(source)])
    run(['-f','lavfi','-i','anullsrc=r=48000:cl=mono','-t','1',str(voice)])
    overlay.write_bytes(b'P6\n20 20\n255\n'+bytes([255,0,0])*400)
    run(['-f','lavfi','-i','sine=frequency=1200:sample_rate=48000','-t','0.1',str(sfx)])
    return {'source':str(source),'source_sha256':digest(source),'voice_audio':str(voice),'voice_audio_sha256':digest(voice),
            'windows':[{'source_start':0,'source_end':1,'timeline_start':0,'timeline_end':1}],
            'voice_windows':[[0,1]],'audio_cues':[{'source_start':0,'source_end':1,'delay_ms':0,'measured_start':0,'measured_end':1,'text':'Test'}],
            'duration':1,'encoder':'libx264','output':str(tmp_path/'out.mp4'),'srt':str(tmp_path/'out.srt'),
            'overlays':[{'path':str(overlay),'sha256':digest(overlay),'start':.2,'end':.6,'x':10,'y':10}],
            'sound_effects':[{'path':str(sfx),'sha256':digest(sfx),'start':.4,'duration':.1,'volume':.25}]}


def test_rendered_overlay_has_timed_pixels_and_sfx_has_timed_audio(plan):
    render_plan(plan)
    def pixels(t): return run(['-ss',str(t),'-i',plan['output'],'-frames:v','1','-vf','crop=20:20:10:10','-f','rawvideo','-pix_fmt','rgb24','-'])
    early,active,late=[pixels(t) for t in (.1,.35,.8)]
    assert sum(early)<1000 and sum(late)<1000
    assert sum(active[0::3])/400>220 and sum(active[1::3])/400<20
    samples=array('f'); samples.frombytes(run(['-i',plan['output'],'-vn','-ar','48000','-ac','1','-f','f32le','-']))
    assert max(abs(x) for x in samples[21000:23000])>.01
    assert max(abs(x) for x in samples[:12000])<.001
    assert len(samples)/48000<1.03

@pytest.mark.parametrize('kind',['overlays','sound_effects'])
def test_packaging_rejects_unbound_asset_bytes(plan,kind):
    Path(plan[kind][0]['path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'):
        validate_render_input(plan)

@pytest.mark.parametrize('kind',['overlays','sound_effects'])
def test_packaging_rejects_outside_video(plan,kind):
    plan[kind][0]['start']=1.1
    with pytest.raises(ValueError,match='packaging'):
        validate_render_input(plan)


def test_packaging_input_cannot_be_output(plan):
    plan['output']=plan['overlays'][0]['path']
    with pytest.raises(ValueError,match='replace'):
        validate_render_input(plan)
