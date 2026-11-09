// Images → slideshow video / animated GIF. Images are normalised with ImageMagick (rotation,
// HEIC, transparency, fit mode) and then encoded with FFmpeg — same approach as the desktop app.
import { opt, type Values } from "../core/options";
import { ConversionError, type Converter, type InputFile } from "../core/types";
import { pick, qualityArgs, resolveVideoCodec, VIDEO_CODEC_LABELS, VIDEO_ENCODERS, AUDIO_ENCODERS, CONTAINERS } from "./codecs";
import { baseName, MIME } from "./util";

const CONTAINER_TARGETS = ["mp4", "webm", "mkv", "mov"];
const VIDEO_ONLY = CONTAINER_TARGETS.map((c) => `slideshow:${c}`);
const PRESET_HEIGHTS: Record<string, number> = { "480p": 480, "720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160 };
const TRANSITIONS: [string, string][] = [
  ["none", "None (hard cut)"], ["fade", "Crossfade"], ["fadeblack", "Fade through black"],
  ["fadewhite", "Fade through white"], ["dissolve", "Dissolve"], ["wipeleft", "Wipe left"],
  ["slideleft", "Slide left"], ["circleopen", "Circle open"], ["zoomin", "Zoom in"],
];

export function orderFiles(files: InputFile[], order: string): InputFile[] {
  const list = [...files];
  if (order === "name") return list.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  if (order === "date") return list.sort((a, b) => a.file.lastModified - b.file.lastModified);
  if (order === "reverse") return list.reverse();
  if (order === "random") {
    for (let i = list.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [list[i], list[j]] = [list[j], list[i]];
    }
  }
  return list;
}

export function outputSize(o: Values, sizes: [number, number][]): [number, number] {
  let w: number;
  let h: number;
  if (o.resolution === "custom") {
    w = Number(o.width);
    h = Number(o.height);
  } else if (String(o.resolution) in PRESET_HEIGHTS) {
    const short = PRESET_HEIGHTS[String(o.resolution)];
    if (o.orientation === "portrait") [w, h] = [short, Math.round((short * 16) / 9)];
    else if (o.orientation === "square") [w, h] = [short, short];
    else [w, h] = [Math.round((short * 16) / 9), short];
  } else if (o.resolution === "largest") {
    [w, h] = sizes.reduce((a, b) => (b[0] * b[1] > a[0] * a[1] ? b : a));
  } else {
    [w, h] = sizes[0];
  }
  return [Math.max(2, w - (w % 2)), Math.max(2, h - (h % 2))];
}

export const slideshowConverter: Converter = {
  id: "slideshow",
  family: "image",
  options: [
    opt.slider("seconds_per_image", "Seconds per image", 2, 0.1, 10, { step: 0.1, decimals: 1, unit: "s" }),
    opt.choice("order", "Image order", "selected", [["selected", "As listed (drag to reorder)"], ["name", "By file name"], ["date", "By date modified"], ["reverse", "Reverse of list"], ["random", "Shuffle"]]),
    opt.choice("resolution", "Resolution", "first", [["first", "Match first image"], ["largest", "Match largest image"], ["480p", "480p"], ["720p", "720p (HD)"], ["1080p", "1080p (Full HD)"], ["1440p", "1440p"], ["4k", "4K"], ["custom", "Custom…"]]),
    opt.choice("orientation", "Orientation", "landscape", [["landscape", "Landscape (16:9)"], ["portrait", "Portrait (9:16)"], ["square", "Square (1:1)"]], { showIf: (v) => String(v.resolution) in PRESET_HEIGHTS }),
    opt.int("width", "Width", 1920, { min: 16, max: 8192, unit: "px", showIf: (v) => v.resolution === "custom" }),
    opt.int("height", "Height", 1080, { min: 16, max: 8192, unit: "px", showIf: (v) => v.resolution === "custom" }),
    opt.choice("fit", "Fit images", "letterbox", [["letterbox", "Fit (add bars)"], ["crop", "Fill (crop edges)"], ["stretch", "Stretch"]]),
    opt.color("background", "Bar / background colour", "#000000"),
    opt.choice("transition", "Transition", "none", TRANSITIONS, { targets: VIDEO_ONLY }),
    opt.slider("transition_duration", "Transition length", 0.5, 0.1, 3, { step: 0.1, decimals: 1, unit: "s", targets: VIDEO_ONLY, showIf: (v) => v.transition !== "none" }),
    opt.slider("fps", "Frame rate", 30, 1, 60, { unit: "fps", targets: VIDEO_ONLY }),
    opt.choice("video_codec", "Video codec", "auto", [["auto", "Auto (best for format)"], ...(["h264", "vp8"] as const).map((k) => [k, VIDEO_CODEC_LABELS[k]] as [string, string])], { targets: VIDEO_ONLY }),
    opt.slider("crf", "Quality (CRF)", 20, 0, 51, { targets: VIDEO_ONLY, help: "Lower = better quality, bigger file." }),
    opt.file("audio_file", "Background music (optional)", { targets: VIDEO_ONLY, accept: "audio/*" }),
    opt.bool("audio_fade_out", "Fade music out at the end", true, { targets: VIDEO_ONLY, showIf: (v) => Boolean(v.audio_file) }),
    opt.choice("loop", "Loop", 0, [[0, "Forever"], [-1, "Play once"], [1, "Repeat 1×"], [3, "Repeat 3×"]], { targets: ["slideshow:gif"] }),
  ],
  targets(files) {
    const t = CONTAINER_TARGETS.map((c) => ({ id: `slideshow:${c}`, label: c.toUpperCase(), ext: c, group: "Slideshow video", converterId: "slideshow", hint: "Turn the images into a video" }));
    if (files.length > 1) t.push({ id: "slideshow:gif", label: "GIF", ext: "gif", group: "Slideshow video", converterId: "slideshow", hint: "Animated GIF slideshow" });
    return t;
  },
  async run(files, target, o, ctx) {
    const { getMagick, copy } = await import("../engines/magick");
    const M = await getMagick((m) => ctx.progress(0, m));
    const ordered = orderFiles(files, String(o.order));
    const d = Number(o.seconds_per_image);

    // 1) Decode + orient every image once, find the output size, then fit each onto a canvas.
    const raw: Uint8Array[] = [];
    const sizes: [number, number][] = [];
    for (const [i, f] of ordered.entries()) {
      ctx.progress(0.15 * (i / ordered.length), `Reading images (${i + 1}/${ordered.length})`);
      const bytes = new Uint8Array(await f.file.arrayBuffer());
      try {
        M.ImageMagick.read(bytes, (img) => {
          img.autoOrient();
          sizes.push([img.width, img.height]);
          raw.push(img.write(M.MagickFormat.Png, copy));
        });
      } catch (e) {
        throw new ConversionError(`Couldn't read “${f.name}” as an image.`, String(e));
      }
    }
    const [w, h] = outputSize(o, sizes);
    const frames = raw.map((bytes, i) => {
      ctx.progress(0.15 + 0.15 * (i / raw.length), `Preparing images (${i + 1}/${raw.length})`);
      return M.ImageMagick.read(bytes, (img) => {
        const bg = new M.MagickColor(String(o.background));
        img.backgroundColor = bg;
        img.alpha(M.AlphaAction.Remove);
        const g = new M.MagickGeometry(w, h);
        if (o.fit === "stretch") g.ignoreAspectRatio = true;
        if (o.fit === "crop") g.fillArea = true;
        img.resize(g);
        img.extent(new M.MagickGeometry(w, h), M.Gravity.Center, bg);
        return img.write(M.MagickFormat.Png, copy);
      });
    });
    const stem = ordered.length === 1 ? baseName(ordered[0].name) : `${baseName(ordered[0].name)} slideshow`;

    if (target.ext === "gif") {
      ctx.progress(0.5, "Building GIF");
      // GIF loop count: 0 = forever, otherwise the total number of plays.
      const loop = Number(o.loop);
      const plays = loop === 0 ? 0 : loop < 0 ? 1 : loop + 1;
      const col = M.MagickImageCollection.create();
      let data: Uint8Array;
      try {
        for (const fr of frames) {
          const img = M.MagickImage.create();
          img.read(fr);
          img.animationDelay = Math.round(d * 100); // centiseconds
          img.animationIterations = plays;
          col.push(img);
        }
        data = col.write(M.MagickFormat.Gif, copy);
      } finally {
        col.dispose();
      }
      ctx.progress(1, "Done");
      return [{ name: `${stem}.gif`, data, type: "image/gif" }];
    }

    // 2) Encode with FFmpeg.
    const { getFFmpeg } = await import("../engines/ffmpeg");
    const ff = await getFFmpeg((m) => ctx.progress(0.3, m));
    const container = target.ext;
    const codec = resolveVideoCodec(container, String(o.video_codec));
    const enc = pick(ff.encoders, VIDEO_ENCODERS[codec], VIDEO_CODEC_LABELS[codec]);
    await ff.ff.createDir("/slides").catch(() => {});
    for (const [i, fr] of frames.entries()) await ff.ff.writeFile(`/slides/img${String(i).padStart(5, "0")}.png`, fr);
    const n = frames.length;
    const fps = Number(o.fps);
    const useTransition = o.transition !== "none" && n > 1;
    const td = useTransition ? Math.min(Number(o.transition_duration), d * 0.9) : 0;
    const args: string[] = [];
    let graph: string;
    let total: number;
    let vmap: string;
    if (useTransition) {
      for (let i = 0; i < n; i++) args.push("-loop", "1", "-framerate", String(fps), "-t", (d + td).toFixed(3), "-i", `/slides/img${String(i).padStart(5, "0")}.png`);
      const parts = [...Array(n).keys()].map((i) => `[${i}:v]fps=${fps},settb=AVTB,format=yuv420p[s${i}]`);
      let prev = "s0";
      for (let i = 1; i < n; i++) {
        parts.push(`[${prev}][s${i}]xfade=transition=${o.transition}:duration=${td.toFixed(3)}:offset=${(i * d).toFixed(3)}[x${i}]`);
        prev = `x${i}`;
      }
      graph = parts.join(";");
      total = n * d + td;
      vmap = `[${prev}]`;
    } else {
      const lines: string[] = [];
      for (let i = 0; i < n; i++) lines.push(`file 'img${String(i).padStart(5, "0")}.png'`, `duration ${d.toFixed(3)}`);
      lines.push(`file 'img${String(n - 1).padStart(5, "0")}.png'`);
      await ff.ff.writeFile("/slides/list.txt", lines.join("\n") + "\n");
      args.push("-f", "concat", "-safe", "0", "-i", "/slides/list.txt");
      graph = `[0:v]fps=${fps},format=yuv420p[v]`;
      total = n * d;
      vmap = "[v]";
    }
    let release: (() => Promise<void>) | null = null;
    if (o.audio_file instanceof File) {
      const mounted = await ff.mount(o.audio_file);
      release = mounted.release;
      const ai = useTransition ? n : 1;
      args.push("-i", mounted.path);
      let af = `[${ai}:a]atrim=0:${total.toFixed(3)}`;
      if (o.audio_fade_out && total > 2) {
        const fade = Math.min(2, total / 4);
        af += `,afade=t=out:st=${(total - fade).toFixed(3)}:d=${fade.toFixed(3)}`;
      }
      graph += `;${af}[a]`;
    }
    args.push("-filter_complex", graph, "-map", vmap);
    if (release) {
      const acodec = CONTAINERS[container][1];
      args.push("-map", "[a]", "-c:a", pick(ff.encoders, AUDIO_ENCODERS[acodec], acodec), "-b:a", "192k");
    }
    args.push("-c:v", enc, ...qualityArgs(enc, Number(o.crf), "veryfast", null), "-pix_fmt", "yuv420p", "-r", String(fps), "-t", total.toFixed(3));
    if (container === "mp4" || container === "mov") args.push("-movflags", "+faststart");
    const dest = `/slideshow.${container}`;
    try {
      await ff.run([...args, dest], total, (p) => ctx.progress(0.3 + 0.7 * p, "Rendering video"), "Couldn't create the slideshow video.");
    } finally {
      await release?.();
      for (const f of await ff.listFiles("/slides")) await ff.ff.deleteFile(`/slides/${f}`).catch(() => {});
      await ff.ff.deleteDir("/slides").catch(() => {});
    }
    ctx.progress(1, "Done");
    return [{ name: `${stem}.${container}`, data: await ff.readAndDelete(dest), type: MIME[container] }];
  },
};
