import unittest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from dlabel.main import cli


class TestDockerfile(unittest.TestCase):
    @patch("docker.from_env")
    def test_empty(self, dcl):
        ctn1 = MagicMock()
        ctn1.name = "container1"
        ctn1.attrs = {
            "Config": {
                "Image": "image1:tag1",
            }
        }
        ctn1.diff.return_value = []
        dcl.return_value.containers.get.return_value = ctn1
        result = CliRunner().invoke(cli, ["make-dockerfile", "--id", "id123"])
        if result.exception:
            raise result.exception
        self.assertEqual(0, result.exit_code)
        self.assertEqual("FROM image1:tag1", result.output.strip())

    @patch("docker.from_env")
    def test_differ(self, dcl):
        ctn1 = MagicMock()
        ctn1.name = "container1"
        ctn1.attrs = {
            "Config": {
                "Image": "image1:tag1",
            }
        }
        ctn1.diff.return_value = [
            {"Path": "/path1-updated", "Kind": 0},
            {"Path": "/path1-added", "Kind": 1},
            {"Path": "/path1-deleted", "Kind": 2},
        ]
        ctn1.get_archive.return_value = ([], {"mode": 0o644})
        dcl.return_value.containers.get.return_value = ctn1
        result = CliRunner().invoke(cli, ["make-dockerfile", "--id", "id123"])
        if result.exception:
            raise result.exception
        self.assertEqual(0, result.exit_code)
        self.assertIn("FROM image1:tag1", result.output)
        self.assertIn("ADD added.tar.gz /", result.output)
        self.assertIn("ADD modified.tar.gz /", result.output)
        self.assertIn("RUN rm -rf /path1-deleted", result.output)
        self.assertNotIn("LABEL", result.output)

    @patch("docker.from_env")
    def test_labels(self, dcl):
        ctn1 = MagicMock()
        ctn1.name = "container1"
        ctn1.attrs = {
            "Config": {
                "Image": "image1:tag1",
            }
        }
        ctn1.diff.return_value = [
            {"Path": "/path1-updated", "Kind": 0},
            {"Path": "/path1-added", "Kind": 1},
            {"Path": "/path1-deleted", "Kind": 2},
        ]
        ctn1.labels = {
            "label1": "value1",
            "ilabel1": "image-value1",
            "ilabel2": "new value",
            "com.docker.compose.project": "proj1",
        }
        ctn1.image.labels = {
            "ilabel1": "image-value1",
            "ilabel2": "image-value2",
        }
        ctn1.get_archive.return_value = ([], {"mode": 0o644})
        dcl.return_value.containers.get.return_value = ctn1
        result = CliRunner().invoke(cli, ["make-dockerfile", "--id", "id123", "--labels"])
        if result.exception:
            raise result.exception
        self.assertEqual(0, result.exit_code)
        self.assertIn("FROM image1:tag1", result.output)
        self.assertIn("ADD added.tar.gz /", result.output)
        self.assertIn("ADD modified.tar.gz /", result.output)
        self.assertIn("RUN rm -rf /path1-deleted", result.output)
        self.assertIn("LABEL label1=value1", result.output)
        self.assertNotIn("ilabel1", result.output)
        self.assertIn("LABEL ilabel2='new value'", result.output)
