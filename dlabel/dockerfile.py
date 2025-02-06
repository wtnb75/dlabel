import docker
from .util import get_diff, get_archives
from logging import getLogger

_log = getLogger(__name__)


def get_dockerfile(container: docker.models.containers.Container,
                   ignore, labels, do_output: bool):
    """make Dockerfile from running container"""
    import shlex
    deleted, added, modified, link = get_diff(container, ignore)
    if do_output:
        ignore_str = """*
!added.tar.gz
!modified.tar.gz
"""
        yield ".dockerignore", ignore_str.encode()
        added_tar = get_archives(container, added, ignore)
        if added_tar:
            yield "added.tar.gz", added_tar
        modified_tar = get_archives(container, modified, ignore)
        if modified_tar:
            yield "modified.tar.gz", modified_tar
    from_image = container.attrs.get("Config", {}).get("Image")
    res: list[str] = []
    res.append(f"FROM {from_image}")
    if deleted:
        res.append("RUN rm -rf " + shlex.join(sorted(deleted)))
    if added:
        res.append("ADD added.tar.gz /")
    if modified:
        res.append("ADD modified.tar.gz /")
    for k, v in sorted(link.items()):
        res.append(f"RUN ln -sf {shlex.quote(v)} {shlex.quote(k)}")
    if labels:
        image_labels = container.image.labels
        for k, v in container.labels.items():
            if k.startswith("com.docker.compose."):
                continue
            if image_labels.get(k) != v:
                res.append(f"LABEL {shlex.quote(k)}={shlex.quote(v)}")
    yield "Dockerfile", "\n".join(res+[""]).encode()
