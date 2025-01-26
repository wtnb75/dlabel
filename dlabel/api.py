from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
import docker
import io
import jsonpointer
import crossplane
import tempfile
import json
from abc import abstractmethod, ABCMeta
from typing import Any
from .compose import compose
from .traefik import traefik_dump, traefik2nginx
from .traefik_conf import TraefikConfig


class CommonRoute(metaclass=ABCMeta):
    def __init__(self, client: docker.DockerClient):
        self.client = client
        self.router = APIRouter()
        self.router.add_api_route("/", self.getroot, methods=["GET"])
        self.router.add_api_route("/{path:path}", self.getsub, methods=["GET"])

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
        return compose(self.client, output=None, all=not bool(project), project=project, volume=False)

    def getsub(self, path: str, project: str | None = None) -> Any:
        if path == "_tar":
            return self.getarchive(project)
        return super().getsub(path, project=project)

    def getarchive(self, project: str | None = None):
        def arc():
            yield "hello"

        return StreamingResponse(arc, media_type="application/x-tar")


class DockerfileRoute(CommonRoute):
    def getroot(self) -> dict:
        # list containers
        pass

    def getsub(self, path: str, project: str | None = None) -> Any:
        # get dockerfile
        pass

    def getarchive(self, project: str | None = None):
        def arc():
            yield "hello"

        return StreamingResponse(arc, media_type="application/x-tar")


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
        self.router.add_api_route("/", self.getroot, methods=["GET"])
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
