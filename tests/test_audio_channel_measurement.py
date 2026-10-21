"""Actual-channel checks must not mistake equal-power downmix gain for clipping."""
import math,struct,wave
from pathlib import Path
import pytest
from workflow.production.qa import measure_audio

@pytest.mark.parametrize('channels',[1,2])
def test_actual_pcm_peak_and_duration_do_not_depend_on_duplicate_channels(tmp_path,channels):
 path=tmp_path/'voice.wav';rate=48000
 values=[round(.75*32767*math.sin(2*math.pi*1000*i/rate)) for i in range(rate)]
 pcm=b''.join(struct.pack('<h',v)*channels for v in values)
 with wave.open(str(path),'wb') as w:
  w.setnchannels(channels);w.setsampwidth(2);w.setframerate(rate);w.writeframes(pcm)
 got=measure_audio(path,tmp_path/'qa.log')
 assert got['sample_peak_dbfs']==pytest.approx(20*math.log10(.75),abs=.03)
 assert got['clipped_pcm_samples']==0
 assert got['decoded_duration_seconds']==pytest.approx(1,abs=.001)
