import io
import tarfile
import fnmatch
import docker
from typing import Any
from pathlib import Path
from logging import getLogger
_log = getLogger(__name__)


def envlist2map(env: list[str], sep: str = "=") -> dict[str, str]:
    res = {}
    for i in env:
        kv = i.split(sep, 1)
        if len(kv) == 2:
            res[kv[0]] = kv[1]
    return res


def portmap2compose(pmap: dict) -> list[str]:
    res = []
    for k, v in pmap.items():
        ctport = k
        if ctport.endswith("/tcp") and len(v) == 1:
            ctport = k.split("/")[0]
            hostip = v[0].get("HostIp")
            hostport = v[0].get("HostPort")
            if hostip:
                res.append(f"{hostip}:{hostport}:{ctport}")
            else:
                res.append(f"{hostport}:{ctport}")
        else:
            target, protocol = k.split("/", 1)
            res.append({
                "target": int(target),
                "published": v[0].get("HostPort"),
                "protocol": protocol,
                "mode": "host",
            })
    return res


def convdict(convmap: dict[str, str], fromdict: dict[str, Any], todict: dict[str, Any]):
    for k, v in convmap.items():
        if fromdict.get(k):
            todict[v] = fromdict.get(k)


def convdict_differ(
        convmap: dict[str, str], dict_img: dict[str, Any], dict_ctn: dict[str, Any], todict: dict[str, Any]):
    for k, v in convmap.items():
        if k in dict_ctn and dict_img.get(k) != dict_ctn.get(k):
            todict[v] = dict_ctn[k]


def copy_files(ctn: docker.models.containers.Container, src: str, dst: str):
    def tfilter(member, path):
        res = tarfile.data_filter(member, path)
        if res:
            if '/' in res.name:
                _, res.name = res.name.split('/', 1)
                return res
        return None

    _log.info("copy %s:%s -> %s", ctn.name, src, dst)
    odir = Path(dst)
    bin, arc = ctn.get_archive(src)
    _log.debug("arc=%s", arc)
    bio = io.BytesIO()
    for x in bin:
        bio.write(x)
    bio.seek(0)
    tf = tarfile.TarFile(fileobj=bio)
    members = tf.getmembers()
    if len(members) == 1 and members[0].isreg():
        _log.info("single file: %s", members[0])
        tf.extractall(odir.parent, filter='data')
    else:
        odir.mkdir(exist_ok=True, parents=True)
        tf.extractall(odir, filter=tfilter)
    tf.close()
    bio.close()


def compose(client: docker.DockerClient, output, all, project, volume):   # noqa: C901
    """generate docker-compose.yml from running containers"""
    svcs = {}
    vols = {}
    nets = {}
    for ctn in client.containers.list():
        config = ctn.attrs.get("Config", {})
        hostconfig = ctn.attrs.get("HostConfig", {})
        labels: dict[str, str] = config.get("Labels", {})
        proj = labels.get("com.docker.compose.project")
        wdir = Path(labels.get("com.docker.compose.project.working_dir", "/"))
        if not all and not proj:
            _log.debug("skip: no project, not --all: %s", ctn.name)
            continue
        if not all and proj and not fnmatch.fnmatch(proj, project):
            _log.debug("skip by project (%s)", proj)
            continue
        name = labels.get("com.docker.compose.service", ctn.name)
        _log.info("processing %s, service=%s", ctn.name, name)
        img = ctn.image
        imglabel = img.labels
        imgconfig = img.attrs.get("Config", {})
        for k, v in imglabel.items():
            if labels.get(k) == v:
                labels.pop(k)
        labels = {k: v for k, v in labels.items() if not k.startswith("com.docker.compose.")}
        envs = envlist2map(config.get("Env", []))
        imgenv = envlist2map(imgconfig.get("Env", []))
        for k, v in imgenv.items():
            if envs.get(k) == v:
                envs.pop(k)
        imgvol = imgconfig.get("Volumes", {})
        cvols = []
        for i in (hostconfig.get("Binds") or []):
            v = i.split(":", 2)
            if imgvol and v[1] in imgvol:
                continue
            src = Path(v[0])
            dest = v[1]
            if src.is_relative_to(wdir):
                src = "./" + str(src.relative_to(wdir))
            if len(v) == 2 or v[2] == "rw":
                cvols.append(f"{src}:{dest}")
            elif len(v) == 3:
                cvols.append(f"{src}:{dest}:{v[2]}")
            if output and volume and isinstance(src, str) and src.startswith("./"):
                copy_files(ctn, dest, Path(output) / src)
            elif output:
                _log.info("skip copy: %s:%s -> %s", name, dest, src)
        for m in hostconfig.get("Mounts", []):
            if imgvol and m.get("Target") in imgvol:
                continue
            volname = m.get("Source")
            if volname.startswith(proj+"_"):
                volname = volname[len(proj)+1:]
            if m.get("Type") == "volume":
                vols[volname] = m.get("VolumeOptions", {})
            if m.get("Target"):
                cvols.append(f"{volname}:{m['Target']}")
        nwmode = None
        cnws = []
        if not proj or hostconfig.get("NetworkMode") != f"{proj}_default":
            nwmode = hostconfig.get("NetworkMode")
        if nwmode not in (None, "host", "none"):
            nets[nwmode] = {}
            cnws.append(nwmode)
            nwmode = None
        svc = {
            "image": config.get("Image"),
        }
        if proj and not ctn.name.startswith(proj+"_"):
            svc["container_name"] = ctn.name
        if nwmode:
            svc["network_mode"] = nwmode
        if cvols:
            svc["volumes"] = cvols
        if cnws:
            svc["networks"] = cnws
        if hostconfig.get("PortBindings"):
            svc["ports"] = portmap2compose(hostconfig.get("PortBindings", {}))
        if hostconfig.get("RestartPolicy", {}).get("Name") not in ("no", None):
            svc["restart"] = hostconfig.get("RestartPolicy", {}).get("Name")
        if labels:
            svc["labels"] = labels
        if envs:
            svc["environment"] = envs
        convmap_hostconfig = {
            "ExtraHosts": "extra_hosts",
            "CpuShares": "cpu_shares",
            "CpuPeriod": "cpu_period",
            "CpuPercent": "cpu_percent",
            "CpuCount": "cpu_count",
            "CpuQuota": "cpu_quota",
            "CpuRealtimeRuntime": "cpu_rt_runtime",
            "CpuRealtimePeriod": "cpu_rt_period",
            "CpusetCpus": "cpuset",
            "CapAdd": "cap_add",
            "CapDrop": "cap_drop",
            "CgroupParent": "cgroup_parent",
            "GroupAdd": "group_add",
            "Privileged": "privileged",
        }
        convmap_label = {
            "com.docker.compose.depends_on": "depends_on",
        }
        convdict(convmap_hostconfig, hostconfig, svc)
        convdict(convmap_label, config.get("Labels", {}), svc)
        diffcopy_config = {
            "Cmd": "command",
            "Entrypoint": "entrypoint",
        }
        convdict_differ(diffcopy_config, imgconfig, config, svc)
        svcs[name] = svc
    res = {}
    if svcs:
        res["services"] = svcs
    if vols:
        res["volumes"] = vols
    if nets:
        res["networks"] = nets
    if output:
        with (Path(output) / "compose.yml").open("w") as ofp:
            import yaml
            yaml.dump(res, stream=ofp, allow_unicode=True, encoding="utf-8", sort_keys=False)
    return res
