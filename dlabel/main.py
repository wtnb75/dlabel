import functools
import docker
import click
from pathlib import Path
from logging import getLogger
from typing import Any
import yaml
import re
import sys
import io
import tarfile
import fnmatch

VERSION = "0.1.dev1"
_log = getLogger(__name__)


@click.group(invoke_without_command=True)
@click.version_option(VERSION)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


def verbose_option(func):
    @click.option("--verbose/--quiet", default=None)
    @functools.wraps(func)
    def _(verbose, **kwargs):
        from logging import basicConfig
        fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
        if verbose is None:
            basicConfig(level="INFO", format=fmt)
        elif verbose is False:
            basicConfig(level="WARNING", format=fmt)
        else:
            basicConfig(level="DEBUG", format=fmt)
        return func(**kwargs)
    return _


def docker_option(func):
    @click.option("-H", "--host", envvar="DOCKER_HOST", help="Daemon socket(s) to connect to", show_envvar=True)
    @functools.wraps(func)
    def _(host, **kwargs):
        if not host:
            cl = docker.from_env()
        else:
            cl = docker.DockerClient(base_url=host)
        return func(client=cl, **kwargs)

    return _


@cli.command()
@click.option("--output", type=click.File("w"), default="-", show_default=True)
@verbose_option
@docker_option
def summary(client: docker.DockerClient, output):
    """show name and labels of containers"""
    res: list[dict] = []
    for ctn in client.containers.list():
        res.append({
            "name": ctn.name,
            "labels": ctn.attrs["Config"]["Labels"]
        })
    yaml.dump(res, stream=output, allow_unicode=True, encoding="utf-8", sort_keys=False)


@cli.command()
@click.option("--output", type=click.File("w"), default="-", show_default=True)
@verbose_option
@docker_option
def attrs(client: docker.DockerClient, output):
    """show name and attributes of containers"""
    res: list[dict] = []
    for ctn in client.containers.list():
        res.append({
            "name": ctn.name,
            "attrs": ctn.attrs
        })
    yaml.dump(res, stream=output, allow_unicode=True, encoding="utf-8", sort_keys=False)


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


@cli.command()
@click.option("--output", type=click.Path(file_okay=False, dir_okay=True, exists=True, writable=True))
@click.option("--all/--compose", default=False, show_default=True)
@click.option("--volume/--no-volume", default=True, show_default=True)
@click.option("--project", default="*", show_default=True)
@verbose_option
@docker_option
def compose(client: docker.DockerClient, output, all, project, volume):
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
            if len(v) == 2:
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
        if hostconfig.get("RestartPolicy", {}).get("Name") != "no":
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
            yaml.dump(res, stream=ofp, allow_unicode=True, encoding="utf-8", sort_keys=False)
    else:
        yaml.dump(res, stream=sys.stdout, allow_unicode=True, encoding='utf-8', sort_keys=False)


def tflabel2dict(labels: dict[str, str], prefix: str) -> dict[str, dict[str, str]]:
    res = {}
    for k, v in labels.items():
        if k.startswith(prefix):
            last = k[len(prefix):].lstrip(".")
            name, k2 = last.split(".", 1)
            if name not in res:
                res[name] = {}
            res[name][k2] = v
    return res


@cli.command()
@click.option("--output", type=click.File("w"), default="-", show_default=True)
@click.option("--ipaddr/--hostname", default=False, show_default=True)
@verbose_option
@docker_option
def traefik2nginx(client: docker.DockerClient, output, ipaddr):
    """generate nginx configuration from traefik labels"""
    for ctn in client.containers.list():
        name = ctn.name
        labels = ctn.attrs["Config"]["Labels"]
        addresses = [x["IPAddress"] for x in ctn.attrs["NetworkSettings"]["Networks"].values()]
        pass_to = name
        if ipaddr:
            pass_to = addresses[0]
        if labels.get("traefik.enable") not in ("true",):
            _log.debug("traefik is not enabled: %s", labels)
            continue
        services = tflabel2dict(labels, "traefik.http.services.")
        routers = tflabel2dict(labels, "traefik.http.routers.")
        middlewares = tflabel2dict(labels, "traefik.http.middlewares.")
        _log.debug("services: %s", services)
        _log.debug("routers: %s", routers)
        _log.debug("middleware: %s", middlewares)
        for router_name, router_config in routers.items():
            rule = router_config.get("rule", "")
            dest = services.get(router_name).get("loadbalancer.server.port")
            if not dest:
                _log.debug("not match lbport: %s", router_config)
                continue
            m = re.match(r"^PathPrefix\(`(?P<prefix>[^`]+)`\)$", rule)
            location_key = None
            if m:
                location_key = m.group("prefix")
            else:
                m = re.match(r"^Path\(`(?P<path>[^`]+)`\)$", rule)
                if m:
                    location_key = f"= {m.group('path')}"
            if not location_key:
                _log.info("not supported rule: %s", rule)
                continue
            confs = [f"proxy_pass http://{pass_to}:{dest}"]
            mdls = router_config.get("middlewares", "").split(",")
            mdlconf = {}
            for mname in mdls:
                if not mname:
                    continue
                _log.debug("middleware[%s]: %s", location_key, mname)
                if mname not in middlewares:
                    _log.info("middleware not found: %s", mname)
                mdlconf.update(middlewares.get(mname, {}))
            _log.debug("middleware configurations: %s", mdlconf)
            del_prefix = ""
            add_prefix = "/"
            if mdlconf.get("stripprefix.prefixes"):
                del_prefix = mdlconf["stripprefix.prefixes"]
            elif mdlconf.get("stripprefixregex.regex"):
                del_prefix = mdlconf.get("stripprefixregex.regex")
            if mdlconf.get("addprefix.prefix"):
                add_prefix = mdlconf["addprefix.prefix"]
            if del_prefix or add_prefix != "/":
                confs.append(f"rewrite {del_prefix}(.*) {add_prefix}$1 break")
            for k, v in mdlconf.items():
                if k.startswith("headers.customrequestheaders."):
                    hdr = k[len("headers.customrequestheaders."):]
                    confs.append(f"proxy_set_header {hdr} {v}")
                elif k.startswith("headers.customresponseheaders."):
                    hdr = k[len("headers.customresponseheaders."):]
                    confs.append(f"add_header {hdr} {v}")
                elif k.split(".", 1)[0] not in ("stripprefix", "addprefix", "headers"):
                    _log.info("not supported middleware: %s", k)
            print(f"# {name} {location_key} -> {addresses[0]}:{dest}")
            print("location %s {" % (location_key))
            for i in confs:
                print("  "+i+";")
            print("}")


@cli.command()
@docker_option
@verbose_option
def list_volume(client: docker.DockerClient):
    """list volumes"""
    yaml.dump([x.attrs for x in client.volumes.list()], stream=sys.stdout,
              allow_unicode=True, encoding="utf-8", sort_keys=False)


@cli.command()
@docker_option
@verbose_option
@click.option("--image", default='hello-world', show_default=True)
@click.option("--output", type=click.File("wb"), default="-", show_default=True)
@click.option("-z", is_flag=True, help="compress with gzip")
@click.argument("volume")
def tar_volume(client: docker.DockerClient, volume, image, output, z):
    """get volume content as tar"""
    mount = "/" + volume.strip("/")
    vol = client.volumes.get(volume)
    _log.debug("Volume %s found with ID %s", volume, vol.id)

    try:
        img = client.images.get(image)
        _log.debug("Image %s found locally", image)
    except docker.errors.ImageNotFound:
        img = client.images.pull(image)
        _log.debug("Image %s pulled successfully", image)

    mnt = docker.types.Mount(target=mount, source=vol.id, read_only=True)
    cl = client.containers.create(img, mounts=[mnt])
    _log.debug("Container created with image %s and volume %s mounted at %s", image, volume, mount)

    try:
        bin, _ = cl.get_archive(mount, encode_stream=z)
        _log.debug("Starting to archive volume %s", volume)
        for b in bin:
            output.write(b)
        _log.debug("Volume %s archived successfully", volume)
    finally:
        cl.remove(force=True)
        _log.debug("Container removed")


if __name__ == "__main__":
    cli()
