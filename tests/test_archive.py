import tarfile
import zipfile
from pathlib import Path

import pytest

from tests.conftest import convert
from umc.core.detect import MediaFamily, detect
from umc.core.errors import ConversionError
from umc.core.registry import targets_for
from umc.tools import archive

FIXTURES = Path(__file__).parent / "fixtures"
needs_bsdtar = pytest.mark.skipif(archive.bsdtar_path() is None, reason="bsdtar not installed")


@pytest.fixture(scope="module")
def sample_zip(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("arc")
    p = d / "photos 2026.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", "hello ÆØÅ\n")
        zf.writestr("sub/deep/data.bin", bytes(range(256)) * 40)
        zf.writestr("empty-dir/", "")
    return p


def _names(path: Path, password: str = "") -> set[str]:
    ext = detect(path).ext
    return set(archive.list_names(path, ext, password))


def test_detects_archives_and_double_extensions(tmp_path, sample_zip):
    f = tmp_path / "backup.TAR.GZ"
    with tarfile.open(f, "w:gz") as tf:
        tf.add(sample_zip, arcname="x.zip")
    d = detect(f)
    assert (d.family, d.ext, d.stem) == (MediaFamily.ARCHIVE, "tar.gz", "backup")
    assert detect(sample_zip).family == MediaFamily.ARCHIVE
    (tmp_path / "noext").write_bytes(sample_zip.read_bytes())
    assert detect(tmp_path / "noext").ext == "zip"


@pytest.mark.parametrize("fmt", ["7z", "tar", "tar.gz", "tar.xz", "tar.bz2"])
def test_zip_converts_to_every_format(sample_zip, tmp_path, fmt):
    [out] = convert([sample_zip], fmt, tmp_path)
    assert out.name == f"photos 2026.{fmt}"
    assert _names(out) == {"readme.txt", "sub/deep/data.bin"}
    [back] = convert([out], "zip", tmp_path / "back")
    with zipfile.ZipFile(back) as zf:
        assert zf.read("sub/deep/data.bin") == bytes(range(256)) * 40
        assert zf.read("readme.txt").decode() == "hello ÆØÅ\n"


def test_extract_into_folder(sample_zip, tmp_path):
    [folder] = convert([sample_zip], "archive:extract", tmp_path)
    assert folder.is_dir() and folder.name == "photos 2026"
    assert (folder / "sub" / "deep" / "data.bin").stat().st_size == 256 * 40
    [again] = convert([sample_zip], "archive:extract", tmp_path)
    assert again.name == "photos 2026 (1)"  # never merges into an existing folder


def test_store_vs_maximum(sample_zip, tmp_path):
    [stored] = convert([sample_zip], "7z", tmp_path / "s", level="store")
    [small] = convert([sample_zip], "7z", tmp_path / "m", level="maximum")
    assert small.stat().st_size < stored.stat().st_size


@pytest.mark.parametrize("fmt", ["zip", "7z"])
def test_password_round_trip(sample_zip, tmp_path, fmt):
    src = sample_zip if fmt == "7z" else convert([sample_zip], "tar", tmp_path / "t")[0]
    [locked] = convert([src], fmt, tmp_path, password="s3cret ÆØÅ")
    with pytest.raises(ConversionError, match="password"):
        convert([locked], "archive:extract", tmp_path / "no")
    [folder] = convert([locked], "archive:extract", tmp_path / "yes", source_password="s3cret ÆØÅ")
    assert (folder / "readme.txt").read_text(encoding="utf-8") == "hello ÆØÅ\n"


def test_zip_password_uses_aes(sample_zip, tmp_path):
    src = convert([sample_zip], "tar", tmp_path / "t")[0]
    [locked] = convert([src], "zip", tmp_path, password="pw")
    with zipfile.ZipFile(locked) as zf:
        info = zf.getinfo("readme.txt")
        assert info.flag_bits & 0x1 and info.compress_type == 99  # 99 = WinZip AES


def test_7z_hides_file_names(sample_zip, tmp_path):
    [locked] = convert([sample_zip], "7z", tmp_path, password="pw", encrypt_names=True)
    assert b"readme" not in locked.read_bytes()
    with pytest.raises(ConversionError):
        convert([locked], "archive:extract", tmp_path / "x")


def test_unsafe_entries_are_not_written_outside(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escaped.txt", "x")
        zf.writestr("/abs.txt", "x")
        link = zipfile.ZipInfo("link")
        link.external_attr = 0o120777 << 16
        zf.writestr(link, "/etc/passwd")
        zf.writestr("ok.txt", "fine")
    [folder] = convert([evil], "archive:extract", tmp_path / "out")
    assert (
        not (tmp_path / "out" / "escaped.txt").exists() and not (tmp_path / "escaped.txt").exists()
    )
    assert sorted(p.name for p in folder.rglob("*") if p.is_file()) == ["abs.txt", "ok.txt"]
    assert not (folder / "link").exists()


@needs_bsdtar
@pytest.mark.parametrize("name", ["sample-rar4.rar", "sample-rar5.rar"])
def test_rar_can_be_read(tmp_path, name):
    files = targets_for([detect(FIXTURES / name)])
    assert "rar" not in {t.id for t in files}  # read-only: RAR can't be written
    [out] = convert([FIXTURES / name], "zip", tmp_path)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert names == ({"test.txt", "testdir/test.txt"} if "rar4" in name else {"test.bin"})


def test_pack_any_files(media, tmp_path):
    files = [media / "img0.png", media / "doc.md", media / "img1.png"]
    [out] = convert(files, "pack:zip", tmp_path)
    assert out.name == "img0 + 2 more.zip"
    assert _names(out) == {"img0.png", "doc.md", "img1.png"}
    [named] = convert(files[:1], "pack:tar.gz", tmp_path, archive_name="My: backup")
    assert named.name == "My backup.tar.gz"


def test_pack_targets_not_offered_for_archives(sample_zip):
    ids = {t.id for t in targets_for([detect(sample_zip)])}
    assert "archive:extract" in ids and not any(i.startswith("pack:") for i in ids)
