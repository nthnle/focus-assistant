"""Train the desk camera's model on your own frames. Runs on a PC, not the Pi.

    python tools/train_camera.py FRAMES model.onnx      train on everything, write the model
    python tools/train_camera.py FRAMES --check         how well it does on minutes it never saw

FRAMES has one folder per label, as `python -m focus.camera --collect`
writes them: phone, work and, for presence, away. The model is a small
image classifier (MobileNetV3-Small, pretrained on ImageNet) fine-tuned to
tell those apart at your desk, from your camera's angle, in your light.

--check holds out two minutes of recording at a time and scores the model
on them, so it is graded on minutes it never saw, not on near-copies of
frames it trained on. Expect it to do worst on anything you recorded only
once (a pose, the light at one time of day): record more of that.

Needs torch, torchvision, onnx, onnxscript and pillow; with an NVIDIA GPU,
install torch's CUDA build (pytorch.org) and it is used automatically.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch import nn
from torchvision import models
from torchvision.transforms import v2

SIZE = 224          # focus.camera feeds the model 224x224 RGB frames, squashed from 4:3
GAP_S = 10          # frames further apart than this start a new stretch
MIN_HELD = 30       # --check holds out stretches of at least this many frames,
CHUNK = 60          # cut into pieces of at most this many (2 minutes), so a long
                    # session still has other minutes of it to learn from
LABEL_ORDER = ("phone", "work", "away")


def labels_in(root: Path) -> list[str]:
    found = [d.name for d in root.iterdir() if d.is_dir() and any(d.glob("*.jpg"))]
    return [name for name in LABEL_ORDER if name in found] + sorted(set(found) - set(LABEL_ORDER))


def stretches(files: list[Path]) -> list[list[Path]]:
    """Frames named YYYYMMDD-HHMMSS-*.jpg, split wherever the gap is over GAP_S."""
    out: list[list[Path]] = []
    last = None
    for f in sorted(files):
        t = datetime.strptime(f.name[:15], "%Y%m%d-%H%M%S")
        if last is None or (t - last).total_seconds() > GAP_S:
            out.append([])
        out[-1].append(f)
        last = t
    return out


def load(files: list[Path]) -> torch.Tensor:
    """uint8 NCHW at SIZE x SIZE, squashed the way ffmpeg's scale filter does on the Pi."""
    return torch.stack([
        torch.from_numpy(np.array(Image.open(f).convert("RGB").resize((SIZE, SIZE), Image.Resampling.BILINEAR)))
        .permute(2, 0, 1)
        for f in files
    ])


def normalize(x: torch.Tensor) -> torch.Tensor:
    """uint8 NCHW to what ImageNet-pretrained weights expect; training and the Pi share it."""
    mean = torch.tensor([0.485, 0.456, 0.406], device=x.device).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=x.device).view(1, 3, 1, 1)
    return (x.float() / 255.0 - mean) / std


class Deployed(nn.Module):
    """What runs on the Pi: uint8 NHWC frames in, label probabilities out."""

    def __init__(self, net: nn.Module):
        super().__init__()
        self.net = net

    def forward(self, frames: torch.Tensor) -> torch.Tensor:
        return torch.softmax(self.net(normalize(frames.permute(0, 3, 1, 2))), dim=1)


AUGMENT = v2.Compose([
    v2.RandomResizedCrop(SIZE, scale=(0.75, 1.0), ratio=(0.9, 1.1), antialias=True),
    v2.RandomHorizontalFlip(),
    v2.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.3, hue=0.03),
])


def train(x: torch.Tensor, y: torch.Tensor, n_labels: int, device: str,
          epochs: int = 12, batch: int = 32, seed: int = 0) -> nn.Module:
    torch.manual_seed(seed)
    random.seed(seed)
    net = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    net.classifier[3] = nn.Linear(net.classifier[3].in_features, n_labels)
    net.to(device)
    # Labels can be uneven (less "away" than "work"): weight the loss to match.
    counts = torch.bincount(y, minlength=n_labels).float()
    loss_fn = nn.CrossEntropyLoss(weight=(counts.sum() / (n_labels * counts.clamp(min=1))).to(device))
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-4)
    steps = epochs * ((len(x) + batch - 1) // batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=1e-3, total_steps=steps)
    x, y = x.to(device), y.to(device)
    net.train()
    for _ in range(epochs):
        order = torch.randperm(len(x), device=device)
        for i in range(0, len(x), batch):
            idx = order[i:i + batch]
            imgs = torch.stack([AUGMENT(img) for img in x[idx]])
            loss = loss_fn(net(normalize(imgs)), y[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
    return net.eval()


@torch.no_grad()
def predict(net: nn.Module, x: torch.Tensor, device: str) -> torch.Tensor:
    return torch.cat([net(normalize(x[i:i + 128].to(device))).argmax(1).cpu() for i in range(0, len(x), 128)])


def smoothed(preds: list[int], window: int = 5) -> list[int]:
    """Majority of the last `window` frames, as focus.camera decides."""
    out = []
    for i in range(len(preds)):
        recent = preds[max(0, i - window + 1): i + 1]
        best = max(set(recent), key=recent.count)
        out.append(best if recent.count(best) * 2 > len(recent) else (out[-1] if out else best))
    return out


def check(root: Path, labels: list[str], device: str, epochs: int) -> None:
    files = {lab: sorted((root / lab).glob("*.jpg")) for lab in labels}
    tensors = {lab: load(fs) for lab, fs in files.items()}
    index = {lab: {f: i for i, f in enumerate(fs)} for lab, fs in files.items()}
    held_out = [(lab, s[i:i + CHUNK]) for lab in labels for s in stretches(files[lab]) if len(s) >= MIN_HELD
                for i in range(0, len(s), CHUNK) if len(s[i:i + CHUNK]) >= MIN_HELD // 2]
    totals = {lab: [0, 0, 0] for lab in labels}  # right, right after smoothing, frames
    for lab, held in held_out:
        held_set = set(held)
        xs, ys = [], []
        for i, other in enumerate(labels):
            keep = [index[other][f] for f in files[other] if f not in held_set]
            xs.append(tensors[other][keep])
            ys.append(torch.full((len(keep),), i))
        net = train(torch.cat(xs), torch.cat(ys), len(labels), device, epochs)
        preds = predict(net, tensors[lab][[index[lab][f] for f in held]], device).tolist()
        want = labels.index(lab)
        right = sum(p == want for p in preds)
        right_s = sum(p == want for p in smoothed(preds))
        wrong = sorted({labels[p] for p in preds if p != want})
        totals[lab][0] += right
        totals[lab][1] += right_s
        totals[lab][2] += len(held)
        print(f"{lab:6} {held[0].name[:15]} {len(held):4} frames: {right / len(held):6.1%} right, "
              f"{right_s / len(held):6.1%} smoothed" + (f"  (mistaken for {', '.join(wrong)})" if wrong else ""),
              flush=True)
    print()
    for lab, (right, right_s, n) in totals.items():
        if n:
            print(f"{lab:6} {right}/{n} right ({right / n:.1%}), {right_s}/{n} smoothed ({right_s / n:.1%})")


def export(root: Path, labels: list[str], out: Path, device: str, epochs: int) -> None:
    import onnx

    files = {lab: sorted((root / lab).glob("*.jpg")) for lab in labels}
    x = torch.cat([load(files[lab]) for lab in labels])
    y = torch.cat([torch.full((len(files[lab]),), i) for i, lab in enumerate(labels)])
    net = train(x, y, len(labels), device, epochs)
    deployed = Deployed(net.cpu()).eval()
    example = torch.zeros(1, SIZE, SIZE, 3, dtype=torch.uint8)
    torch.onnx.export(deployed, (example,), str(out), input_names=["frames"],
                      output_names=["probs"], dynamo=False, opset_version=17)
    model = onnx.load(str(out))
    meta = {"labels": json.dumps(labels), "size": str(SIZE), "trained": date.today().isoformat(),
            "frames": json.dumps({lab: len(fs) for lab, fs in files.items()})}
    for key, value in meta.items():
        model.metadata_props.add(key=key, value=value)
    onnx.save(model, str(out))
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB): labels {labels}, "
          f"{', '.join(f'{len(fs)} {lab}' for lab, fs in files.items())} frames")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames", type=Path)
    ap.add_argument("out", type=Path, nargs="?", help="where to write the model (.onnx)")
    ap.add_argument("--check", action="store_true", help="score it on held-out stretches instead")
    ap.add_argument("--epochs", type=int, default=12)
    args = ap.parse_args(argv)

    labels = labels_in(args.frames)
    if len(labels) < 2 or "phone" not in labels:
        print(f"{args.frames} needs a phone folder and at least one other (work, away)")
        return 1
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Labels {labels}, training on {device}")
    if args.check:
        check(args.frames, labels, device, args.epochs)
    elif args.out:
        export(args.frames, labels, args.out, device, args.epochs)
    else:
        ap.error("give an output file, or --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
