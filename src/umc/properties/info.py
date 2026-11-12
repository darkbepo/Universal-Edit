"""The "About this file" sections, built from plain facts.

Each app reads a file with its own engines (desktop: Pillow / ffprobe / PyMuPDF / fontTools,
``properties/read.py``; web: ImageMagick / Mediabunny / MuPDF / own parsers) into the same plain
dicts, and these builders turn them into rows — ``web/src/properties/info.ts`` is a line-by-line
port, and both test suites check ``tests/fixtures/properties_cases.json``. Dates arrive already
as display text (local time), so the builders don't depend on the time zone.

Facts (every key optional):
  file:     name, type, size, modified, folder
  picture:  width, height, kind ("colour" | "greyscale" | "bw" | "cmyk"), alpha, frames, dpi
            [x, y], orientation (EXIF 1–8), and from EXIF: make, model, lens, taken, exposure,
            fnumber, iso, focal, flash, description, artist, copyright, lat, lon, altitude
  media:    duration, size, video {codec, width, height, fps, rotation}, audio [{codec,
            channels, sample_rate, language}], subtitles [{language}], tags {title, artist,
            album, album_artist, track, disc, date, genre, composer, comment}, cover, lat, lon,
            recorded
  document: pages, page_size [w, h] (points), pdf_version, encrypted, title, author, subject,
            keywords, last_saved_by, company, application, created, modified, words, slides
  font:     family, style, version, designer, manufacturer, copyright, glyphs, outlines
            ("ttf" | "cff"), axes [tag…]
  model:    rows — ``editor3d.mesh.describe`` (already the same in both apps)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from umc.properties import format as fmt


@dataclass
class Section:
    title: str
    rows: list[tuple[str, str]] = field(default_factory=list)

    def add(self, label: str, value: object) -> None:
        if value is None:
            return
        text = str(value).strip()
        if text:
            self.rows.append((label, text))

    def to_json(self) -> dict:
        return {"title": self.title, "rows": [list(r) for r in self.rows]}


# FFmpeg's and the browser's (Mediabunny / WebCodecs) codec ids → what people call them
CODECS = {
    "h264": "H.264",
    "avc": "H.264",
    "hevc": "H.265 (HEVC)",
    "vp8": "VP8",
    "vp9": "VP9",
    "av1": "AV1",
    "mpeg4": "MPEG-4 Part 2",
    "mpeg2video": "MPEG-2",
    "mpeg1video": "MPEG-1",
    "mjpeg": "Motion JPEG",
    "prores": "ProRes",
    "theora": "Theora",
    "wmv3": "Windows Media Video",
    "wmv2": "Windows Media Video",
    "flv1": "Flash Video",
    "aac": "AAC",
    "mp3": "MP3",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "flac": "FLAC",
    "alac": "ALAC (Apple Lossless)",
    "ac3": "Dolby Digital (AC-3)",
    "eac3": "Dolby Digital Plus (E-AC-3)",
    "dts": "DTS",
    "wmav2": "Windows Media Audio",
    "wmav1": "Windows Media Audio",
    "amr_nb": "AMR",
    "amrnb": "AMR",
}

COLOUR = {
    "colour": "Colour",
    "greyscale": "Greyscale",
    "bw": "Black and white",
    "cmyk": "CMYK (for print)",
}

ORIENTATION = {
    2: "Mirrored",
    3: "Upside down",
    4: "Upside down, mirrored",
    5: "Turned 90° left, mirrored",
    6: "Turned 90° right",
    7: "Turned 90° right, mirrored",
    8: "Turned 90° left",
}

TAGS = (
    ("title", "Title"),
    ("artist", "Artist"),
    ("album", "Album"),
    ("album_artist", "Album artist"),
    ("track", "Track"),
    ("disc", "Disc"),
    ("date", "Year"),
    ("genre", "Genre"),
    ("composer", "Composer"),
    ("comment", "Comment"),
)


def codec_name(codec: str) -> str:
    c = (codec or "").lower()
    if c.startswith("pcm"):
        return "PCM (uncompressed)"
    return CODECS.get(c, c.upper())


def file_section(f: dict) -> Section:
    s = Section("File")
    s.add("Name", f.get("name"))
    s.add("Type", f.get("type"))
    if f.get("size") is not None:
        s.add("Size", fmt.size_exact(int(f["size"])))
    s.add("Modified", f.get("modified"))
    s.add("Folder", f.get("folder"))
    return s


def picture_sections(p: dict) -> list[Section]:
    pic = Section("Picture")
    w, h = int(p.get("width") or 0), int(p.get("height") or 0)
    if w and h:
        pic.add("Dimensions", fmt.pixels(w, h))
        if w * h >= 100_000:
            pic.add("Megapixels", fmt.megapixels(w, h))
    if p.get("kind") in COLOUR:
        pic.add("Colour", COLOUR[p["kind"]] + (", with transparency" if p.get("alpha") else ""))
    frames = int(p.get("frames") or 1)
    if frames > 1:
        pic.add("Frames", f"{fmt.count(frames)} (animated)")
    dpi = p.get("dpi")
    if dpi and dpi[0] and dpi[1]:
        x, y = round(dpi[0]), round(dpi[1])
        pic.add("Resolution", f"{x} dpi" if x == y else f"{x} × {y} dpi")
    pic.add("Orientation", ORIENTATION.get(int(p.get("orientation") or 1)))

    cam = Section("Camera")
    make, model = (p.get("make") or "").strip(), (p.get("model") or "").strip()
    if make and model.lower().startswith(make.lower().split()[0]):
        make = ""  # "Canon" + "Canon EOS R6" → "Canon EOS R6"
    cam.add("Camera", " ".join(x for x in (make, model) if x))
    cam.add("Lens", p.get("lens"))
    cam.add("Taken", p.get("taken"))
    if p.get("exposure"):
        cam.add("Exposure", fmt.exposure(float(p["exposure"])))
    if p.get("fnumber"):
        cam.add("Aperture", fmt.aperture(float(p["fnumber"])))
    if p.get("iso"):
        cam.add("ISO", int(p["iso"]))
    if p.get("focal"):
        cam.add("Focal length", fmt.focal(float(p["focal"])))
    if p.get("flash") is not None:
        cam.add("Flash", "Fired" if int(p["flash"]) & 1 else "Didn't fire")

    det = Section("Details")
    det.add("Description", p.get("description"))
    det.add("Author", p.get("artist"))
    det.add("Copyright", p.get("copyright"))

    return [pic, cam, det, _location(p)]


def _location(d: dict) -> Section:
    loc = Section("Location")
    if d.get("lat") is not None and d.get("lon") is not None:
        loc.add("Coordinates", fmt.location(float(d["lat"]), float(d["lon"])))
        if d.get("altitude") is not None:
            loc.add("Altitude", f"{round(float(d['altitude']))} m")
    return loc


def media_sections(m: dict) -> list[Section]:
    out: list[Section] = []
    duration = float(m.get("duration") or 0)
    size = int(m.get("size") or 0)
    rate = fmt.bitrate(size * 8 / duration) if duration and size else None
    v = m.get("video")
    audio = list(m.get("audio") or [])
    if v:
        vs = Section("Video")
        if duration:
            vs.add("Length", fmt.duration(duration))
        if v.get("width") and v.get("height"):
            vs.add("Dimensions", fmt.pixels(int(v["width"]), int(v["height"])))
        if v.get("fps"):
            vs.add("Frame rate", fmt.fps(float(v["fps"])))
        vs.add("Codec", codec_name(v.get("codec", "")) if v.get("codec") else None)
        rot = int(v.get("rotation") or 0) % 360
        if rot:
            vs.add("Rotation", f"{rot}°")
        vs.add("Bit rate", rate)
        vs.add("Recorded", m.get("recorded"))
        out.append(vs)
    for i, a in enumerate(audio):
        title = "Audio" if len(audio) == 1 else f"Audio track {i + 1}"
        s = Section(title)
        if not v and i == 0:
            if duration:
                s.add("Length", fmt.duration(duration))
            s.add("Bit rate", rate)
        s.add("Codec", codec_name(a.get("codec", "")) if a.get("codec") else None)
        if a.get("channels"):
            s.add("Channels", fmt.channels(int(a["channels"])))
        if a.get("sample_rate"):
            s.add("Sample rate", fmt.sample_rate(int(a["sample_rate"])))
        lang = (a.get("language") or "").lower()
        if lang and lang != "und":
            s.add("Language", lang)
        out.append(s)
    subs = list(m.get("subtitles") or [])
    if subs:
        s = Section("Subtitles")
        s.add("Tracks", len(subs))
        langs = [x.get("language", "").lower() for x in subs]
        s.add("Languages", ", ".join(x for x in langs if x and x != "und"))
        out.append(s)
    tags = m.get("tags") or {}
    ts = Section("Tags")
    for key, label in TAGS:
        value = tags.get(key)
        if key == "date" and value:
            value = str(value).strip()[:4] if str(value).strip()[:4].isdigit() else value
        ts.add(label, value)
    if m.get("cover"):
        ts.add("Cover picture", "Yes")
    out.append(ts)
    out.append(_location(m))
    return out


def document_sections(d: dict) -> list[Section]:
    s = Section("Document")
    if d.get("pages"):
        s.add("Pages", fmt.count(int(d["pages"])))
    if d.get("slides"):
        s.add("Slides", fmt.count(int(d["slides"])))
    if d.get("words"):
        s.add("Words", fmt.count(int(d["words"])))
    size = d.get("page_size")
    if size and size[0] and size[1]:
        s.add("Page size", fmt.paper(float(size[0]), float(size[1])))
    s.add("Title", d.get("title"))
    s.add("Subject", d.get("subject"))
    s.add("Author", d.get("author"))
    s.add("Last saved by", d.get("last_saved_by"))
    s.add("Company", d.get("company"))
    s.add("Keywords", d.get("keywords"))
    s.add("Created", d.get("created"))
    s.add("Modified", d.get("modified"))
    s.add("Made with", d.get("application"))
    s.add("Version", d.get("pdf_version"))
    if d.get("encrypted"):
        s.add("Password", "Protected")
    return [s]


def font_sections(f: dict) -> list[Section]:
    s = Section("Font")
    s.add("Family", f.get("family"))
    s.add("Style", f.get("style"))
    version = (f.get("version") or "").strip()
    if version.lower().startswith("version "):
        version = version[8:]
    s.add("Version", version)
    s.add("Designer", f.get("designer"))
    s.add("Maker", f.get("manufacturer"))
    s.add("Copyright", f.get("copyright"))
    if f.get("glyphs"):
        s.add("Glyphs", fmt.count(int(f["glyphs"])))
    if f.get("outlines"):
        s.add("Outlines", "TrueType" if f["outlines"] == "ttf" else "PostScript (CFF)")
    axes = list(f.get("axes") or [])
    if axes:
        s.add("Variable", "Yes (" + ", ".join(axes) + ")")
    return [s]


def build(facts: dict) -> list[Section]:
    """Every section with at least one row, "File" first."""
    sections = [file_section(facts.get("file") or {})]
    if "picture" in facts:
        sections += picture_sections(facts["picture"])
    if "media" in facts:
        sections += media_sections(facts["media"])
    if "document" in facts:
        sections += document_sections(facts["document"])
    if "font" in facts:
        sections += font_sections(facts["font"])
    if "model" in facts:
        sections.append(Section("3D model", [tuple(r) for r in facts["model"]]))
    if facts.get("note"):
        sections.append(Section("Note", [("", facts["note"])]))
    return [s for s in sections if s.rows]
