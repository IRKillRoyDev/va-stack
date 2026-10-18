"""safe_path on Windows path forms: drive letters, UNC, junctions.

The portable cases run on every OS. The real-junction, second-drive and UNC
cases need Windows and are skipped elsewhere with the reason; the windows CI
job runs this file with ``-rs`` so its log shows what ran and what skipped.
Everything is created under tmp_path; no subprocess, no network.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import string
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sp = _load("safe_path")
fe = _load("fill_engine")

WINDOWS = os.name == "nt"
only_windows = pytest.mark.skipif(
    not WINDOWS, reason="NTFS junctions/drives/UNC exist only on Windows"
)

DRIVE_FORMS = ["C:x", "C:\\x", "c:/x", "D:/x", "Z:", "z:\\escape\\x", "C:..\\x"]
UNC_FORMS = [
    "\\\\server\\share\\x",
    "//server/share/x",
    "\\\\localhost\\C$\\x",
    "//localhost/C$/x",
    "\\\\?\\C:\\x",
    "//?/C:/x",
    "\\\\?\\UNC\\server\\share\\x",
    "\\\\.\\pipe\\x",
    "//./pipe/x",
    "\\\\.\\C:\\x",
]


@pytest.fixture
def bases(tmp_path, monkeypatch):
    home = tmp_path / "home"
    data = home / "AppData" / "Roaming" / "va-stack"
    data.mkdir(parents=True)
    monkeypatch.setattr(sp, "_default_bases", lambda: [data, home])
    return home, data


def _files(base: Path) -> set[Path]:
    return {p for p in base.rglob("*") if p.is_file()}


# ---- portable: drive letters and UNC as names and destinations ----
@pytest.mark.parametrize("dest", DRIVE_FORMS + UNC_FORMS)
def test_drive_and_unc_forms_refused_as_name(dest):
    with pytest.raises(sp.SafePathError):
        sp.check_name(dest)


@pytest.mark.parametrize("dest", DRIVE_FORMS + UNC_FORMS)
def test_drive_and_unc_forms_refused_as_destination(tmp_path, dest):
    root = tmp_path / "root"
    before = _files(tmp_path)
    with pytest.raises(sp.SafePathError):
        sp.confine(root, dest)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("stem", DRIVE_FORMS + UNC_FORMS)
def test_drive_and_unc_stems_refused_by_save_new(tmp_path, stem):
    folder = tmp_path / "Completed forms"
    folder.mkdir()
    with pytest.raises(fe.FillError):
        fe.save_new(folder, stem, "completed", b"%PDF-fake")
    assert _files(tmp_path) == set()


def _fake_junction(monkeypatch, link: Path) -> None:
    """Make link look like a junction: islink() False, isjunction() True."""
    real_islink = os.path.islink
    target = os.path.abspath(link)
    monkeypatch.setattr(
        os.path,
        "islink",
        lambda p: False if os.path.abspath(p) == target else real_islink(p),
    )
    monkeypatch.setattr(
        os.path, "isjunction", lambda p: os.path.abspath(p) == target, raising=False
    )


@pytest.mark.skipif(WINDOWS, reason="Windows runs the real-junction cases below")
def test_link_islink_misses_is_still_checked(tmp_path, bases, monkeypatch):
    """A junction is not islink(); the base check must still see it."""
    _home, data = bases
    outside = tmp_path / "outside"
    outside.mkdir()
    link = data / "Completed forms"
    link.symlink_to(outside, target_is_directory=True)
    _fake_junction(monkeypatch, link)
    with pytest.raises(sp.SafePathError):
        sp.pin_root(link)


def test_reparse_tag_fallback_without_isjunction(tmp_path, monkeypatch):
    """Python < 3.12 has no os.path.isjunction: the reparse tag decides."""
    real_lstat = os.lstat
    plain = tmp_path / "plain"
    plain.mkdir()
    marked = tmp_path / "marked"
    marked.mkdir()

    class _St:
        st_mode = stat.S_IFDIR | 0o755
        st_reparse_tag = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT

    monkeypatch.delattr(os.path, "isjunction", raising=False)
    monkeypatch.setattr(
        os,
        "lstat",
        lambda p, *a, **k: _St() if Path(p) == marked else real_lstat(p, *a, **k),
    )
    assert sp._is_link(marked)
    assert not sp._is_link(plain)


# ---- Windows only: real junctions ----
def _junction(target: Path, link: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


@only_windows
def test_junction_completed_folder_outside_base_refused(tmp_path, bases):
    _home, data = bases
    outside = tmp_path / "outside"
    outside.mkdir()
    link = data / "Completed forms"
    _junction(outside, link)
    assert not os.path.islink(link)
    with pytest.raises(sp.SafePathError):
        sp.pin_root(link)
    with pytest.raises(fe.FillError):
        fe.save_new(link, "doc", "completed", b"%PDF-fake")
    assert _files(outside) == set()


@only_windows
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_junction_parent_below_base_escaping_refused(tmp_path, bases, depth):
    _home, data = bases
    outside = tmp_path / "outside"
    outside.mkdir()
    link = data / "lnk"
    _junction(outside, link)
    folder = link.joinpath(*[f"d{i}" for i in range(depth)])
    with pytest.raises(sp.SafePathError):
        sp.pin_root(folder)
    assert _files(outside) == set()


@only_windows
def test_junction_inside_base_allowed(tmp_path, bases):
    _home, data = bases
    real = data / "real"
    real.mkdir()
    link = data / "Completed forms"
    _junction(real, link)
    path = sp.write_new(sp.pin_root(link), "doc.pdf", b"x")
    assert (real / "doc.pdf").read_bytes() == b"x"
    assert Path(path).resolve() == (real / "doc.pdf").resolve()


@only_windows
def test_junction_subfolder_in_destination_refused(tmp_path):
    root = tmp_path / "exports"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _junction(outside, root / "sub")
    with pytest.raises(sp.SafePathError):
        sp.confine(root, "sub/head.json")
    assert _files(outside) == set()


@only_windows
def test_folder_swapped_for_junction_after_pin_refused(tmp_path, bases):
    _home, data = bases
    folder = data / "Completed forms"
    pinned = sp.pin_root(folder)
    outside = tmp_path / "outside"
    outside.mkdir()
    folder.rmdir()
    _junction(outside, folder)
    for write in (sp.write_new, sp.write_replace):
        with pytest.raises(sp.SafePathError):
            write(pinned, "doc.pdf", b"x")
    with pytest.raises(sp.SafePathError):
        sp.append_text(pinned, "log.jsonl", "x")
    assert _files(outside) == set()


# ---- portable: the no-dir_fd path Windows takes ----
@pytest.fixture
def no_dirfd(monkeypatch):
    """Run safe_path the way it runs on Windows (no dir_fd, no O_NOFOLLOW)."""
    monkeypatch.setattr(sp, "_HAVE_DIRFD", False)
    monkeypatch.setattr(sp, "_NOFOLLOW", 0)


@pytest.mark.parametrize("dangling", [False, True])
def test_no_dirfd_link_at_name_is_a_taken_name(tmp_path, no_dirfd, dangling):
    folder = tmp_path / "c"
    folder.mkdir()
    victim = tmp_path / "victim.pdf"
    if not dangling:
        victim.write_bytes(b"original")
    (folder / "doc completed.pdf").symlink_to(victim)
    out = fe.save_new(folder, "doc", "completed", b"%PDF-fake")
    assert out.name == "doc completed-2.pdf" and out.read_bytes() == b"%PDF-fake"
    assert (folder / "doc completed.pdf").is_symlink()
    if dangling:
        assert not victim.exists()
    else:
        assert victim.read_bytes() == b"original"


def test_no_dirfd_link_at_append_name_refused(tmp_path, no_dirfd):
    folder = tmp_path / "data"
    folder.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    (folder / "log.jsonl").symlink_to(victim)
    with pytest.raises(sp.SafePathError):
        sp.append_text(sp.pin_root(folder, bases=[]), "log.jsonl", "x")
    assert victim.read_text() == "original"


# ---- Windows only: real drives and UNC ----
@only_windows
def test_absolute_native_destination_inside_root_allowed(tmp_path):
    root = tmp_path / "exports"
    (root / "sub").mkdir(parents=True)
    for dest in (
        root / "head.json",
        str(root / "sub" / "head.json"),
        root.as_posix() + "/b.json",
    ):
        folder, name = sp.confine(root, dest)
        assert folder.path.is_relative_to(root.resolve())
        sp.write_replace(folder, name, b"x")
    assert (root / "head.json").read_bytes() == b"x"
    assert (root / "sub" / "head.json").read_bytes() == b"x"


@only_windows
@pytest.mark.parametrize(
    "rel", ["..\\x.json", "sub\\..\\..\\x.json", "..\\exports2\\x.json"]
)
def test_absolute_native_destination_escaping_root_refused(tmp_path, rel):
    root = tmp_path / "exports"
    (root / "sub").mkdir(parents=True)
    (tmp_path / "exports2").mkdir()
    with pytest.raises(sp.SafePathError):
        sp.confine(root, str(root) + "\\" + rel)
    assert _files(tmp_path) == set()


@only_windows
def test_other_drive_destination_refused(tmp_path):
    here = Path(tmp_path).drive[:1].upper()
    others = [
        d for d in string.ascii_uppercase if d != here and os.path.exists(f"{d}:\\")
    ]
    if not others:
        pytest.skip("this machine has no second drive letter")
    root = tmp_path / "exports"
    for form in (
        f"{others[0]}:\\x.json",
        f"{others[0]}:/x.json",
        f"{others[0]}:x.json",
    ):
        with pytest.raises(sp.SafePathError):
            sp.confine(root, form)


def _unc_alias(p: Path) -> str:
    drive, rest = os.path.splitdrive(str(p))
    return f"\\\\localhost\\{drive[0]}${rest}"


@only_windows
def test_unc_alias_of_root_pins_and_unc_destination_refused(tmp_path):
    root = tmp_path / "exports"
    root.mkdir()
    unc = _unc_alias(root)
    if not os.path.isdir(unc):
        pytest.skip("the administrative share \\\\localhost\\C$ is not reachable here")
    pinned = sp.pin_root(unc, bases=[], create=False)
    sp.write_new(pinned, "doc.json", b"x")
    assert (root / "doc.json").read_bytes() == b"x"
    with pytest.raises(sp.SafePathError):
        sp.confine(root, unc + "\\other.json")
    with pytest.raises(sp.SafePathError):
        sp.confine(root, unc.replace("\\", "/") + "/other.json")


# ---- condition 1: a link at the create-new name is never written through ----
def _write_opens(monkeypatch):
    """Record every successful os.open for writing (path, dir_fd given)."""
    seen = []
    real_open = os.open
    write = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT

    def spy(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if flags & write:
            seen.append(os.path.basename(os.fspath(path)))
        return fd

    monkeypatch.setattr(os, "open", spy)
    return seen


def _stamp(p: Path):
    st = os.stat(p)
    return p.read_bytes(), st.st_mtime_ns, st.st_size


@pytest.mark.parametrize("dirfd", [True, False], ids=["dir_fd", "no_dir_fd"])
def test_symlink_at_new_name_skipped_never_opened(tmp_path, monkeypatch, dirfd):
    if not dirfd:
        monkeypatch.setattr(sp, "_HAVE_DIRFD", False)
        monkeypatch.setattr(sp, "_NOFOLLOW", 0)
    elif not sp._HAVE_DIRFD:
        pytest.skip("this OS has no dir_fd for os.open (Windows)")
    folder = tmp_path / "c"
    folder.mkdir()
    victim = tmp_path / "victim.pdf"
    victim.write_bytes(b"original")
    os.utime(victim, ns=(1_000_000_000, 1_000_000_000))
    before = _stamp(victim)
    link = folder / "doc completed.pdf"
    link.symlink_to(victim)
    opened = _write_opens(monkeypatch)
    out = fe.save_new(folder, "doc", "completed", b"%PDF-fake")
    assert out == folder.resolve() / "doc completed-2.pdf"
    assert out.read_bytes() == b"%PDF-fake"
    assert _stamp(victim) == before
    assert link.is_symlink() and Path(os.path.realpath(link)) == victim.resolve()
    assert opened == ["doc completed-2.pdf"], opened


@only_windows
def test_junction_at_new_name_skipped_never_opened(tmp_path, monkeypatch):
    folder = tmp_path / "c"
    folder.mkdir()
    victim_dir = tmp_path / "victim"
    victim_dir.mkdir()
    keep = victim_dir / "keep.txt"
    keep.write_bytes(b"original")
    before = (_stamp(keep), sorted(os.listdir(victim_dir)))
    _junction(victim_dir, folder / "doc completed.pdf")
    opened = _write_opens(monkeypatch)
    out = fe.save_new(folder, "doc", "completed", b"%PDF-fake")
    assert out == folder.resolve() / "doc completed-2.pdf"
    assert (_stamp(keep), sorted(os.listdir(victim_dir))) == before
    assert opened == ["doc completed-2.pdf"], opened


# ---- condition 2: X:\ accepted only on the same drive, inside the root ----
@only_windows
@pytest.mark.parametrize("hop", ["junction", "dotdot", "junction-dotdot"])
def test_native_path_resolving_outside_root_refused(tmp_path, hop):
    root = tmp_path / "exports"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _junction(outside, root / "jn")
    dest = {
        "junction": str(root / "jn" / "x.json"),
        "dotdot": str(root) + "\\..\\outside\\x.json",
        "junction-dotdot": str(root / "jn") + "\\..\\..\\outside\\x.json",
    }[hop]
    with pytest.raises(sp.SafePathError):
        sp.confine(root, dest)
    assert _files(tmp_path) == set()


@only_windows
def test_native_path_same_drive_inside_root_accepted(tmp_path):
    root = tmp_path / "exports"
    (root / "sub").mkdir(parents=True)
    folder, name = sp.confine(root, str(root / "sub" / "x.json"))
    assert folder.path.drive.upper() == Path(root).resolve().drive.upper()
    assert folder.path == (root / "sub").resolve() and name == "x.json"


@only_windows
def test_root_spelled_as_unc_device_or_drive_relative_refused(tmp_path):
    root = tmp_path / "exports"
    root.mkdir()
    native = str(root.resolve() / "x.json")
    drive, rest = os.path.splitdrive(native)
    for dest in (
        "\\\\?\\" + native,
        "\\\\.\\" + native,
        "//?/" + native.replace("\\", "/"),
        f"\\\\localhost\\{drive[0]}${rest}",
        f"{drive}x.json",
        f"{drive}{rest.lstrip(chr(92))}",
    ):
        with pytest.raises(sp.SafePathError):
            sp.confine(root, dest)
    assert _files(tmp_path) == set()
