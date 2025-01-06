import unittest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
import yaml

from dlabel.main import _compose as compose


class TestCompose(unittest.TestCase):

    def test_compose_help(self):
        result = CliRunner().invoke(compose, ["--help"])
        if result.exception:
            raise result.exception
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--verbose", result.output)
        self.assertIn("--quiet", result.output)
        self.assertIn("--format", result.output)
        self.assertIn("--host", result.output)
        self.assertIn("-H", result.output)
        self.assertIn("DOCKER_HOST", result.output)

    @patch("docker.from_env")
    def test_compose_empty(self, dcl):
        dcl.return_value.containers.list.return_value = []
        result = CliRunner().invoke(compose)
        if result.exception:
            raise result.exception
        data = yaml.safe_load(result.output)
        self.assertEqual(0, result.exit_code)
        self.assertEqual({}, data)

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

    @patch("docker.from_env")
    def test_compose_ignore_noproj(self, dcl):
        img1 = self._image("docker-image:latest", {})
        ctn1 = self._container("ctn1", {"key1": "value1", "key2": "value2"}, img1)
        dcl.return_value.containers.list.return_value = [ctn1]
        result = CliRunner().invoke(compose)
        if result.exception:
            raise result.exception
        data = yaml.safe_load(result.output)
        self.assertEqual(0, result.exit_code)
        self.assertEqual({}, data)

    @patch("docker.from_env")
    def test_compose_ignore_proj(self, dcl):
        img1 = self._image("docker-image:latest", {
            "image-label1": "image-value1",
            "image-label2": "container-value"
        })
        ctn1 = self._container("ctn1", {"com.docker.compose.project": "proj2"}, img1)
        dcl.return_value.containers.list.return_value = [ctn1]
        result = CliRunner().invoke(compose, ["--project", "proj1"])
        if result.exception:
            raise result.exception
        data = yaml.safe_load(result.output)
        self.assertEqual(0, result.exit_code)
        self.assertEqual({}, data)

    @patch("docker.from_env")
    def test_compose_proj(self, dcl):
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
        dcl.return_value.containers.list.return_value = [ctn1, ctn2]
        result = CliRunner().invoke(compose, ["--project", "proj1"])
        if result.exception:
            raise result.exception
        data = yaml.safe_load(result.output)
        self.assertEqual(0, result.exit_code)
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
        self.assertEqual(expected, data)


if __name__ == '__main__':
    unittest.main()
