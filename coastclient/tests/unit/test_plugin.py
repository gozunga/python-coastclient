# Copyright 2026 Gozunga and Coast contributors
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Check installed namespace loading and OSC option configuration."""
import argparse
from importlib import metadata
import io
from types import SimpleNamespace
from unittest import mock

import fixtures
from openstackclient import shell
from oslotest import base

from coastclient.osc import plugin
from coastclient.osc.v1 import service


class PluginTest(base.BaseTestCase):

    def test_api_version_help_explains_pins_and_precedence(self):
        parser = plugin.build_option_parser(argparse.ArgumentParser())
        action = next(action for action in parser._actions
                      if '--os-coast-api-version' in action.option_strings)
        for text in ('major version 1', '1.0 through 1.31',
                     'overrides --os-coast-api-microversion',
                     'OS_COAST_API_MICROVERSION'):
            self.assertIn(text, action.help)

    def setUp(self):
        super().setUp()
        self.useFixture(fixtures.EnvironmentVariable('OS_COAST_API_VERSION'))
        self.useFixture(fixtures.EnvironmentVariable(
            'OS_COAST_API_MICROVERSION'))

    def test_installed_extension_and_major_mapping(self):
        points = metadata.entry_points(group='openstack.cli.extension')
        extension = next(ep for ep in points if ep.name == 'coast')
        self.assertIs(plugin, extension.load())
        self.assertEqual('coast', plugin.API_NAME)
        self.assertEqual(dict.fromkeys(('1',) + plugin.client.MICROVERSIONS,
                                       'coastclient.v1.client.Client'),
                         plugin.API_VERSIONS)
        self.assertTrue(
            {'coast_stack_create', 'coast_stack_list', 'coast_stack_show',
             'coast_stack_delete', 'coast_stack_event_list',
             'coast_server_add', 'coast_server_list',
             'coast_server_remove', 'coast_server_resize', 'coast_flavor_list',
             'coast_appgroup_create', 'coast_appgroup_list',
             'coast_appgroup_show', 'coast_appgroup_delete',
             'coast_environment_create', 'coast_environment_list',
             'coast_environment_show', 'coast_environment_delete',
             'coast_service_create', 'coast_service_list',
             'coast_service_show', 'coast_app_create', 'coast_app_list',
             'coast_app_show', 'coast_app_webhook_show',
             'coast_app_webhook_regenerate', 'coast_app_delete',
             'coast_app_start',
             'coast_app_stop', 'coast_app_restart', 'coast_app_exec',
             'coast_app_set',
             'coast_service_delete', 'coast_service_start',
             'coast_service_stop',
             'coast_service_restart', 'coast_service_exec',
             'coast_service_set',
             'coast_deploy', 'coast_deployment_list',
             'coast_deployment_show', 'coast_deployment_log',
             'coast_deployment_rollback', 'coast_deployment_cancel',
             'coast_logs',
             'coast_env_set', 'coast_env_unset', 'coast_env_list',
             'coast_domain_add', 'coast_domain_list', 'coast_domain_verify',
             'coast_domain_remove', 'coast_domain_set',
             'coast_domain_show', 'coast_quota_show', 'coast_quota_set'} <=
            {ep.name for ep in metadata.entry_points(
                group='openstack.coast.v1')})

    def test_default_and_explicit_options(self):
        parser = plugin.build_option_parser(argparse.ArgumentParser())
        defaults = parser.parse_args([])
        self.assertEqual('1', defaults.os_coast_api_version)
        self.assertIsNone(defaults.os_coast_api_microversion)
        options = parser.parse_args(['--os-coast-api-version', '1',
                                    '--os-coast-api-microversion', '1.1'])
        self.assertEqual('1', options.os_coast_api_version)
        self.assertEqual('1.1', options.os_coast_api_microversion)

    def test_environment_options(self):
        self.useFixture(fixtures.EnvironmentVariable(
            'OS_COAST_API_VERSION', '1'))
        self.useFixture(fixtures.EnvironmentVariable(
            'OS_COAST_API_MICROVERSION', '1.1'))
        options = plugin.build_option_parser(
            argparse.ArgumentParser()).parse_args([])
        self.assertEqual('1', options.os_coast_api_version)
        self.assertEqual('1.1', options.os_coast_api_microversion)

    def test_cli_microversion_overrides_environment(self):
        self.useFixture(fixtures.EnvironmentVariable(
            'OS_COAST_API_MICROVERSION', '1.23'))
        parser = plugin.build_option_parser(argparse.ArgumentParser())
        for version in ('1.27', '1.28'):
            for option in ('--os-coast-api-microversion',
                           '--os-coast-api-version'):
                options = parser.parse_args([option, version])
                self.assertEqual(version, getattr(
                    options, option[2:].replace('-', '_')))

    def test_hyphenated_file_mount_command_group(self):
        app = shell.OpenStackShell()
        app.stdout = io.StringIO()
        app.stderr = io.StringIO()
        self.assertEqual(0, app.run([
            '--os-auth-type', 'none', 'help', 'coast', 'file-mount', 'list']))
        _, name, _ = app.command_manager.find_command(
            ['coast', 'file-mount', 'list'])
        self.assertEqual('coast file-mount list', name)

    def test_hyphenated_app_deploy_key_command(self):
        app = shell.OpenStackShell()
        app.stdout = io.StringIO()
        app.stderr = io.StringIO()
        self.assertEqual(0, app.run([
            '--os-auth-type', 'none', 'help', 'coast', 'app',
            'deploy-key', 'show']))
        command, name, remainder = app.command_manager.find_command(
            ['coast', 'app', 'deploy-key', 'show', 'APP'])
        self.assertIs(service.ShowAppDeployKey, command)
        self.assertEqual('coast app deploy-key show', name)
        self.assertEqual(['APP'], remainder)
        self.assertNotIn('coast app deploy key show',
                         app.command_manager.commands)
        self.assertRaises(ValueError, app.command_manager.find_command,
                          ['coast', 'app', 'deploy', 'key', 'show', 'APP'])

    def test_invalid_major_does_not_create_client(self):
        instance = SimpleNamespace(_api_version={'coast': '2'})
        self.assertRaises(ValueError, plugin.make_client, instance)

    def test_api_version_1_1_sets_microversion(self):
        instance = SimpleNamespace(
            _api_version={'coast': '1.1'}, session=None, region_name=None,
            interface=None, get_configuration=lambda: {})
        with mock.patch.object(plugin.client, 'Client') as factory:
            plugin.make_client(instance)
        self.assertEqual('1.1', factory.call_args.kwargs['microversion'])

    def test_osc_configuration_normalizes_microversion(self):
        app = shell.OpenStackShell()
        app.stdout = io.StringIO()
        app.stderr = io.StringIO()
        self.assertEqual(0, app.run([
            '--os-auth-type', 'none', '--os-coast-api-microversion', '1.1',
            'help']))
        self.assertEqual('1', app.api_version['coast'])
        self.assertEqual('1.1', app.client_manager.get_configuration()[
            'coast_api_microversion'])
        self.assertIn('openstack.coast.v1',
                      app.command_manager.group_list)

    def test_application_credential_command_has_no_namespace_conflict(self):
        app = shell.OpenStackShell()
        app.stdout = io.StringIO()
        app.stderr = io.StringIO()
        self.assertEqual(0, app.run([
            '--os-auth-type', 'none', 'help', 'application', 'credential',
            'list']))
        command, name, remainder = app.command_manager.find_command(
            ['application', 'credential', 'list'])
        self.assertEqual('application credential list', name)
        self.assertEqual([], remainder)
        self.assertEqual('openstackclient.identity.v3.application_credential',
                         command.__module__)
