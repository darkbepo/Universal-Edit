"""Archives: convert between ZIP / 7Z / TAR(.gz/.xz/.bz2), read RAR, extract, and pack any files."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import archive

LABELS = {
    "zip": "ZIP",
    "7z": "7Z",
    "tar": "TAR",
    "tar.gz": "TAR.GZ",
    "tar.xz": "TAR.XZ",
    "tar.bz2": "TAR.BZ2",
}
PACK = ("zip", "7z", "tar.gz")  # offered for non-archive files
ENCRYPTED_TARGETS = ("zip", "7z", "pack:zip", "pack:7z")


@dataclass
class ArchiveOptions:
    level: str = opt(
        "normal",
        "Compression",
        choices=[
            ("store", "None (just store the files)"),
            ("fast", "Fast"),
            ("normal", "Normal"),
            ("maximum", "Maximum (slowest, smallest)"),
        ],
    )
    password: str = opt(
        "",
        "Protect with a password",
        kind="password",
        targets=ENCRYPTED_TARGETS,
        help="Optional. Encrypts the files with AES-256. Empty = no password.",
    )
    encrypt_names: bool = opt(
        True,
        "Also hide the file names",
        targets=("7z", "pack:7z"),
        show_if=lambda v: bool(v["password"]),
    )
    source_password: str = opt(
        "",
        "Password of the archive you added",
        kind="password",
        help="Only needed when the archive you're converting or extracting is password-protected.",
        targets=("archive:extract", *archive.WRITABLE),
    )
    archive_name: str = opt(
        "",
        "Archive name",
        targets=tuple(f"pack:{e}" for e in PACK),
        help="Without extension. Empty = named after the first file.",
    )


class ArchiveConverter(Converter):
    """Archive → other archive format, or → extracted files."""

    family = MediaFamily.ARCHIVE
    options_cls = ArchiveOptions

    def targets(self, files: list[DetectedFile]):
        single = files[0].ext if len(files) == 1 else ""
        out = [
            self.target(
                "archive:extract", "Extract files", "", "Archive", "Unpack everything into a folder"
            )
        ]
        for ext in archive.WRITABLE:
            if ext != single:
                out.append(
                    self.target(ext, LABELS[ext], ext, "Archive", f"Repack as {LABELS[ext]}")
                )
        return out

    def run(self, job: ConversionJob) -> list[Path]:
        o: ArchiveOptions = job.options
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / n, f"Opening {f.name} ({i + 1}/{n})")
            if job.target.id == "archive:extract":
                folder = _unique_dir(job.output_dir_for(f.path), f.stem)
                try:
                    files = archive.extract(
                        f.path,
                        f.ext,
                        folder,
                        o.source_password,
                        job.sub_progress(i / n, (i + 1) / n),
                        job.cancel_event,
                    )
                except BaseException:
                    _remove_tree(folder)
                    raise
                if not files:
                    _remove_tree(folder)
                    raise ConversionError(f"“{f.name}” doesn't contain any files.")
                outputs.append(folder)
                continue
            with tempfile.TemporaryDirectory(prefix="umc-archive-") as tmp:
                root = Path(tmp)
                files = archive.extract(
                    f.path,
                    f.ext,
                    root,
                    o.source_password,
                    job.sub_progress(i / n, (i + 0.5) / n),
                    job.cancel_event,
                )
                if not files:
                    raise ConversionError(f"“{f.name}” doesn't contain any files.")
                entries = [(p, p.relative_to(root).as_posix()) for p in sorted(files)]
                dest = unique_path(job.output_dir_for(f.path), f.stem, job.target.ext)
                job.report((i + 0.5) / n, f"Writing {dest.name}")
                archive.create(
                    entries,
                    dest,
                    job.target.ext,
                    o.level,
                    o.password,
                    o.encrypt_names,
                    job.sub_progress((i + 0.5) / n, (i + 1) / n),
                    job.cancel_event,
                )
                outputs.append(dest)
        job.report(1.0, "Done")
        return outputs


class PackConverter(Converter):
    """Any files (of any kind, even mixed) → one archive."""

    family = MediaFamily.DOCUMENT  # unused: ``any_family`` offers it for every kind of input
    any_family = True
    options_cls = ArchiveOptions

    def targets(self, files: list[DetectedFile]):
        if all(f.family == MediaFamily.ARCHIVE for f in files):
            return []
        return [
            self.target(
                f"pack:{ext}",
                LABELS[ext],
                ext,
                "Archive",
                f"Put the files into one {LABELS[ext]} archive",
            )
            for ext in PACK
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: ArchiveOptions = job.options
        files = list(job.files)
        first = files[0]
        stem = o.archive_name.strip() or (
            first.path.stem if len(files) == 1 else f"{first.path.stem} + {len(files) - 1} more"
        )
        stem = "".join(ch for ch in stem if ch not in '<>:"/\\|?*').strip() or "archive"
        names: set[str] = set()
        entries = []
        for f in files:
            name, k = f.name, 1
            while name.lower() in names:  # same file name from two folders
                name = f"{f.path.stem} ({k}){f.path.suffix}"
                k += 1
            names.add(name.lower())
            entries.append((f.path, name))
        dest = unique_path(job.output_dir_for(first.path), stem, job.target.ext)
        job.report(0.0, f"Writing {dest.name}")
        archive.create(
            entries,
            dest,
            job.target.ext,
            o.level,
            o.password,
            o.encrypt_names,
            job.sub_progress(0, 1),
            job.cancel_event,
        )
        job.report(1.0, "Done")
        return [dest]


def _unique_dir(parent: Path, stem: str) -> Path:
    candidate, k = parent / stem, 1
    while candidate.exists():
        candidate = parent / f"{stem} ({k})"
        k += 1
    return candidate


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
