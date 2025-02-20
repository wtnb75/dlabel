import unittest
from unittest.mock import patch, MagicMock, ANY
import click
from click.testing import CliRunner
import json
import yaml
import tomllib

from dlabel.main import verbose_option, format_option, docker_option, container_option


class TestVerboseOption(unittest.TestCase):

    def setUp(self):
        # テスト用のダミー関数を作成
        @click.command()
        @verbose_option
        def dummy_command():
            click.echo("Command executed")

        self.runner = CliRunner()
        self.dummy_command = dummy_command

    def test_verbose_option_help(self):
        result = self.runner.invoke(self.dummy_command, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--verbose", result.output)
        self.assertIn("--quiet", result.output)

    @patch('logging.basicConfig')
    def test_verbose_option_default(self, mock_basicConfig):
        result = self.runner.invoke(self.dummy_command)
        self.assertEqual(result.exit_code, 0)
        mock_basicConfig.assert_called_once_with(level="INFO", format=ANY)

    @patch('logging.basicConfig')
    def test_verbose_option_verbose(self, mock_basicConfig):
        result = self.runner.invoke(self.dummy_command, ['--verbose'])
        self.assertEqual(result.exit_code, 0)
        mock_basicConfig.assert_called_once_with(level="DEBUG", format=ANY)

    @patch('logging.basicConfig')
    def test_verbose_option_quiet(self, mock_basicConfig):
        result = self.runner.invoke(self.dummy_command, ['--quiet'])
        self.assertEqual(result.exit_code, 0)
        mock_basicConfig.assert_called_once_with(level="WARNING", format=ANY)


class TestFormatOption(unittest.TestCase):

    def setUp(self):
        @click.command()
        @format_option
        def dummy_command():
            return {"key": "value"}

        self.runner = CliRunner()
        self.dummy_command = dummy_command

    def test_format_option_help(self):
        result = self.runner.invoke(self.dummy_command, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--format", result.output)

    def test_format_option_default_yaml(self):
        result = self.runner.invoke(self.dummy_command)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual({"key": "value"}, yaml.safe_load(result.output))

    def test_format_option_json(self):
        result = self.runner.invoke(self.dummy_command, ['--format', 'json'])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual({"key": "value"}, json.loads(result.output))

    def test_format_option_toml(self):
        result = self.runner.invoke(self.dummy_command, ['--format', 'toml'])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual({"key": "value"}, tomllib.loads(result.output))

    def test_format_option_error(self):
        result = self.runner.invoke(self.dummy_command, ['--format', 'another'])
        self.assertEqual(result.exit_code, 2)


class TestDockerOption(unittest.TestCase):

    def setUp(self):
        @click.command()
        @docker_option
        def dummy_command(client):
            click.echo(f"Client: {client}")

        @click.command()
        @container_option
        def dummy_command_container(client, container):
            click.echo(f"Client: {client}")
            click.echo(f"Container: {container}")

        self.runner = CliRunner()
        self.dummy_command = dummy_command
        self.dummy_command_container = dummy_command_container

    def test_docker_option_help(self):
        result = self.runner.invoke(self.dummy_command, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--host", result.output)
        self.assertIn("-H", result.output)
        self.assertIn("DOCKER_HOST", result.output)

    @patch('docker.from_env')
    def test_docker_option_default(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        result = self.runner.invoke(self.dummy_command)
        self.assertEqual(result.exit_code, 0)
        mock_from_env.assert_called_once()
        self.assertIn("Client: ", result.output)

    @patch('docker.DockerClient')
    def test_docker_option_with_host(self, mock_docker_client):
        mock_client = MagicMock()
        mock_docker_client.return_value = mock_client

        result = self.runner.invoke(self.dummy_command, ['--host', 'tcp://127.0.0.1:2375'])
        self.assertEqual(result.exit_code, 0)
        mock_docker_client.assert_called_once_with(base_url='tcp://127.0.0.1:2375')
        self.assertIn("Client: ", result.output)

    @patch('docker.DockerClient')
    def test_docker_option_with_ssh_env(self, mock_docker_client):
        mock_client = MagicMock()
        mock_docker_client.return_value = mock_client

        result = self.runner.invoke(self.dummy_command, env={'DOCKER_HOST': 'ssh://127.0.0.1'})
        self.assertEqual(result.exit_code, 0)
        mock_docker_client.assert_called_once_with(base_url='ssh://127.0.0.1')
        self.assertIn("Client: ", result.output)

    @patch('docker.from_env')
    def test_docker_container_option_notset(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        ctn1 = MagicMock()
        ctn1.image.tags = ["image1:tag1", "image1:tag2"]
        ctn1.name = "container1"
        ctn2 = MagicMock()
        ctn2.image.tags = ["image2:tag1"]
        ctn2.name = "container2"
        mock_client.containers.list.return_value = [ctn1, ctn2]

        result = self.runner.invoke(self.dummy_command_container)
        if result.exception:
            raise result.exception
        self.assertEqual(result.exit_code, 0)
        mock_from_env.assert_called_once()
        mock_client.containers.list.assert_called_once_with()
        self.assertNotIn("Client: ", result.output)
        self.assertIn("image2:tag1", result.output)

    @patch('docker.from_env')
    def test_docker_container_option_id(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        ctn1 = MagicMock()
        ctn1.image.tags = ["image1:tag1", "image1:tag2"]
        ctn1.name = "container1"
        mock_client.containers.get.return_value = ctn1

        result = self.runner.invoke(self.dummy_command_container, ["--id", "id123"])
        if result.exception:
            raise result.exception
        self.assertEqual(result.exit_code, 0)
        mock_from_env.assert_called_once()
        mock_client.containers.get.assert_called_once_with("id123")
        self.assertIn("Client: ", result.output)
        self.assertIn("Container: ", result.output)

    @patch('docker.from_env')
    def test_docker_container_option_name(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        ctn1 = MagicMock()
        ctn1.image.tags = ["image1:tag1", "image1:tag2"]
        ctn1.name = "container1"
        mock_client.containers.list.return_value = [ctn1]

        result = self.runner.invoke(self.dummy_command_container, ["--name", "container1"])
        if result.exception:
            raise result.exception
        self.assertEqual(result.exit_code, 0)
        mock_from_env.assert_called_once()
        mock_client.containers.list.assert_called_once_with(filters={"name": "container1"})
        self.assertIn("Client: ", result.output)
        self.assertIn("Container: ", result.output)

    @patch('docker.from_env')
    def test_docker_container_option_name_multi(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        ctn1 = MagicMock()
        ctn1.image.tags = ["image1:tag1", "image1:tag2"]
        ctn1.name = "container1"
        ctn2 = MagicMock()
        ctn2.image.tags = ["image2:tag1"]
        ctn2.name = "container2"
        mock_client.containers.list.return_value = [ctn1, ctn2]

        result = self.runner.invoke(self.dummy_command_container, ["--name", "container1"])
        self.assertEqual(result.exit_code, 1)
        self.assertIsNotNone(result.exception)
        mock_from_env.assert_called_once()
        mock_client.containers.list.assert_called_once_with(filters={"name": "container1"})
        self.assertNotIn("Client: ", result.output)
        self.assertNotIn("Container: ", result.output)


if __name__ == '__main__':
    unittest.main()
