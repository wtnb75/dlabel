import unittest
from unittest.mock import patch, MagicMock, ANY
import click
from click.testing import CliRunner
import json
import yaml

from dlabel.main import compose


class TestCompose(unittest.TestCase):

    def test_compose_help(self):
        result = CliRunner().invoke(compose, ["--help"])
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

    def _container(self, name, labels, image):
        container = MagicMock()
        container.name = name
        container.attrs = {
            "Config": {
                "Labels": labels,
                "Image": image,
            }
        }
        return container

    @patch("docker.from_env")
    def test_compose_ignore_noproj(self, dcl):
        ctn1 = self._container("ctn1", {"key1": "value1", "key2": "value2"}, "docker-image:latest")
        dcl.return_value.containers.list.return_value = [ctn1]
        result = CliRunner().invoke(compose)
        if result.exception:
            raise result.exception
        data = yaml.safe_load(result.output)
        self.assertEqual(0, result.exit_code)
        self.assertEqual({}, data)

    @patch("docker.from_env")
    def test_compose_proj(self, dcl):
        ctn1 = self._container("ctn1", {"com.docker.compose.project": "proj1", "key2": "value2"}, "docker-image:latest")
        dcl.return_value.containers.list.return_value = [ctn1]
        result = CliRunner().invoke(compose, ["--project", "proj1"])
        if result.exception:
            raise result.exception
        data = yaml.safe_load(result.output)
        self.assertEqual(0, result.exit_code)
        expected = {
            "services": {
                "ctn1": {
                    "container_name": "ctn1",
                    "image": "docker-image:latest",
                    "labels": {
                        "key2": "value2",
                    }
                }
            }
        }
        self.assertEqual(expected, data)


if __name__ == '__main__':
    unittest.main()
