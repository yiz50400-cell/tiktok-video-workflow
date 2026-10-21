import hashlib
import json
import math
from pathlib import Path
import subprocess

import imageio_ffmpeg
import pytest

from scripts.produce_video import build_srt, render_plan, validate_plan


WINDOWS = [
    (0.0, 3.1),
    (3.1, 4.85),
    (4.85, 9.783),
    (9.783, 14.746),
    (14.746, 17.246),
]


def test_build_srt_uses_measured_audio_boundaries():
    cues = [(0.182, 2.219, "第一句"), (3.179, 4.470, "第二句")]
    assert build_srt(cues) == (
        "1\n00:00:00,182 --> 00:00:02,219\n第一句\n\n"
        "2\n00:00:03,179 --> 00:00:04,470\n第二句\n"
    )


def test_validate_plan_rejects_audio_outside_approved_windows():
    measured = [(0.182, 2.219), (3.179, 4.470), (4.996, 8.888),
                (9.940, 13.553), (14.822, 17.103)]
    assert validate_plan(WINDOWS, measured) is True
    with pytest.raises(ValueError, match="window"):
        validate_plan(WINDOWS, measured[:-1] + [(14.822, 17.30)])


@pytest.mark.parametrize("start,end", [(math.nan, 1), (0, math.inf), (-1, 1)])
def test_invalid_measured_times_are_rejected(start, end):
    with pytest.raises(ValueError):
        validate_plan([(0, 2)], [(start, end)])


@pytest.fixture
def render_input(tmp_path):
    video = tmp_path / "source.mp4"
    voice = tmp_path / "voice.wav"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=180x320:rate=60", "-t", "1", "-an",
                    "-c:v", "libx264", "-preset", "ultrafast", str(video)], check=True)
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=400:sample_rate=24000", "-t", "1",
                    str(voice)], check=True)
    return {
        "source": str(video), "voice_audio": str(voice),
        "source_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "voice_audio_sha256": hashlib.sha256(voice.read_bytes()).hexdigest(),
        "windows": [
            {"source_start": 0, "source_end": .5, "timeline_start": 0, "timeline_end": .5},
            {"source_start": .5, "source_end": 1, "timeline_start": .5, "timeline_end": 1},
        ],
        "voice_windows": [[0, 1]],
        "audio_cues": [{"source_start": 0, "source_end": 1, "delay_ms": 0,
                        "measured_start": 0, "measured_end": 1, "text": "Test cue."}],
        "subtitle_cues": [[0, .5, "Test"], [.5, 1, "cue."]],
        "duration": 1, "encoder": "libx264",
        "output": str(tmp_path / "output.mp4"), "srt": str(tmp_path / "output.srt"),
    }


def test_render_accepts_distinct_shot_and_voice_windows_and_writes_short_cues(render_input):
    result = render_plan(render_input)
    assert Path(result["output"]).is_file()
    frames = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i",
        result["output"], "-vf", "scale=2:2,format=gray", "-an", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    assert len(frames) // 4 == 60
    assert Path(result["srt"]).read_text() == (
        "1\n00:00:00,000 --> 00:00:00,500\nTest\n\n"
        "2\n00:00:00,500 --> 00:00:01,000\ncue.\n"
    )
    decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i",
                              result["output"], "-f", "null", "-"], capture_output=True)
    assert decoded.returncode == 0, decoded.stderr.decode()


def test_default_render_works_in_a_chinese_space_directory(render_input, tmp_path, monkeypatch):
    real_run = subprocess.run
    def without_mac_hardware(command, *args, **kwargs):
        if 'h264_videotoolbox' in command:
            return subprocess.CompletedProcess(command, 1, b'', b'Mac hardware unavailable')
        return real_run(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', without_mac_hardware)
    directory = tmp_path / '员工 甲的素材'
    directory.mkdir()
    render_input.pop('encoder')
    render_input['output'] = str(directory / '商品 成片.mp4')
    render_input['srt'] = str(directory / '中文字幕.srt')
    render_input['subtitle_cues'] = [[0, 1, '真人配音与商品动作']]
    result = render_plan(render_input)
    decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-i',
                              result['output'], '-f', 'null', '-'], capture_output=True)
    assert decoded.returncode == 0
    assert '真人配音与商品动作' in Path(result['srt']).read_text(encoding='utf-8')


def test_render_finishes_when_final_audio_trim_reaches_eof(render_input, tmp_path, monkeypatch):
    """Mixing the EOF trim must terminate with exactly the planned audio samples."""
    video, voice = Path(render_input['source']), Path(render_input['voice_audio'])
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                    'color=size=180x320:rate=60', '-t', '16.3', '-an',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-y', str(video)], check=True)
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=400:sample_rate=48000', '-t', '13.512',
                    '-y', str(voice)], check=True)
    render_input.update(source_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
                        voice_audio_sha256=hashlib.sha256(voice.read_bytes()).hexdigest(),
                        duration=16.3,
                        windows=[{'source_start': 0, 'source_end': 16.3,
                                  'timeline_start': 0, 'timeline_end': 16.3}],
                        voice_windows=[[0, 2.6], [2.6, 5.6], [5.6, 8.6], [8.6, 12.6], [12.6, 16.3]],
                        subtitle_cues=[])
    trims = [(0, 2.064, 268), (2.064, 4.608, 2828), (4.608, 7.368, 5720),
             (7.368, 10.536, 9016), (10.536, 13.512, 12962)]
    render_input['audio_cues'] = [{'source_start': a, 'source_end': b, 'delay_ms': delay,
                                 'measured_start': delay / 1000,
                                 'measured_end': delay / 1000 + b - a, 'text': 'Test.'}
                                for a, b, delay in trims]
    real_run = subprocess.run
    def bounded_run(*args, **kwargs):
        kwargs.setdefault('timeout', 5)
        return real_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', bounded_run)
    result = render_plan(render_input)
    pcm = real_run([ffmpeg, '-v', 'error', '-i', result['output'], '-vn',
                    '-ar', '48000', '-ac', '1', '-f', 's16le', '-'], capture_output=True, check=True).stdout
    assert len(pcm) / 2 / 48000 == pytest.approx(16.3, abs=.03)


def test_render_accepts_per_shot_source_bindings(render_input, tmp_path):
    second = tmp_path / "second.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
                    "color=c=green:size=90x160:rate=30", "-t", "1", "-an",
                    "-c:v", "libx264", "-preset", "ultrafast", str(second)], check=True)
    render_input["sources"] = [
        {"path": render_input["source"], "sha256": render_input["source_sha256"]},
        {"path": str(second), "sha256": hashlib.sha256(second.read_bytes()).hexdigest()},
    ]
    del render_input["source"], render_input["source_sha256"]
    render_input["windows"][1]["source_index"] = 1
    render_input["width"], render_input["height"] = 180, 320
    result = render_plan(render_input)
    assert Path(result["output"]).is_file()
    frame = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-ss", "0.75",
        "-i", result["output"], "-frames:v", "1", "-vf", "scale=1:1,format=rgb24",
        "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    assert len(frame) == 3 and frame[1] > 100 and frame[0] < 10 and frame[2] < 10
    frames = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i",
        result["output"], "-vf", "scale=2:2,format=gray", "-an", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    assert len(frames) // 4 == 60


def test_render_checks_all_mixed_sources_and_protects_them(render_input, tmp_path):
    second = tmp_path / "second.mp4"
    second.write_bytes(Path(render_input["source"]).read_bytes())
    render_input["sources"] = [
        {"path": render_input["source"], "sha256": render_input["source_sha256"]},
        {"path": str(second), "sha256": "wrong"},
    ]
    del render_input["source"], render_input["source_sha256"]
    with pytest.raises(ValueError, match="hash"):
        render_plan(render_input)
    render_input["output"] = str(second)
    with pytest.raises(ValueError, match="input"):
        render_plan(render_input)


def test_render_rejects_modified_voice_before_creating_outputs(render_input):
    Path(render_input["voice_audio"]).write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash"):
        render_plan(render_input)
    assert not Path(render_input["output"]).exists()


def test_render_cannot_replace_a_source_file(render_input):
    render_input["output"] = render_input["source"]
    original = Path(render_input["source"]).read_bytes()
    with pytest.raises(ValueError, match="source|input"):
        render_plan(render_input)
    assert Path(render_input["source"]).read_bytes() == original


def test_render_rejects_speech_cropped_by_trim(render_input):
    render_input["audio_cues"][0]["source_end"] = .4
    with pytest.raises(ValueError, match="trim"):
        render_plan(render_input)


def test_render_rejects_changed_shot_duration(render_input):
    render_input["windows"][0]["source_end"] = .7
    with pytest.raises(ValueError, match="duration"):
        render_plan(render_input)


def test_subtitles_reject_overlap():
    with pytest.raises(ValueError):
        build_srt([(0, 1, "one"), (.5, 2, "two")])


def test_persisted_voice_survives_retries_and_rejects_changed_localization(tmp_path):
    from workflow.production.voices import select_voice
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({
        "job_id": "job_test", "market": "BR", "locale": "pt-BR",
        "status": "user_approved_both_auditions", "localization_sha256": "localization-v2",
        "approved_auditions": [
            {"voice": "pt-BR-FranciscaNeural", "sha256": "audio-a", "file": "a.mp3"},
            {"voice": "pt-BR-ThalitaMultilingualNeural", "sha256": "audio-b", "file": "b.mp3"},
        ],
    }))
    selection = tmp_path / "selection.json"
    first = select_voice(approval, selection, "job_test", 1)
    saved = selection.read_bytes()
    assert first["voice"] in ("pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural")
    assert select_voice(approval, selection, "job_test", 1) == first
    assert selection.read_bytes() == saved
    content = json.loads(approval.read_text())
    content["localization_sha256"] = "localization-v3"
    approval.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="selection|approval"):
        select_voice(approval, selection, "job_test", 1)


def test_new_market_pool_requires_its_own_real_audition_confirmation(tmp_path):
    from workflow.production.voices import select_voice
    audition=tmp_path/'dalia.wav';audition.write_bytes(b'test audition')
    approval=tmp_path/'approved.json'
    approval.write_text(json.dumps({'market':'MX','locale':'es-MX','status':'user_approved_voice_pool',
        'user_reply':'测试音色认可','source_turn_id':'test-only',
        'approved_auditions':[{'voice':'es-MX-DaliaNeural','file':audition.name,
                               'sha256':hashlib.sha256(audition.read_bytes()).hexdigest()}]}))
    options={'approval_scope':'market_pool','localization_sha256':'test-script','market':'MX','locale':'es-MX'}
    selected=select_voice(approval,tmp_path/'selection.json','job_mx',1,**options)
    assert selected['market']=='MX' and selected['voice']=='es-MX-DaliaNeural'
    audition.write_bytes(b'changed')
    with pytest.raises(ValueError,match='audition'):
        select_voice(approval,tmp_path/'other.json','job_mx',2,**options)


def test_render_rejects_bound_file_tampering(render_input, tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    render_input["bound_files"] = [{"path": str(evidence), "sha256": "wrong"}]
    with pytest.raises(ValueError, match="hash"):
        render_plan(render_input)


def test_audio_qa_measures_pcm_and_true_peak_with_saved_log(render_input, tmp_path):
    from workflow.production.qa import measure_audio
    metrics = measure_audio(render_input["voice_audio"], tmp_path / "audio.log")
    assert metrics["clipped_pcm_samples"] == 0
    assert metrics["sample_peak_dbfs"] == pytest.approx(-18.06, abs=.1)
    assert metrics["true_peak_dbfs"] == pytest.approx(-18.1, abs=.2)
    assert metrics["decoded_duration_seconds"] == 1
    assert "True peak:" in (tmp_path / "audio.log").read_text()


def test_approved_voice_pool_can_record_new_script_and_lock_its_hash(tmp_path):
    from workflow.production.voices import select_voice
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({
        "job_id": "job_test", "market": "BR", "locale": "pt-BR",
        "status": "user_approved_both_auditions", "localization_sha256": "old-script",
        "approved_auditions": [
            {"voice": "pt-BR-FranciscaNeural", "sha256": "audition", "file": "a.mp3"},
        ],
    }))
    selection = tmp_path / "selection.json"
    first = select_voice(approval, selection, "job_test", 1,
                         localization_sha256="new-script")
    assert first["localization_sha256"] == "new-script"
    assert first["approved_audition_sha256"] == "audition"
    assert first["approval_scope"] == "voice_pool_not_generated_recording"
    assert select_voice(approval, selection, "job_test", 1,
                        localization_sha256="new-script") == first
    with pytest.raises(ValueError, match="selection"):
        select_voice(approval, selection, "job_test", 1,
                     localization_sha256="changed-script")


def test_new_recording_must_match_selected_voice_and_localization():
    from workflow.production.voices import validate_recording
    selected = {"voice": "pt-BR-FranciscaNeural", "localization_sha256": "script",
                "rate": "+0%", "volume": "+0%", "pitch": "+0Hz"}
    record = {**selected, "audio_sha256": "new-audio"}
    assert validate_recording(selected, record, "new-audio") is True
    for key, value in (("voice", "other-voice"), ("localization_sha256", "other-script"),
                       ("audio_sha256", "other-audio"), ("rate", "+10%")):
        with pytest.raises(ValueError, match="recording"):
            validate_recording(selected, {**record, key: value}, "new-audio")


@pytest.fixture
def rotation_approval(tmp_path):
    path = tmp_path / "approval.json"
    path.write_text(json.dumps({
        "job_id": "audition_job", "market": "BR", "locale": "pt-BR",
        "status": "user_approved_both_auditions", "localization_sha256": "audition-script",
        "approved_auditions": [
            {"voice": "pt-BR-FranciscaNeural", "sha256": "audio-a", "file": "a.mp3"},
            {"voice": "pt-BR-ThalitaMultilingualNeural", "sha256": "audio-b", "file": "b.mp3"},
        ],
    }))
    return path


def test_cross_job_voice_pool_requires_explicit_scope_and_localization(rotation_approval, tmp_path):
    from workflow.production.voices import select_voice
    selection = tmp_path / "selection.json"
    with pytest.raises(ValueError, match="job/market"):
        select_voice(rotation_approval, selection, "new_job", 1)
    with pytest.raises(ValueError, match="localization"):
        select_voice(rotation_approval, selection, "new_job", 1, approval_scope="market_pool")
    with pytest.raises(ValueError, match="job/market"):
        select_voice(rotation_approval, selection, "new_job", 1, approval_scope="market_pool",
                     localization_sha256="new-script", market="MX", locale="es-MX")
    assert not selection.exists()
    saved = select_voice(rotation_approval, selection, "new_job", 1,
                         approval_scope="market_pool", localization_sha256="new-script", rate="+15%")
    assert saved["rate"] == "+15%"
    assert saved["approval_scope"] == "voice_pool_not_generated_recording"
    assert saved["pool_authorization_scope"] == "market_pool"


def test_batch_voice_rotation_exhausts_pool_and_keeps_retry_files(rotation_approval, tmp_path):
    from workflow.production.voices import select_voice
    options = dict(approval_scope="market_pool", localization_sha256="new-script",
                   rate="+15%", batch_state_path=tmp_path / "batch.json",
                   prior_voice="pt-BR-ThalitaMultilingualNeural")
    records = [select_voice(rotation_approval, tmp_path / f"selection-{i}.json", f"job-{i}", i,
                            **options) for i in range(1, 4)]
    assert [record["voice"] for record in records] == [
        "pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural", "pt-BR-FranciscaNeural"]
    selection = tmp_path / "selection-1.json"
    before = selection.read_bytes(), (tmp_path / "batch.json").read_bytes()
    assert select_voice(rotation_approval, selection, "job-1", 1, **options) == records[0]
    assert before == (selection.read_bytes(), (tmp_path / "batch.json").read_bytes())
    with pytest.raises(ValueError, match="selection"):
        select_voice(rotation_approval, selection, "job-1", 1, **{**options, "rate": "+20%"})
    assert before == (selection.read_bytes(), (tmp_path / "batch.json").read_bytes())


def test_batch_voice_state_rejects_other_approval_or_selection_path(rotation_approval, tmp_path):
    from workflow.production.voices import select_voice
    selection = tmp_path / "selection.json"
    options = dict(approval_scope="market_pool", localization_sha256="new-script",
                   batch_state_path=tmp_path / "batch.json")
    select_voice(rotation_approval, selection, "new_job", 1, **options)
    before = selection.read_bytes(), (tmp_path / "batch.json").read_bytes()
    with pytest.raises(ValueError, match="selection"):
        select_voice(rotation_approval, selection, "new_job", 1,
                     **{**options, "batch_state_path": tmp_path / "other-batch.json"})
    content = json.loads(rotation_approval.read_text())
    content["localization_sha256"] = "modified-approval"
    rotation_approval.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="batch|approval"):
        select_voice(rotation_approval, tmp_path / "next.json", "another-job", 2, **options)
    assert before == (selection.read_bytes(), (tmp_path / "batch.json").read_bytes())


def test_batch_recovers_reserved_selection_without_drawing_again(rotation_approval, tmp_path):
    from workflow.production.voices import select_voice
    selection = tmp_path / "selection.json"
    options = dict(approval_scope="market_pool", localization_sha256="new-script",
                   batch_state_path=tmp_path / "batch.json")
    saved = select_voice(rotation_approval, selection, "new_job", 1, **options)
    state_bytes = (tmp_path / "batch.json").read_bytes()
    selection.unlink()
    assert select_voice(rotation_approval, selection, "new_job", 1, **options) == saved
    assert (tmp_path / "batch.json").read_bytes() == state_bytes


def test_cli_render_reuses_market_pool_voice_options(render_input, rotation_approval, tmp_path):
    from scripts.produce_video import main
    from workflow.production.voices import select_voice
    options = dict(approval_scope="market_pool", rate="+15%", batch_state_path="batch.json",
                   prior_voice="pt-BR-ThalitaMultilingualNeural")
    selection, words, recording = [tmp_path / name for name in ("selection.json", "words.json", "recording.json")]
    saved = select_voice(rotation_approval, selection, "new_job", 1,
                         localization_sha256="new-script", **{**options, "batch_state_path": tmp_path / "batch.json"})
    words.write_text("{}")
    recording.write_text(json.dumps({**saved, "audio_sha256": render_input["voice_audio_sha256"],
        "voice_selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
        "word_metadata_sha256": hashlib.sha256(words.read_bytes()).hexdigest()}))
    render_input.update(job_id="new_job", ordinal=1, localization_sha256="new-script",
                        voice_approval=str(rotation_approval), voice_selection="selection.json",
                        voice_recording="recording.json", word_metadata="words.json", voice_options=options,
                        bound_files=[{"path": str(recording), "sha256": hashlib.sha256(recording.read_bytes()).hexdigest()}])
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(render_input))
    assert main(["render", str(plan), "--workspace", str(tmp_path)]) == 0
    assert Path(render_input["output"]).is_file()


def test_batch_voice_recovers_interrupted_selection_write(rotation_approval, tmp_path, monkeypatch):
    import workflow.production.voices as voices
    selection = tmp_path / "selection.json"
    options = dict(approval_scope="market_pool", localization_sha256="new-script",
                   batch_state_path=tmp_path / "batch.json")
    import os
    original_fsync = os.fsync
    calls = 0
    def interrupt_second_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("interrupted selection write")
        return original_fsync(fd)
    monkeypatch.setattr(os, "fsync", interrupt_second_fsync)
    with pytest.raises(OSError, match="interrupted"):
        voices.select_voice(rotation_approval, selection, "new_job", 1, **options)
    assert not selection.exists()
    state = json.loads((tmp_path / "batch.json").read_text())
    reserved = state["selections"][str(selection.resolve())]
    monkeypatch.setattr(os, "fsync", original_fsync)
    assert voices.select_voice(rotation_approval, selection, "new_job", 1, **options) == reserved


def test_recording_rejects_changed_word_metadata_or_voice_selection():
    from workflow.production.voices import validate_recording
    selected = {"voice": "pt-BR-FranciscaNeural", "localization_sha256": "script",
                "rate": "+0%", "volume": "+0%", "pitch": "+0Hz"}
    record = {**selected, "audio_sha256": "audio", "word_metadata_sha256": "words",
              "voice_selection_sha256": "selection"}
    assert validate_recording(selected, record, "audio", selection_sha256="selection",
                              word_metadata_sha256="words")
    for selection, words in (("changed", "words"), ("selection", "changed")):
        with pytest.raises(ValueError, match="recording"):
            validate_recording(selected, record, "audio", selection_sha256=selection,
                               word_metadata_sha256=words)


@pytest.fixture
def localization_revision(rotation_approval, tmp_path):
    from workflow.production.voices import select_voice
    selection = tmp_path / "voice-selection.json"
    old, new = "a" * 64, "b" * 64
    options = dict(approval_scope="market_pool", localization_sha256=old,
                   rate="+15%", batch_state_path=tmp_path / "batch.json")
    saved = select_voice(rotation_approval, selection, "new_job", 1, **options)
    evidence = tmp_path / "review.json"
    evidence.write_text(json.dumps({"status": "passed_independent_internal_text_review",
        "previous_localization_sha256": old, "localization_sha256": new,
        "material_changes_to_approved_meaning": []}))
    return selection, old, new, evidence, saved, options


def test_localization_revision_preserves_voice_archives_bytes_and_retries(localization_revision, rotation_approval):
    from workflow.production.voices import revise_localization_binding, select_voice
    selection, old, new, evidence, saved, options = localization_revision
    previous_bytes = selection.read_bytes()
    result = revise_localization_binding(selection, old, new, evidence)
    archive = selection.parent / f"voice-selection-before-{old[:12]}.json"
    assert archive.read_bytes() == previous_bytes
    assert result["localization_sha256"] == new
    assert result["localization_revision"]["previous_selection_sha256"] == hashlib.sha256(previous_bytes).hexdigest()
    assert result["localization_revision"]["evidence_sha256"] == hashlib.sha256(evidence.read_bytes()).hexdigest()
    for key, value in saved.items():
        if key != "localization_sha256":
            assert result[key] == value
    after = selection.read_bytes(), Path(options["batch_state_path"]).read_bytes()
    assert revise_localization_binding(selection, old, new, evidence) == result
    assert after == (selection.read_bytes(), Path(options["batch_state_path"]).read_bytes())
    assert select_voice(rotation_approval, selection, "new_job", 1,
                        **{**options, "localization_sha256": new}) == result


@pytest.mark.parametrize("change", ["old", "status", "new", "meaning", "batch", "archive"])
def test_localization_revision_rejects_unbound_review_without_writes(localization_revision, change):
    from workflow.production.voices import revise_localization_binding
    selection, old, new, evidence, saved, options = localization_revision
    if change == "old":
        old = "c" * 64
    elif change == "batch":
        state = json.loads(Path(options["batch_state_path"]).read_text())
        state["selections"][str(selection.resolve())]["voice"] = "changed"
        Path(options["batch_state_path"]).write_text(json.dumps(state))
    elif change == "archive":
        (selection.parent / f"voice-selection-before-{old[:12]}.json").write_text("changed")
    else:
        review = json.loads(evidence.read_text())
        review[{"status": "status", "new": "localization_sha256", "meaning": "material_changes_to_approved_meaning"}[change]] = {
            "status": "pending", "new": "c" * 64, "meaning": ["new claim"]}[change]
        evidence.write_text(json.dumps(review))
    before = selection.read_bytes(), Path(options["batch_state_path"]).read_bytes()
    with pytest.raises(ValueError, match="revision|archive|batch"):
        revise_localization_binding(selection, old, new, evidence)
    assert before == (selection.read_bytes(), Path(options["batch_state_path"]).read_bytes())


def test_localization_revision_recovers_between_batch_and_selection(localization_revision, monkeypatch):
    import workflow.production.voices as voices
    selection, old, new, evidence, saved, options = localization_revision
    import os
    original = os.replace
    def interrupt_selection(src, dst):
        if Path(dst) == selection:
            raise OSError("interrupted revision")
        return original(src, dst)
    monkeypatch.setattr(os, "replace", interrupt_selection)
    with pytest.raises(OSError, match="interrupted"):
        voices.revise_localization_binding(selection, old, new, evidence)
    monkeypatch.setattr(os, "replace", original)
    result = voices.revise_localization_binding(selection, old, new, evidence)
    state = json.loads(Path(options["batch_state_path"]).read_text())
    assert result == state["selections"][str(selection.resolve())]
    assert result["voice"] == saved["voice"]


def test_localization_revision_rejects_changed_review_on_retry(localization_revision):
    from workflow.production.voices import revise_localization_binding
    selection, old, new, evidence, saved, options = localization_revision
    revise_localization_binding(selection, old, new, evidence)
    before = selection.read_bytes(), Path(options["batch_state_path"]).read_bytes()
    evidence.write_text(evidence.read_text() + "\n")
    with pytest.raises(ValueError, match="revision"):
        revise_localization_binding(selection, old, new, evidence)
    assert before == (selection.read_bytes(), Path(options["batch_state_path"]).read_bytes())


def test_cli_render_automatically_records_registered_local_sources(render_input, tmp_path):
    from scripts.produce_video import main
    from workflow.production.library import LocalLibrary
    db = tmp_path / "library.sqlite3"
    lib = LocalLibrary(tmp_path, db)
    lib.register({"assets": [{
        "path": render_input["source"], "sha256": render_input["source_sha256"],
        "product": "test-product", "creator": "test-creator", "duration": 1,
        "review_status": "sampled_visual_review_passed", "rights_status": "unverified", "clips": []}]})
    render_input.update(job_id="job_test", ordinal=1)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(render_input))
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"product": "test-product", "market": "BR",
        "employee": "local-test", "angle": "test-angle", "hook": "看一下",
        "chinese_lines": ["看看实际动作。"]}))
    argv = ["render", str(plan), "--workspace", str(tmp_path), "--library", str(db),
            "--context", str(context)]
    assert main(argv) == 0
    assert len(lib.history("test-product")) == 1
    assert lib.history("test-product")[0]["review"]["status"] == "pending_user"


def test_library_rejects_unregistered_source_before_render_creates_output(render_input, tmp_path):
    from scripts.produce_video import main
    render_input.update(job_id="job_test", ordinal=1)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(render_input))
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"product": "test-product", "market": "BR",
        "employee": "local-test", "angle": "test-angle", "hook": "看一下",
        "chinese_lines": ["看看实际动作。"]}))
    assert main(["render", str(plan), "--workspace", str(tmp_path), "--library",
                 str(tmp_path / "library.sqlite3"), "--context", str(context)]) == 1
    assert not Path(render_input["output"]).exists()


@pytest.mark.parametrize("change", ["voice", "binding", "missing_srt", "changed_srt"])
def test_cached_render_checks_evidence_and_restores_subtitles(render_input, tmp_path, monkeypatch, capsys, change):
    import scripts.produce_video as producer
    from workflow.production.library import LocalLibrary
    evidence = tmp_path / "approval.json"
    evidence.write_text('{"approved": true}')
    render_input.update(job_id="job_test", ordinal=1,
                        bound_files=[{"path": str(evidence),
                                      "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}])
    lib = LocalLibrary(tmp_path, "library.sqlite3")
    lib.register({"assets": [{"path": render_input["source"],
        "sha256": render_input["source_sha256"], "product": "test-product", "creator": "test",
        "duration": 1, "review_status": "sampled_visual_review_passed", "rights_status": "unverified"}]})
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"product": "test-product", "market": "BR", "employee": "test",
        "angle": "test", "hook": "看一下", "chinese_lines": ["看看实际动作。"]}))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(render_input))
    argv = ["render", str(plan), "--workspace", str(tmp_path), "--library", "library.sqlite3",
            "--context", str(context)]
    assert producer.main(argv) == 0
    capsys.readouterr()
    srt = Path(render_input["srt"])
    expected = srt.read_bytes()
    output = Path(render_input["output"])
    original_output = output.read_bytes()
    if change == "voice":
        Path(render_input["voice_audio"]).write_bytes(b"changed")
    elif change == "binding":
        evidence.write_text('{"approved": false}')
    elif change == "missing_srt":
        srt.unlink()
    else:
        srt.write_text("incorrect captions")
    def no_render(_):
        pytest.fail("cached MP4 must not be re-rendered")
    monkeypatch.setattr(producer, "render_plan", no_render)
    result = producer.main(argv)
    captured = capsys.readouterr()
    if change in ("voice", "binding"):
        assert result == 1
        assert "hash mismatch" in captured.err
    else:
        assert result == 0
        assert json.loads(captured.out)["render_reused"] is True
        assert srt.read_bytes() == expected
    assert output.read_bytes() == original_output
    assert len(lib.history("test-product")) == 1
