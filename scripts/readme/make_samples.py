"""Make good-looking sample files for the README screenshots (no binaries in git).

    python scripts/readme/make_samples.py OUT_DIR

Writes pictures (painted landscapes), a Word letter, a stereo song, a 3D model (torus knot),
a short video and a PDF. Everything is drawn from code, so it is free to show.
"""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

W, H = 1600, 1000


def _mix(a, b, t):
    return a + (b - a) * t


def landscape(path: Path, sky_top, sky_low, sun, hills, water, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, H)[:, None, None]
    img = _mix(np.array(sky_top, float), np.array(sky_low, float), y**0.8) * np.ones((H, W, 3))
    # sun glow
    yy, xx = np.mgrid[0:H, 0:W]
    sx, sy = W * 0.68, H * 0.46
    d = np.hypot(xx - sx, yy - sy)
    glow = np.exp(-((d / 260) ** 2))[..., None]
    img = img + (np.array(sun, float) - img) * glow * 0.75
    img[d < 70] = sun
    horizon = int(H * 0.62)
    # mountain ridges, far to near
    for i, col in enumerate(hills):
        base = H * (0.42 + i * 0.07)
        x = np.arange(W)
        ridge = base
        for f, a in ((1 / 900, 90), (1 / 310, 40), (1 / 90, 12), (1 / 23, 4)):
            ph = rng.uniform(0, 6.28)
            ridge = ridge + a * (1 - i * 0.15) * np.sin(x * f * 6.28 + ph)
        mask = yy < horizon
        under = (yy >= ridge[None, :]) & mask
        shade = np.clip((yy - ridge[None, :]) / 300, 0, 1)[..., None]
        c = np.array(col, float)
        img[under] = (c * (1 - shade * 0.25))[under]
    # water: mirrored sky + hills, darkened, with ripples
    top = img[:horizon][::-1]
    rows = H - horizon
    refl = top[:rows].copy()
    ripple = (np.sin(np.arange(rows)[:, None] * 0.9 + xx[:rows] * 0.02) * 3).astype(int)
    cols = np.clip(np.arange(W)[None, :] + ripple, 0, W - 1)
    refl = refl[np.arange(rows)[:, None], cols]
    img[horizon:] = _mix(refl, np.array(water, float), 0.45)
    img += rng.normal(0, 2.2, img.shape)
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    out = out.filter(ImageFilter.GaussianBlur(0.6))
    out.save(path, quality=92)
    return out


def pictures(out: Path) -> None:
    landscape(
        out / "lake-sunrise.jpg",
        (38, 54, 112),
        (247, 176, 120),
        (255, 236, 190),
        [(120, 92, 140), (84, 66, 112), (52, 44, 82), (30, 28, 54)],
        (40, 46, 90),
        3,
    )
    landscape(
        out / "alpine-noon.jpg",
        (40, 110, 200),
        (175, 215, 240),
        (255, 252, 235),
        [(150, 180, 205), (95, 135, 150), (60, 100, 90), (34, 66, 52)],
        (30, 80, 110),
        7,
    )
    landscape(
        out / "desert-dusk.jpg",
        (70, 30, 90),
        (250, 120, 80),
        (255, 210, 150),
        [(200, 110, 90), (160, 80, 70), (115, 55, 60), (70, 35, 45)],
        (90, 40, 70),
        11,
    )
    landscape(
        out / "teal-evening.jpg",
        (12, 40, 60),
        (60, 160, 170),
        (220, 255, 240),
        [(40, 110, 120), (25, 80, 95), (15, 55, 70), (8, 35, 48)],
        (10, 50, 70),
        19,
    )


def camera_photo(out: Path) -> None:
    """lake-sunrise with the EXIF a camera writes (for the Properties screenshot)."""
    from PIL.TiffImagePlugin import IFDRational as R

    img = Image.open(out / "lake-sunrise.jpg")
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "Canon EOS R6"
    exif[0x013B] = "Sam Rivers"
    exif[0x010E] = "Sunrise over the lake"
    sub = exif.get_ifd(0x8769)
    sub[0x829A] = R(1, 250)
    sub[0x829D] = R(8, 1)
    sub[0x8827] = 100
    sub[0x9003] = "2026:04:18 06:14:52"
    sub[0x920A] = R(35, 1)
    sub[0xA434] = "RF 24-105mm F4 L IS USM"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (R(61, 1), R(38, 1), R(1250, 100))
    gps[3], gps[4] = "E", (R(8, 1), R(18, 1), R(4420, 100))
    img.save(out / "IMG_2048.jpg", quality=92, exif=exif)


def letter(out: Path) -> None:
    import docx
    from docx.shared import Pt, RGBColor

    d = docx.Document()
    d.add_heading("Spring trip to the lakes", 0)
    p = d.add_paragraph("Hi everyone — here's the plan for our long weekend. ")
    r = p.add_run("Please reply by Friday")
    r.bold = True
    p.add_run(" so we can book the cabins.")
    d.add_heading("What to bring", 1)
    for item in (
        "Warm layers and a rain jacket",
        "Walking shoes",
        "A camera (sunrise is at 6:12!)",
        "Snacks for the boat",
    ):
        d.add_paragraph(item, style="List Bullet")
    d.add_heading("Schedule", 1)
    t = d.add_table(rows=1, cols=3)
    t.style = "Light Grid Accent 1"
    for c, h in zip(t.rows[0].cells, ("Day", "Morning", "Afternoon"), strict=False):
        c.text = h
    for row in (
        ("Saturday", "Drive up, check in", "Lake walk"),
        ("Sunday", "Sunrise photos", "Boat trip"),
        ("Monday", "Breakfast at the café", "Drive home"),
    ):
        cells = t.add_row().cells
        for c, v in zip(cells, row, strict=False):
            c.text = v
    q = d.add_paragraph()
    q.add_run("\nSee you there! ").italic = True
    s = q.add_run("— Sam")
    s.font.color.rgb = RGBColor(0x2B, 0x6C, 0xB0)
    s.font.size = Pt(12)
    d.save(out / "lake-trip.docx")


def song(out: Path) -> None:
    rate, secs = 44100, 24
    t = np.arange(rate * secs) / rate
    chords = [
        (220.0, 277.18, 329.63),
        (196.0, 246.94, 293.66),
        (174.61, 220.0, 261.63),
        (196.0, 246.94, 329.63),
    ]
    left = np.zeros_like(t)
    right = np.zeros_like(t)
    beat = 0.5
    for i, start in enumerate(np.arange(0, secs, beat)):
        n = int(start * rate)
        m = min(len(t), n + int(beat * rate * 1.6))
        tt = t[n:m] - start
        env = np.exp(-tt * (3 if i % 2 else 1.6))
        ch = chords[int(start // 2) % len(chords)]
        note = ch[i % 3] * (2 if i % 4 == 3 else 1)
        tone = np.sin(2 * np.pi * note * tt) + 0.3 * np.sin(4 * np.pi * note * tt)
        pan = 0.5 + 0.4 * np.sin(i * 0.7)
        left[n:m] += tone * env * (1 - pan)
        right[n:m] += tone * env * pan
        if i % 2 == 0:  # kick
            kick = np.sin(2 * np.pi * (60 + 80 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 9)
            left[n:m] += kick * 0.9
            right[n:m] += kick * 0.9
    swell = 0.55 + 0.45 * np.sin(np.pi * t / secs) ** 0.6
    stereo = np.stack([left, right], 1) * swell[:, None]
    stereo /= np.abs(stereo).max() * 1.1
    with wave.open(str(out / "summer-tune.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((stereo * 32767).astype("<i2").tobytes())


def knot(out: Path) -> None:
    import trimesh

    n, m, r = 600, 24, 6.0
    s = np.linspace(0, 2 * np.pi, n, endpoint=False)
    p, q = 2, 3
    c = (
        np.stack(
            [
                (np.cos(q * s) + 2) * np.cos(p * s),
                (np.cos(q * s) + 2) * np.sin(p * s),
                -np.sin(q * s),
            ],
            1,
        )
        * 18
    )
    tang = np.roll(c, -1, 0) - np.roll(c, 1, 0)
    tang /= np.linalg.norm(tang, axis=1)[:, None]
    nrm = np.cross(tang, [0, 0, 1.0])
    nrm /= np.linalg.norm(nrm, axis=1)[:, None]
    bin_ = np.cross(tang, nrm)
    a = np.linspace(0, 2 * np.pi, m, endpoint=False)
    verts = (
        c[:, None, :]
        + r
        * (np.cos(a)[None, :, None] * nrm[:, None, :] + np.sin(a)[None, :, None] * bin_[:, None, :])
    ).reshape(-1, 3)
    faces = []
    for i in range(n):
        for j in range(m):
            a0, a1 = i * m + j, i * m + (j + 1) % m
            b0, b1 = ((i + 1) % n) * m + j, ((i + 1) % n) * m + (j + 1) % m
            faces += [(a0, b0, b1), (a0, b1, a1)]
    mesh = trimesh.Trimesh(verts, faces)
    mesh.export(out / "torus-knot.stl")


def video_and_pdf(out: Path, ffmpeg: str) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-i",
            str(out / "alpine-noon.jpg"),
            "-vf",
            "scale=1280:-2,zoompan=z='1+0.0015*on':d=150:s=1280x800:fps=30",
            "-t",
            "5",
            "-pix_fmt",
            "yuv420p",
            str(out / "mountain-flight.mp4"),
        ],
        check=True,
    )
    imgs = [Image.open(out / f).convert("RGB") for f in ("lake-sunrise.jpg", "desert-dusk.jpg")]
    imgs[0].save(out / "photo-book.pdf", save_all=True, append_images=imgs[1:])


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    pictures(out)
    camera_photo(out)
    letter(out)
    song(out)
    knot(out)
    ffmpeg = sys.argv[2] if len(sys.argv) > 2 else "ffmpeg"
    video_and_pdf(out, ffmpeg)
    print("samples in", out)


if __name__ == "__main__":
    main()
