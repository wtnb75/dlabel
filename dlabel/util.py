import docker
import tarfile
import io
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
