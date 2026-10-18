"""One shared safe-path helper for every save/write the app makes.

Policy (r19, class 3): PIN-AND-RECHECK.

* A target folder is pinned once with ``pin_root``: its real location is
  resolved with ``strict=True`` and its identity (st_dev, st_ino) recorded.
  The configured folder itself may not be a symlink unless it sits under an
  expected base (the app data folder or the user's home) and its real path
  stays inside that same base; any parent below an expected base that is a
  symlink pointing outside that base is refused.
* Every write re-checks the pin: the configured folder must still resolve to
  the same real path and the same inode, and the file is then opened
  relative to a directory fd of the pinned folder (``dir_fd``) with
  ``O_NOFOLLOW``, so a folder swapped for a symlink after setup (TOCTOU), a
  symlink planted at the file name, or a parent swapped mid-write cannot
  redirect the write. New files use ``O_EXCL``.
* Names are single path segments: absolute paths, drive letters, UNC,
  NUL, backslashes, ``..``/``.`` and Unicode-normalized dot tricks are
  refused. ``confine`` maps a destination (relative, or absolute inside the
  root) to a pinned sub-folder plus one safe name, refusing anything that
  resolves outside the root.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
import sys
import unicodedata
from pathlib import Path, PureWindowsPath


class SafePathError(ValueError):
    """Raised when a path would escape its target folder."""


# Lookalike separators: not separators to the OS, but refused (fail closed)
# so no later tool or display can turn them into one.
_SLASHES = ("／", "∕", "⁄", "⧸", "＼", "﹨", "⧵", "\u29f8")
_DOTS = ("．", "。", "﹒", "\u2024", "\u2025", "\u2026", "\uff61")
_HAVE_DIRFD = os.open in getattr(os, "supports_dir_fd", set()) and hasattr(
    os, "O_DIRECTORY"
)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _as_text(value, what: str) -> str:
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if not isinstance(value, str):
        raise SafePathError(f"{what} must be a string or path")
    if "\x00" in value:
        raise SafePathError(f"{what} contains NUL")
    return value


def check_name(name) -> str:
    """Return name if it is one safe path segment, else raise SafePathError."""
    name = _as_text(name, "file name")
    if not name.strip():
        raise SafePathError("empty file name")
    if "\\" in name or "/" in name:
        raise SafePathError("file name may not contain a path separator")
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        raise SafePathError("file name looks like a drive letter")
    if name.startswith("~"):
        raise SafePathError("file name may not start with ~")
    if any(c in name for c in _SLASHES):
        raise SafePathError("file name contains a lookalike path separator")
    n = _nfkc(name)
    if "/" in n or "\\" in n or "\x00" in n:
        raise SafePathError("file name normalizes to a path separator")
    plain = n
    for d in _DOTS:
        plain = plain.replace(d, ".")
    if not plain.strip(". "):
        raise SafePathError("file name is a parent or self segment")
    if len(os.fsencode(name)) > 255:
        raise SafePathError("file name too long")
    return name


def check_relative(dest) -> list[str]:
    """Split a relative destination into safe segments (no '.', '..')."""
    text = _as_text(dest, "path")
    if "\\" in text:
        raise SafePathError("path contains backslash")
    if (
        text.startswith(("/", "~"))
        or PureWindowsPath(text).is_absolute()
        or PureWindowsPath(text).drive
    ):
        raise SafePathError("absolute path refused")
    segs = [s for s in text.split("/") if s not in ("", ".")]
    if not segs:
        raise SafePathError("empty relative path")
    return [check_name(s) for s in segs]


def _default_bases() -> list[Path]:
    bases = [Path.home()]
    try:
        mod = sys.modules.get("app_paths")
        if mod is None:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "app_paths", Path(__file__).resolve().parent / "app_paths.py"
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules["app_paths"] = mod
            spec.loader.exec_module(mod)
        bases.insert(0, Path(mod.data_dir()))
    except Exception:  # noqa: BLE001 - home alone is still a base
        pass
    return bases


def _under(p: Path, base: Path) -> bool:
    return p != base and p.is_relative_to(base)


def _is_link(p) -> bool:
    """True for a symlink or a Windows junction: both redirect a path.

    os.path.islink() is False for an NTFS junction, so it alone would let a
    junction folder escape the expected base.
    """
    if os.path.islink(p):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        return isjunction(p)
    try:  # Python < 3.12: read the reparse tag (0 or absent off Windows)
        tag = getattr(os.lstat(p), "st_reparse_tag", 0)
    except OSError:
        return False
    return tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def _check_symlinks(lex: Path, bases: list[Path]) -> None:
    """Refuse the folder/parents that are symlinks escaping an expected base."""
    pairs = []
    for b in bases:
        b_lex = Path(os.path.abspath(b))
        b_real = Path(os.path.realpath(b))
        pairs.append((b_lex, b_real))
    chain = [lex, *lex.parents]
    for i, comp in enumerate(chain):
        if not _is_link(comp):
            continue
        real = Path(os.path.realpath(comp))
        below = any(_under(comp, b) for pair in pairs for b in pair)
        inside = any(real.is_relative_to(b_real) for _b, b_real in pairs)
        if below and not inside:
            raise SafePathError(
                "folder or a parent is a symlink that leaves the expected base"
            )
        if i == 0 and not (below and inside):
            raise SafePathError("the target folder itself is a symlink")


class PinnedRoot:
    """A target folder whose real location and identity were fixed once."""

    __slots__ = ("configured", "path", "_dev", "_ino")

    def __init__(self, configured: Path, path: Path, st: os.stat_result):
        self.configured = configured
        self.path = path
        self._dev, self._ino = st.st_dev, st.st_ino

    def __fspath__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        return f"PinnedRoot({str(self.path)!r})"

    def _same(self, st: os.stat_result) -> bool:
        return (st.st_dev, st.st_ino) == (self._dev, self._ino)

    def recheck(self) -> None:
        """Raise SafePathError if the folder moved since it was pinned."""
        try:
            now = self.configured.resolve(strict=True)
            st = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise SafePathError("target folder is gone") from exc
        if now != self.path or not stat.S_ISDIR(st.st_mode) or not self._same(st):
            raise SafePathError("target folder changed since it was pinned")

    def _open_dir(self) -> int:
        self.recheck()
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW)
        try:
            if not self._same(os.fstat(fd)):
                raise SafePathError("target folder changed since it was pinned")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def child(self, name) -> Path:
        """Path of a safe name directly inside the pinned folder."""
        return self.path / check_name(name)


def pin_root(folder, *, bases=None, create: bool = True, mode=None) -> PinnedRoot:
    """Pin folder: resolve once (strict=True), check symlinks, record inode.

    mode (for example 0o700) applies when this call creates the folder
    itself; its parents and an existing folder keep their modes. On Windows
    the folder's access list applies instead.
    """
    if isinstance(folder, PinnedRoot):
        folder.recheck()
        return folder
    text = _as_text(folder, "folder")
    if not text:
        raise SafePathError("empty folder")
    lex = Path(os.path.abspath(text))
    bases = _default_bases() if bases is None else [Path(b) for b in bases]
    _check_symlinks(lex, bases)
    if create:
        if mode is None:
            lex.mkdir(parents=True, exist_ok=True)
        else:
            lex.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.mkdir(lex, mode)
            except FileExistsError:
                pass
    _check_symlinks(lex, bases)  # again: closes a swap during mkdir
    try:
        real = lex.resolve(strict=True)
        st = os.stat(real, follow_symlinks=False)
    except OSError as exc:
        raise SafePathError("target folder does not exist") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise SafePathError("target is not a folder")
    return PinnedRoot(lex, real, st)


def resolve_folder(folder, **kw) -> PinnedRoot:
    """Back-compat name for pin_root."""
    return pin_root(folder, **kw)


def confine(root, dest, *, create: bool = True) -> tuple[PinnedRoot, str]:
    """Map dest (relative, or absolute inside root) to (pinned folder, name)."""
    root = pin_root(root, create=create)
    text = _as_text(dest, "destination")
    if os.path.isabs(text) or PureWindowsPath(text).drive:
        if os.name == "nt":
            # Only a plain "X:\\..." path; UNC, device (\\?\, \\.\) and
            # drive-relative ("X:name") forms are refused.
            win = PureWindowsPath(text)
            drive = win.drive
            if not (len(drive) == 2 and drive[1] == ":" and win.root):
                raise SafePathError("absolute path refused")
            segments = win.parts[1:]
        else:
            if "\\" in text or PureWindowsPath(text).drive:
                raise SafePathError("absolute path refused")
            segments = text.split("/")
        if ".." in segments:
            raise SafePathError("path traversal refused")
        p = Path(os.path.abspath(text))
        name = check_name(p.name)
        try:
            parent_real = p.parent.resolve(strict=True)
        except OSError as exc:
            raise SafePathError("destination folder does not exist") from exc
        # Accepted only after resolution: same drive as the root, then inside it.
        if os.path.normcase(parent_real.drive) != os.path.normcase(root.path.drive):
            raise SafePathError("destination is on another drive")
        if not parent_real.is_relative_to(root.path):
            raise SafePathError("destination is outside the allowed root")
        segs = list(parent_real.relative_to(root.path).parts)
    else:
        parts = check_relative(text)
        segs, name = parts[:-1], parts[-1]
    sub = root
    for seg in segs:
        sub = pin_root(sub.path / seg, bases=[root.path], create=create)
        if not sub.path.is_relative_to(root.path):
            raise SafePathError("destination is outside the allowed root")
    return sub, name


def safe_path_in(folder, *parts: str) -> Path:
    """Path for parts under folder; refuses anything that would escape it."""
    if not parts:
        raise SafePathError("missing path parts")
    rel = "/".join(_as_text(p, "path part") for p in parts)
    sub, name = confine(folder, rel)
    target = sub.path / name
    if _is_link(target):
        raise SafePathError("destination is a symlink")
    return target


def _open_in(root: PinnedRoot, name: str, flags: int, mode: int = 0o600) -> int:
    name = check_name(name)
    flags |= _NOFOLLOW | _BINARY
    if _HAVE_DIRFD:
        dfd = root._open_dir()
        try:
            return os.open(name, flags, mode, dir_fd=dfd)
        finally:
            os.close(dfd)
    root.recheck()  # no dir_fd (Windows): path re-check plus O_EXCL
    target = root.path / name
    if _is_link(target):
        if flags & os.O_EXCL:  # as O_EXCL | O_NOFOLLOW: the name is taken
            raise FileExistsError(errno.EEXIST, "a link holds this name", str(target))
        raise SafePathError("destination is a symlink")
    return os.open(target, flags, mode)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]
    os.fsync(fd)


def write_new(root, name, data: bytes) -> Path:
    """Create name in root with data; FileExistsError if it is taken."""
    root = pin_root(root, create=False)
    try:
        fd = _open_in(root, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except OSError as exc:
        if isinstance(exc, FileExistsError):
            raise
        raise SafePathError(
            f"could not create the file safely: {exc.strerror}"
        ) from exc
    try:
        _write_all(fd, bytes(data))
    finally:
        os.close(fd)
    return root.path / name


def append_text(root, name, text: str, *, mode: int = 0o600) -> Path:
    """Append text to name in root (created with mode if missing); no links."""
    root = pin_root(root, create=False)
    try:
        # O_NONBLOCK: a FIFO planted at the name errors instead of hanging.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NONBLOCK", 0)
        fd = _open_in(root, name, flags, mode)
    except OSError as exc:
        raise SafePathError(f"could not open the file safely: {exc.strerror}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SafePathError("destination is not a regular file")
        if st.st_nlink != 1:  # a hard link would write into a file elsewhere
            raise SafePathError("destination has other hard links")
        _write_all(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    return root.path / name


def write_replace(root, name, data: bytes) -> Path:
    """Atomically write name in root (temp file + replace inside the pin)."""
    root = pin_root(root, create=False)
    name = check_name(name)
    tmp = f".{name[:200]}.{secrets.token_hex(8)}.tmp"
    try:
        fd = _open_in(root, tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except OSError as exc:
        raise SafePathError(
            f"could not create the file safely: {exc.strerror}"
        ) from exc
    try:
        _write_all(fd, bytes(data))
    finally:
        os.close(fd)
    try:
        if _HAVE_DIRFD:
            dfd = root._open_dir()
            try:
                os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            finally:
                os.close(dfd)
        else:
            root.recheck()
            os.replace(root.path / tmp, root.path / name)
    except BaseException:
        try:
            os.unlink(root.path / tmp)
        except OSError:
            pass
        raise
    return root.path / name
