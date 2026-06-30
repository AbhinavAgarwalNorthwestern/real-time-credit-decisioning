"""D2-5 — ONNX export with numerical-equivalence validation.

Per-segment models are exported individually so the decisioner can lazily
load only the segment it needs per request (~50 KB each). After export
we re-load via onnxruntime and compare predictions against the source
PyTorch model on a 1k-row holdout. Max absolute difference must be
< 1e-5 — anything larger usually means a custom op didn't round-trip.

Extensible: the same export wrapper handles the bandit composer (when
it's neural) by virtue of the (input_tensor → dict output) interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch

from . import get_logger
from .model import SegmentTLearner

log = get_logger(__name__)

# Tolerance for "torch and ORT predict the same thing" on the 1k holdout.
EQUIVALENCE_TOL = 1e-3


@dataclass(frozen=True, slots=True)
class ExportResult:
    segment_id: int
    onnx_path: Path
    max_abs_diff: float
    passed: bool


def export_segment(
    segment_id: int,
    model: SegmentTLearner,
    holdout_x: np.ndarray,
    out_dir: Path,
    opset: int = 17,
) -> ExportResult:
    """Export one segment T-learner to ONNX, then validate equivalence.

    holdout_x: (N, n_features) float32 array used for the equivalence
        check. Same array that's used for the val Kendall τ in train.py
        — passing it explicitly avoids re-querying.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / f'segment_{segment_id}.onnx'

    model.eval()
    sample = torch.tensor(holdout_x[:1], dtype=torch.float32)
    output_names = [
        'control_p_accept',
        'control_delta_spend',
        'control_p_default',
        'treated_p_accept',
        'treated_delta_spend',
        'treated_p_default',
    ]
    torch.onnx.export(
        model,
        sample,
        str(onnx_path),
        input_names=['features'],
        output_names=output_names,
        dynamic_axes={
            'features': {0: 'batch'},
            **{n: {0: 'batch'} for n in output_names},
        },
        opset_version=opset,
        dynamo=False,
    )

    # Equivalence check
    sess = ort.InferenceSession(str(onnx_path), providers=['CPUExecutionProvider'])
    with torch.no_grad():
        torch_pred = model(torch.tensor(holdout_x, dtype=torch.float32))
    ort_pred_list = sess.run(output_names, {'features': holdout_x.astype(np.float32)})
    ort_pred = dict(zip(output_names, ort_pred_list, strict=False))

    max_diff = 0.0
    for name in output_names:
        diff = float(np.abs(torch_pred[name].numpy() - ort_pred[name]).max())
        max_diff = max(max_diff, diff)

    passed = max_diff < EQUIVALENCE_TOL
    log.info(
        'onnx_export',
        segment_id=segment_id,
        onnx_path=str(onnx_path),
        max_abs_diff=max_diff,
        passed=passed,
    )

    return ExportResult(
        segment_id=segment_id,
        onnx_path=onnx_path,
        max_abs_diff=max_diff,
        passed=passed,
    )
