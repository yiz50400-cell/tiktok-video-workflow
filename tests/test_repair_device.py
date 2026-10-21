"""Real tensors catch wrong-device inference and changes outside the mask."""
from pathlib import Path
import cv2
import numpy as np
import pytest
import torch
from workflow.repair.pipeline import LamaEngine, run_repair, digest, resolve_device


@pytest.fixture
def model(tmp_path):
    class Fill(torch.nn.Module):
        def forward(self, image, mask):
            return image * (1 - mask) + .25 * mask
    path = tmp_path / 'model.pt'
    torch.jit.trace(Fill(), (torch.zeros(1, 3, 320, 320),
                            torch.zeros(1, 1, 320, 320))).save(str(path))
    return path


@pytest.mark.parametrize('device', ['cpu', 'mps'])
def test_repair_uses_requested_device_without_changing_unmasked_pixels(model, device):
    if device == 'mps' and not torch.backends.mps.is_available():
        pytest.skip('MPS not available on this host')
    frame = np.full((400, 400, 3), 180, np.uint8)
    mask = np.zeros((400, 400), np.uint8)
    mask[90:150, 240:310] = 255
    result = LamaEngine(model, device=device).repair(frame, mask)
    assert np.array_equal(result[mask == 0], frame[mask == 0])
    assert np.all(result[mask > 0] == 63)


def test_unknown_device_is_rejected_before_model_load():
    with pytest.raises(ValueError, match='device'):
        LamaEngine(Path('missing.pt'), device='invalid')


def test_auto_device_uses_available_accelerator():
    expected = ('cuda' if torch.cuda.is_available() else
                'mps' if torch.backends.mps.is_available() else 'cpu')
    assert resolve_device('auto') == expected


def test_device_change_cannot_reuse_a_cpu_result(model, tmp_path):
    if not torch.backends.mps.is_available():
        pytest.skip('MPS not available on this host')
    source = tmp_path / 'source.mp4'
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 64))
    for _ in range(2):
        writer.write(np.full((64, 64, 3), 180, np.uint8))
    writer.release()
    spec = tmp_path / 'mask.json'
    spec.write_text('{"dimensions":[64,64],"regions":[{"name":"mark","mode":"box","box":[8,8,12,12]}]}')
    args = (source, spec, model, tmp_path / 'repair', 0, .2)
    cpu = run_repair(*args, device='cpu', expected_model_md5=digest(model, 'md5'))
    gpu = run_repair(*args, device='mps', expected_model_md5=digest(model, 'md5'))
    assert cpu['task_id'] != gpu['task_id']
    assert not gpu['reused'] and gpu['inference_device'] == 'mps'
    assert run_repair(*args, device='mps', expected_model_md5=digest(model, 'md5'))['reused']
