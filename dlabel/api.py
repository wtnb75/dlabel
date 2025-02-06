from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
import docker
import io
import jsonpointer
import crossplane
import tempfile
import tarfile
import time
import json
from abc import abstractmethod, ABCMeta
from typing import Any
from logging import getLogger
from .compose import compose
from .traefik import traefik_dump, traefik2nginx
from .traefik_conf import TraefikConfig
from .dockerfile import get_dockerfile

_log = getLogger(__name__)


class CommonRoute(metaclass=ABCMeta):
    def __init__(self, client: docker.DockerClient):
        self.client = client
        self.router = APIRouter()
        kwargs = dict(response_model_exclude_none=True, response_model_exclude_unset=True)
        self.router.add_api_route("/", self.getroot, methods=["GET"], **kwargs)
        self.router.add_api_route("/{path:path}", self.getsub, methods=["GET"], **kwargs)

    @abstractmethod
    def getroot(self, **kwargs) -> dict:
        raise NotImplementedError("GET /: not implemented")

    def subpath(self, obj: dict, path: str) -> Any:
        try:
            res = jsonpointer.resolve_pointer(obj, "/"+path)
            if isinstance(res, (int, str)):
                return PlainTextResponse(content=str(res))
            return JSONResponse(content=res)
        except jsonpointer.JsonPointerException as e:
            raise HTTPException(status_code=404, detail=dict(path=path, message=e.args[0]))

    @abstractmethod
    def getsub(self, path: str, **kwargs) -> Any:
        return self.subpath(self.getroot(**kwargs), path=path)


class ComposeRoute(CommonRoute):
    def getroot(self, project: str | None = None) -> dict:
        try:
            g = compose(self.client, project=project, volume=False)
            while True:
                _ = next(g)
        except StopIteration as e:
            return e.value

    def getsub(self, path: str, project: str | None = None) -> Any:
        if path == "_tar":
            return self.getarchive(project)
        return super().getsub(path, project=project)

    def getarchive(self, project: str | None = None):
        def arc():
            yield "hello"

        return StreamingResponse(arc, media_type="application/x-tar")


class DockerfileRoute:
    def __init__(self, client: docker.DockerClient):
        self.client = client
        self.router = APIRouter()
        kwargs = dict(response_model_exclude_none=True, response_model_exclude_unset=True)
        self.router.add_api_route("/", self.getroot, methods=["GET"], **kwargs)
        self.router.add_api_route("/{container:path}/Dockerfile", self.get_dockerfile, methods=["GET"],
                                  response_class=PlainTextResponse, **kwargs)
        self.router.add_api_route(
            "/{container:path}/archive.tar", self.get_archive, methods=["GET"],
            response_class=StreamingResponse,
            responses={200: {"content": {"application/x-tar": {}}, }}, **kwargs)

    def getroot(self) -> list[str]:
        # list containers
        return [x.name for x in self.client.containers.list()]

    def get_dockerfile(self, container: str, ignore: list[str] = [], labels: bool = True) -> Any:
        # get dockerfile
        ctn = self.client.containers.get(container)
        for _, bin in get_dockerfile(ctn, ignore, labels, do_output=False):
            return PlainTextResponse(bin.decode())

    def get_archive(self, container: str, ignore: list[str] = [], labels: bool = True):
        ctn = self.client.containers.get(container)

        def arc():
            ofp = io.BytesIO()
            osk = ofp.tell()
            tf = tarfile.open(mode="w", fileobj=ofp, format=tarfile.GNU_FORMAT)
            for name, bin in get_dockerfile(ctn, ignore, labels, do_output=True):
                ti = tarfile.TarInfo(name)
                ti.mode = 0o644
                ti.mtime = time.time()
                ti.size = len(bin)
                tf.addfile(ti, io.BytesIO(bin))
                _log.info("addfile %s, size=%s", name, len(bin))
                if osk != ofp.tell():
                    ofp.seek(osk)
                    yield ofp.read()
                    osk = ofp.tell()
            tf.close()
            if osk != ofp.tell():
                ofp.seek(osk)
                yield ofp.read()
            _log.info("finished: %s", container)

        return StreamingResponse(arc(), media_type="application/x-tar")


class TraefikRoute(CommonRoute):
    def getroot(self) -> TraefikConfig:
        return traefik_dump(self.client)

    def getsub(self, path: str) -> Any:
        return self.subpath(self.getroot().to_dict(), path=path)


class NginxRoute(CommonRoute):
    base_url = "http://localhost/"

    def __init__(self, client: docker.DockerClient):
        self.client = client
        self.router = APIRouter()
        self.router.add_api_route("/", self.getroot, methods=["GET"],
                                  response_class=PlainTextResponse)
        self.router.add_api_route("/json", self.getplane, methods=["GET"])
        self.router.add_api_route("/json/{path:path}", self.getplanesub, methods=["GET"])

    def getroot(self, ipaddr: bool = True) -> PlainTextResponse:
        tmp = io.StringIO()
        traefik2nginx(traefik_dump(self.client), tmp, baseconf=None,
                      server_url=self.base_url, ipaddr=ipaddr)
        return PlainTextResponse(tmp.getvalue())

    def getplane(self, ipaddr: bool = True) -> dict:
        with tempfile.NamedTemporaryFile("r+") as tf:
            traefik2nginx(traefik_dump(self.client), tf, baseconf=None,
                          server_url=self.base_url, ipaddr=ipaddr)
            tf.flush()
            res = crossplane.parse(tf.name, combine=True)
            return json.loads(json.dumps(res).replace("\""+tf.name+"\"", '"nginx.conf"'))

    def getplanesub(self, path: str, ipaddr: bool = True) -> dict:
        return self.subpath(self.getplane(ipaddr), path)

    def getsub(self, path):
        pass
