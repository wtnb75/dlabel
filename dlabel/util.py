import docker
import tarfile
import fnmatch
import io
from pathlib import Path
from logging import getLogger

_log = getLogger(__name__)

# https://pkg.go.dev/io/fs#ModeDir
modebits = {
    "dir": 1 << 31,
    "append": 1 << 30,
    "exclusive": 1 << 29,
    "temporary": 1 << 28,
    "symlink": 1 << 27,
    "device": 1 << 26,
    "namedpipe": 1 << 25,
    "socket": 1 << 24,
    "setuid": 1 << 23,
    "setgid": 1 << 22,
    "chardev": 1 << 21,
    "sticky": 1 << 20,
    "irregular": 1 << 19,
}
nonreg = {"dir", "device", "namedpipe", "socket", "chardev", "irregular"}


def special_modes(mode: int) -> tuple[set[str], int]:
    res: set[str] = set()
    for k, v in modebits.items():
        if (mode & v) != 0:
            res.add(k)
    return res, (mode & 0o777)


def download_files(ctn: docker.models.containers.Container, filename: str):
    bins, stat = ctn.get_archive(filename)
    is_dir = "dir" in special_modes(stat["mode"])[0]
    _log.debug("download %s: %s is_dir=%s", filename, stat, is_dir)
    fp = io.BytesIO()
    for chunk in bins:
        fp.write(chunk)
    fp.seek(0)
    with tarfile.open(fileobj=fp, mode="r|") as tar:
        for member in tar:
            if member.isfile():
                _log.debug("extract %s", member.name)
                tf = tar.extractfile(member)
                if tf is not None:
                    yield is_dir, member, tf.read()


def get_archives(container: docker.models.containers.Container, names: set[str], ignore: list[str],
                 mode: str = "w:gz"):
    if not names:
        return
    ofp = io.BytesIO()
    outarchive = tarfile.open(mode=mode, fileobj=ofp)
    for fn in sorted(names):
        _log.debug("extract: %s", fn)
        for is_dir, tinfo, bin in download_files(container, fn):
            if is_dir:
                tinfo.name = str(Path(fn) / tinfo.name).lstrip("/")
            else:
                tinfo.name = fn.lstrip("/")
            if is_match(ignore, tinfo.name):
                _log.debug("ignore: %s", tinfo.name)
                continue
            _log.debug("add file: %s (%s bytes) is_dir=%s", tinfo.name, len(bin), is_dir)
            outarchive.addfile(tinfo, io.BytesIO(bin))
    outarchive.close()
    return ofp.getvalue()


def is_match(patterns: list[str], target: str) -> bool:
    for p in patterns:
        if fnmatch.fnmatch(target, p):
            return True
    return False


def is_already(prev: set[str], target: str) -> bool:
    for p in prev:
        if Path(p) in Path(target).parents:
            return True
    return False


def do_kind0(modified: set[str], path: str, link: dict[str, str],
             container: docker.models.containers.Container):   # modified
    _, stats = container.get_archive(path)
    _log.debug("stats %s: %s", path, stats)
    special, _ = special_modes(stats["mode"])
    if nonreg & special:
        _log.debug("skip: %s %s", path, special)
    elif "symlink" in special and stats["linkTarget"]:
        link[path] = stats["linkTarget"]
    else:
        modified.add(path)


def do_kind1(added: set[str], path: str, link: dict[str, str],
             container: docker.models.containers.Container):   # added
    if is_already(added, path):
        _log.debug("skip(parent-exists): %s", path)
    else:
        _, stats = container.get_archive(path)
        _log.debug("stats %s: %s", path, stats)
        special, _ = special_modes(stats["mode"])
        if (nonreg-{"dir"}) & special:
            _log.debug("skip: %s %s", path, special)
        elif "symlink" in special and stats["linkTarget"]:
            link[path] = stats["linkTarget"]
        else:
            added.add(path)


def do_kind2(deleted: set[str], path: str):  # deleted
    if is_already(deleted, path):
        _log.debug("skip(parent-exists): %s", path)
    else:
        deleted.add(path)


def get_diff(container: docker.models.containers.Container, ignore: list[str]) -> \
        tuple[set[str], set[str], set[str], dict[str, str]]:
    deleted: set[str] = set()
    added: set[str] = set()
    modified: set[str] = set()
    link: dict[str, str] = {}
    for pathkind in container.diff():
        path = pathkind["Path"]
        kind = pathkind["Kind"]
        if is_match(ignore, path):
            _log.debug("ignore: %s", path)
            continue
        if kind == 2:  # deleted
            do_kind2(deleted, path)
        elif kind == 1:  # added
            do_kind1(added, path, link, container)
        elif kind == 0:  # modified
            do_kind0(modified, path, link, container)
    _log.debug("deleted: %s", deleted)
    _log.debug("added: %s", added)
    _log.debug("modified: %s", modified)
    _log.debug("link: %s", link)
    return deleted, added, modified, link
