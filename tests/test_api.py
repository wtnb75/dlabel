import unittest
from unittest.mock import MagicMock, ANY
from fastapi import FastAPI
from fastapi.testclient import TestClient
from dlabel.api import ComposeRoute, TraefikRoute, NginxRoute


class TestComposeRoute(unittest.TestCase):
    def setUp(self):
        self.docker_cl = MagicMock()
        self.api = FastAPI()
        self.api.include_router(ComposeRoute(self.docker_cl).router, prefix="/compose")
        self.client = TestClient(self.api)

    def tearDown(self):
        del self.docker_cl
        del self.client
        del self.api

    def _image(self, name, labels):
        image = MagicMock()
        image.tags = [name]
        image.labels = labels
        image.attrs = {
            "Config": {
                "Env": ["env1=value1=ext1", "env2=value2"],
            }
        }
        return image

    def _container(self, name, labels, image):
        container = MagicMock()
        container.name = name
        container.attrs = {
            "Config": {
                "Labels": labels,
                "Image": image.tags[0],
                "Env": ["env1=value1=ext1", "env2=value2"],
            }
        }
        container.image = image
        return container

    def test_root0(self):
        self.docker_cl.containers.list.return_value = []
        res = self.client.get("/compose")
        self.assertEqual(200, res.status_code)
        self.assertEqual({}, res.json())
        self.docker_cl.containers.list.assert_called_once_with()

    def _setup_mock(self):
        img1 = self._image("docker-image:latest", {
            "image-label1": "image-value1", "image-label2": "image-value2"})
        ctn1 = self._container("proj1_ctn1", {
            "com.docker.compose.project": "proj1",
            "com.docker.compose.service": "ctn1",
            "key2": "value2",
            "image-label1": "image-value1",
            "image-label2": "container-value"}, img1)
        img2 = self._image("docker-image2:latest", {
            "image-label1": "image-value1", "image-label2": "image-value2"})
        ctn2 = self._container("name2", {
            "com.docker.compose.project": "proj1",
            "com.docker.compose.service": "ctn2",
            "image-label1": "image-value1",
            "image-label2": "image-value2"}, img2)
        ctn2.attrs["Config"]["Env"][1] = "env2=value2=ext2"
        ctn2.attrs["Config"]["Labels"]["com.docker.compose.project.working_dir"] = "/home/dir"
        ctn2.attrs["HostConfig"] = {
            "Binds": ["/home/dir/data:/data:rw", "/home/dir2/data2:/data2:ro"],
            "Mounts": [{
                "Type": "volume",
                "Target": "/db",
                "Source": "proj1_db",
            }],
            "PortBindings": {
                "8080/tcp": [{"HostPort": "8080"}],
                "443/udp": [{"HostPort": "443"}],
                "8888/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8888", }],
            },
            "RestartPolicy": {
                "Name": "always",
            }
        }
        expected = {
            "services": {
                "ctn1": {
                    "image": "docker-image:latest",
                    "labels": {
                        "key2": "value2",
                        "image-label2": "container-value",
                    }
                },
                "ctn2": {
                    "container_name": "name2",
                    "image": "docker-image2:latest",
                    "environment": {
                        "env2": "value2=ext2",
                    },
                    "volumes": ["./data:/data", "/home/dir2/data2:/data2:ro", "db:/db"],
                    "ports": [
                        "8080:8080",
                        {"target": 443, "published": 443, "protocol": "udp", "mode": "host"},
                        "127.0.0.1:8888:8888"
                    ],
                    "restart": "always",
                }
            },
            "volumes": {"db": {}}
        }
        self.docker_cl.containers.list.return_value = [ctn1, ctn2]
        return expected

    def test_root_container(self):
        expected = self._setup_mock()
        res = self.client.get("/compose")
        self.assertEqual(200, res.status_code)
        self.assertEqual(expected, res.json())

    def test_subpath(self):
        expected = self._setup_mock()
        res = self.client.get("/compose/services/ctn1")
        self.assertEqual(200, res.status_code)
        self.assertEqual(expected["services"]["ctn1"], res.json())

    def test_subpath_text(self):
        expected = self._setup_mock()
        res = self.client.get("/compose/services/ctn1/image")
        self.assertEqual(200, res.status_code)
        self.assertEqual("utf-8", res.charset_encoding)
        self.assertIn("text/plain", res.headers.get("content-type"))
        self.assertEqual(expected["services"]["ctn1"]["image"], res.text)

    def test_subpath_notfound(self):
        self._setup_mock()
        res = self.client.get("/compose/services/ctn1/notfound")
        expected = {
            "detail": {
                "path": "services/ctn1/notfound",
                "message": ANY,
            }
        }
        self.assertEqual(404, res.status_code)
        self.assertDictEqual(expected, res.json())


class TestTraefikRoute(unittest.TestCase):
    def setUp(self):
        self.docker_cl = MagicMock()
        self.api = FastAPI()
        self.api.include_router(TraefikRoute(self.docker_cl).router, prefix="/traefik")
        self.client = TestClient(self.api)

    def tearDown(self):
        del self.docker_cl
        del self.client
        del self.api

    def test_root0(self):
        self.docker_cl.containers.list.return_value = []
        res = self.client.get("/traefik")
        self.assertEqual(200, res.status_code)
        self.assertEqual({}, res.json())
        self.docker_cl.containers.list.assert_called_once_with()

    def _container(self, name, image_name, labels: dict[str, str], args: list[str],
                   env: list[str], ipaddr: str | None = None):
        container = MagicMock()
        container.name = name
        container.status = "running"
        container.labels = labels
        container.image.tags = [image_name]
        container.attrs = {
            "Config": {
                "Labels": labels,
                "Image": image_name,
                "Env": env,
                "Cmd": args,
            },
            "NetworkSettings": {
                "Networks": {},
            },
            "Args": args[1:],
        }
        if ipaddr:
            container.attrs["NetworkSettings"]["Networks"] = {"xyz": {"IPAddress": "1.2.3.4"}}
        return container

    def _setup_mock(self):
        ctn1 = self._container(
            "proj1_ctn1", "alpine:3",
            {
                "label123": "valule123",
                "traefik.enable": "true",
                "traefik.http.routers.ctn1.entrypoints": "web",
                "traefik.http.routers.ctn1.middlewares": "mdl",
                "traefik.http.routers.ctn1.rule": "Path(`/`)",
                "traefik.http.services.ctn1.loadbalancer.server.port": "8080",
            }, [], [], "1.2.3.4")
        ctn2 = self._container(
            "proj1_ctn2", "alpine:3",
            {
                "label234": "valule234",
                "traefik.enable": "true",
                "traefik.http.routers.ctn2.entrypoints": "web",
                "traefik.http.routers.ctn2.middlewares": "mdl",
                "traefik.http.routers.ctn2.rule": "PathPrefix(`/ctn2`)",
                "traefik.http.services.ctn2.loadbalancer.server.port": "9999",
                "traefik.api": "true",
            }, [], [])
        self.docker_cl.containers.list.return_value = [ctn1, ctn2]
        expected = {
            "api": {},
            "http": {
                "routers": {
                    "ctn1": {
                        "entrypoints": ["web"],
                        "rule": "Path(`/`)",
                        "middlewares": ["mdl"]},
                    "ctn2": {
                        "entrypoints": ["web"],
                        "rule": "PathPrefix(`/ctn2`)",
                        "middlewares": ["mdl"]}},
                "services": {
                    "ctn1": {
                        "loadbalancer": {
                            "server": {"host": "proj1_ctn1", "ipaddress": "1.2.3.4", "port": 8080}}},
                    "ctn2": {
                        "loadbalancer": {
                            "server": {"host": "proj1_ctn2", "ipaddress": "", "port": 9999}}}}}}
        return expected

    def test_label(self):
        expected = self._setup_mock()
        res = self.client.get("/traefik")
        self.assertEqual(expected, res.json())

    def test_label_sub(self):
        expected = self._setup_mock()
        res = self.client.get("/traefik/http/routers")
        self.assertEqual(expected["http"]["routers"], res.json())


class TestNginxRoute(unittest.TestCase):
    def setUp(self):
        self.docker_cl = MagicMock()
        self.api = FastAPI()
        self.api.include_router(NginxRoute(self.docker_cl).router, prefix="/nginx")
        self.client = TestClient(self.api)

    def tearDown(self):
        del self.docker_cl
        del self.client
        del self.api

    def _container(self, name, image_name, labels: dict[str, str], args: list[str],
                   env: list[str], ipaddr: str | None = None):
        container = MagicMock()
        container.name = name
        container.status = "running"
        container.labels = labels
        container.image.tags = [image_name]
        container.attrs = {
            "Config": {
                "Labels": labels,
                "Image": image_name,
                "Env": env,
                "Cmd": args,
            },
            "NetworkSettings": {
                "Networks": {},
            },
            "Args": args[1:],
        }
        if ipaddr:
            container.attrs["NetworkSettings"]["Networks"] = {"xyz": {"IPAddress": "1.2.3.4"}}
        return container

    def _setup_mock(self):
        ctn1 = self._container(
            "proj1_ctn1", "alpine:3",
            {
                "label123": "valule123",
                "traefik.enable": "true",
                "traefik.http.routers.ctn1.entrypoints": "web",
                "traefik.http.routers.ctn1.middlewares": "mdl",
                "traefik.http.routers.ctn1.rule": "Path(`/`)",
                "traefik.http.services.ctn1.loadbalancer.server.port": "8080",
            }, [], [], "1.2.3.4")
        ctn2 = self._container(
            "proj1_ctn2", "alpine:3",
            {
                "label234": "valule234",
                "traefik.enable": "true",
                "traefik.http.routers.ctn2.entrypoints": "web",
                "traefik.http.routers.ctn2.middlewares": "mdl",
                "traefik.http.routers.ctn2.rule": "PathPrefix(`/ctn2`)",
                "traefik.http.services.ctn2.loadbalancer.server.port": "9999",
                "traefik.api": "true",
            }, [], [])
        self.docker_cl.containers.list.return_value = [ctn1, ctn2]
        expected = {
            "status": "ok",
            "errors": [],
            "config": [{
                "status": "ok",
                "errors": [],
                "parsed": [{
                    "directive": "user",
                    "args": ["nginx"]
                }, {
                    "directive": "worker_processes",
                    "args": ["auto"]
                }, {
                    "directive": "error_log",
                    "args": ["/dev/stderr", "notice"]
                }, {
                    "directive": "events",
                    "args": [],
                    "block": [{
                        "directive": "worker_connections",
                        "args": ["512"]
                    }]
                }, {
                    "directive": "http",
                    "args": [],
                    "block": [{
                        "directive": "server",
                        "args": [],
                        "block": [{
                            "directive": "listen",
                            "args": ["80", "default_server"]
                        }, {
                            "directive": "server_name",
                            "args": ["localhost"]
                        }, {
                            "directive": "location",
                            "args": ["=", "/"],
                            "block": [{
                                "directive": "proxy_pass",
                                "args": ["http://1.2.3.4:8080"]
                            }]
                        }, {
                            "directive": "location",
                            "args": ["/ctn2"],
                            "block": [{
                                "directive": "proxy_pass",
                                "args": ["http://:9999"]
                            }]
                        }]
                    }]
                }]
            }]
        }
        return expected

    def _del_file_line(self, d: dict) -> dict:
        d.pop("file", None)
        d.pop("line", None)
        if "block" in d:
            d["block"] = [self._del_file_line(x) for x in d["block"]]
        if "parsed" in d:
            d["parsed"] = [self._del_file_line(x) for x in d["parsed"]]
        return d

    def test_plain(self):
        self._setup_mock()
        res = self.client.get("/nginx")
        self.assertEqual(200, res.status_code)
        self.assertIn("user nginx;", res.text)
        self.assertIn("worker_processes auto;", res.text)
        self.assertIn("location /ctn2", res.text)
        self.assertIn("location = /", res.text)
        self.assertIn("1.2.3.4:8080", res.text)

    def test_json(self):
        expected = self._setup_mock()
        res = self.client.get("/nginx/json")
        resj = res.json()
        resj["config"] = [self._del_file_line(x) for x in resj["config"]]
        self.assertEqual(200, res.status_code)
        self.assertDictEqual(expected, resj)

    def test_json_subpath(self):
        self._setup_mock()
        res = self.client.get("/nginx/json/status")
        self.assertEqual(200, res.status_code)
        self.assertEqual("ok", res.text)
