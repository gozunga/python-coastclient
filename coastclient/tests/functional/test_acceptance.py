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

"""Operator-configured M2 acceptance through OSC only, never raw HTTP."""
import json
import math
import os
import subprocess
import time
from urllib import error
from urllib import parse
from urllib import request
import uuid

from oslotest import base


class AcceptanceTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        config_path = os.environ.get('COAST_ACCEPTANCE_CONFIG')
        if not config_path:
            if os.environ.get('COAST_ACCEPTANCE_REQUIRED') == '1':
                self.fail('COAST_ACCEPTANCE_CONFIG is required for this job')
            self.skipTest('Set COAST_ACCEPTANCE_CONFIG for live M2 acceptance')
        with open(config_path) as stream:
            self.config = json.load(stream)
        required = ('clouds', 'project_id', 'foreign_project_id', 'plan',
                    'worker_plan', 'repo_url', 'first_ref', 'second_ref',
                    'domain', 'quota_restore', 'timeout', 'poll_interval',
                    'url', 'first_marker', 'second_marker')
        self.assertTrue(all(key in self.config for key in required))
        self.assertEqual({'environments', 'servers', 'baremetal_servers',
                          'build_servers', 'apps', 'domains', 'volume_gb'},
                         set(self.config['quota_restore']))
        for key in ('timeout', 'poll_interval'):
            self.assertTrue(math.isfinite(self.config[key]))
            self.assertGreater(self.config[key], 0)
        self.assertTrue(all(value is None or (
            type(value) is int and value >= -1)
            for value in self.config['quota_restore'].values()))
        self.assertEqual({'reader', 'member', 'manager', 'admin', 'foreign'},
                         set(self.config['clouds']))
        self.assertTrue(self.config['first_marker'])
        self.assertTrue(self.config['second_marker'])
        self.assertNotEqual(self.config['first_marker'],
                            self.config['second_marker'])
        url = parse.urlsplit(self.config['url'])
        self.assertEqual('https', url.scheme)
        self.assertEqual(self.config['domain'], url.hostname)
        self.name = 'm5-' + uuid.uuid4().hex[:12]
        # Prove the catalog service exists before creating resources.
        services = self.osc('admin', ['catalog', 'list', '-f', 'json'])
        self.assertTrue(any(row.get('Type') == 'app-hosting'
                            for row in services))

    def osc(self, role, words, version='1.0', expected=None, value=None):
        env = dict(os.environ)
        env['OS_CLOUD'] = self.config['clouds'][role]
        env.pop('OS_COAST_API_VERSION', None)
        env.pop('OS_COAST_API_MICROVERSION', None)
        result = subprocess.run(
            ['openstack', '--os-coast-api-version', '1',
             '--os-coast-api-microversion', version, *words],
            input=value, text=True, capture_output=True, env=env,
            timeout=self.config['timeout'], check=False)
        # Never attach command output or credentials to an assertion.
        if expected is not None:
            self.assertNotEqual(0, result.returncode, 'Expected CLI failure')
            self.assertTrue('HTTP ' + str(expected) in result.stderr,
                            'Expected HTTP status was not returned')
            return None
        self.assertEqual(0, result.returncode, 'OSC acceptance command failed')
        return json.loads(result.stdout) if result.stdout.strip() else None

    def app(self, role, words, **kwargs):
        return self.osc(role, ['coast', *words], **kwargs)

    def wait(self, words, version='1.0', deleted=None, status='ACTIVE'):
        deadline = time.monotonic() + self.config['timeout']
        while time.monotonic() < deadline:
            if deleted is not None:
                # Check absence with list commands; a 404 can be isolation.
                rows = self.app('member', [*words, '-f', 'json'],
                                version=version)
                if not any(row['ID'] == deleted for row in rows):
                    return
            else:
                resource = self.app('member', [*words, '-f', 'json'],
                                    version=version)
                self.assertNotIn(resource.get('status'),
                                 ('ERROR', 'FAILED', 'CANCELLED'))
                if resource.get('status') == status:
                    return resource
            time.sleep(self.config['poll_interval'])
        self.fail('Acceptance polling deadline exceeded')

    def cleanup_resource(self, words, collection, version):
        self.app('member', words, version=version)
        self.wait(collection, version=version, deleted=words[2])

    def restore_quota(self):
        args = ['quota', 'set', '--project', self.config['project_id']]
        for key, value in self.config['quota_restore'].items():
            args.extend(['--' + key.replace('_', '-'),
                         'default' if value is None else str(value)])
        self.app('admin', args)

    def test_full_m2_osc_flow(self):
        project = self.config['project_id']
        foreign = self.config['foreign_project_id']
        for role in ('reader', 'member', 'manager', 'admin'):
            quota = self.app(role, ['quota', 'show', '--project', project,
                                    '-f', 'json'])
            self.assertIn('usage', quota)
        for role in ('reader', 'member', 'manager'):
            self.app(role, ['quota', 'show', '--project', foreign],
                     expected=404)
            self.app(role, ['quota', 'set', '--project', project,
                            '--apps', '0'],
                     expected=403)
        self.addCleanup(self.restore_quota)
        self.app('admin', ['quota', 'set', '--project', project,
                           '--apps', '0'])
        for version in ('1.0', '1.1'):
            self.flow(version)

    def flow(self, version):
        # Each iteration owns a fresh stack. Cleanup runs in child-first order.
        self.resources = []
        try:
            self.run_flow(version)
        finally:
            failed = False
            for function, args, kwargs in reversed(self.resources):
                try:
                    function(*args, **kwargs)
                except Exception:
                    failed = True
            if failed:
                self.fail('Acceptance cleanup failed; inspect owned resources')

    def track(self, function, *args, **kwargs):
        self.resources.append((function, args, kwargs))

    def run_flow(self, version):
        args = ['stack', 'create', self.name, '--plan', self.config['plan'],
                '--worker', 'kind=vm,plan=' + self.config['worker_plan'],
                '-f', 'json']
        if version == '1.1':
            for key in ('image', 'build_server_plan', 'build_server_image'):
                if key in self.config:
                    args.extend(['--' + key.replace('_', '-'),
                                 self.config[key]])
            if 'build_server_plan' in self.config:
                args.append('--build-server')
        stack = self.app('member', args, version=version)['id']
        self.track(self.cleanup_resource, ['stack', 'delete', stack],
                   ['stack', 'list'], version)
        self.wait(['stack', 'show', stack], version)
        appgroup = self.app('member', [
            'appgroup', 'create', self.name, '--stack', stack,
            '-f', 'json'], version=version)['id']
        self.track(self.cleanup_resource,
                   ['appgroup', 'delete', appgroup],
                   ['appgroup', 'list', '--stack', stack], version)
        self.wait(['appgroup', 'show', appgroup], version)
        environment = self.app('member', [
            'environment', 'create', self.name, '--appgroup', appgroup,
            '-f', 'json'], version=version)['id']
        self.track(self.cleanup_resource,
                   ['environment', 'delete', environment],
                   ['environment', 'list', '--appgroup', appgroup],
                   version)
        self.wait(['environment', 'show', environment], version)
        server = self.app('member', [
            'server', 'add', self.name, '--stack', stack, '--kind', 'vm',
            '--role', 'worker', '--plan', self.config['worker_plan'],
            '-f', 'json'],
            version=version)['id']
        self.track(self.cleanup_resource,
                   ['server', 'remove', server, '--stack', stack],
                   ['server', 'list', '--stack', stack], version)
        # No server show command exists; bounded list polling is the API gate.
        deadline = time.monotonic() + self.config['timeout']
        while time.monotonic() < deadline:
            rows = self.app('member', [
                'server', 'list', '--stack', stack, '-f', 'json'],
                version=version)
            row = next((row for row in rows if row['ID'] == server), {})
            self.assertNotEqual('ERROR', row.get('Status'))
            if row.get('Status') == 'ACTIVE':
                break
            time.sleep(self.config['poll_interval'])
        else:
            self.fail('Server provisioning deadline exceeded')
        create = ['app', 'create', self.name,
                  '--appgroup', appgroup, '--environment', environment,
                  '--repo-url', self.config['repo_url'],
                  '--branch', self.config['first_ref'], '--no-deploy']
        self.app('reader', create, version=version, expected=403)
        self.app('member', create, version=version, expected=403)
        self.app('admin', ['quota', 'set', '--project',
                           self.config['project_id'],
                           '--apps', '-1'])
        service = self.app('member', create, version=version)['id']
        self.track(self.cleanup_resource, ['app', 'delete', service],
                   ['app', 'list', '--environment', environment], version)
        self.wait(['app', 'show', service], version, status='STOPPED')
        self.app('foreign', ['service', 'show', service],
                 version=version, expected=404)
        self.app('reader', ['service', 'show', service, '-f', 'json'],
                 version=version)
        self.app('manager', ['service', 'show', service, '-f', 'json'],
                 version=version)
        deployments = []
        for ref, marker in (
                (self.config['first_ref'], self.config['first_marker']),
                (self.config['second_ref'], self.config['second_marker'])):
            deployment = self.app('member', [
                'deploy', service, '--source-ref', ref], version=version)['id']
            deployments.append(deployment)
            self.wait(['deployment', 'show', deployment, '--service', service],
                      version)
            self.app('member', ['deployment', 'log', deployment, '--service',
                                service, '-f', 'json'], version=version)
            if len(deployments) == 1:
                self.add_domain(service, version)
            self.check_https(marker)
        rolled = self.app('member', [
            'deployment', 'rollback', deployments[0], '--service', service],
            version=version)['id']
        self.assertNotIn(rolled, deployments)
        self.wait(['deployment', 'show', rolled, '--service', service],
                  version)
        self.check_https(self.config['first_marker'])
        self.app('member', ['logs', '--service', service, '-f', 'json'],
                 version=version)
        scoped = ['--service', service]
        value = 'acceptance-value'
        result = self.app('member', ['env', 'set', 'M5_ACCEPTANCE', *scoped],
                          version=version, value=value)
        self.assertNotIn(value, json.dumps(result))
        result = self.app('member', ['env', 'list', *scoped, '-f', 'json'],
                          version=version)
        self.assertNotIn(value, json.dumps(result))
        self.app('member', ['env', 'unset', 'M5_ACCEPTANCE', *scoped],
                 version=version)
        for action in ('stop', 'start', 'restart'):
            self.app('manager', ['service', action, service], version=version)
            self.wait(['service', 'show', service], version,
                      status='STOPPED' if action == 'stop' else 'ACTIVE')
        # Put the over-quota gate back in place for the next version.
        self.app('admin', ['quota', 'set', '--project',
                           self.config['project_id'],
                           '--apps', '0'])

    def add_domain(self, service, version):
        scoped = ['--service', service]
        domain = self.app('member', [
            'domain', 'add', self.config['domain'], *scoped, '--auto-dns'],
            version=version)['id']
        self.track(self.cleanup_resource,
                   ['domain', 'remove', domain, *scoped],
                   ['domain', 'list', *scoped], version)
        self.app('member', ['domain', 'verify', domain, *scoped],
                 version=version)
        deadline = time.monotonic() + self.config['timeout']
        while time.monotonic() < deadline:
            rows = self.app('member', [
                'domain', 'list', *scoped, '-f', 'json'], version=version)
            if any(row['ID'] == domain and
                   row['Status'] in ('VERIFIED', 'ACTIVE') for row in rows):
                break
            time.sleep(self.config['poll_interval'])
        else:
            self.fail('Domain verification deadline exceeded')

    def check_https(self, marker):
        deadline = time.monotonic() + self.config['timeout']
        while time.monotonic() < deadline:
            try:
                with request.urlopen(self.config['url'], timeout=min(
                        self.config['poll_interval'],
                        max(0.1, deadline - time.monotonic()))) as response:
                    if marker.encode() in response.read():
                        return
            except (error.URLError, TimeoutError):
                pass
            time.sleep(self.config['poll_interval'])
        self.fail('Sample HTTPS response did not match the expected revision')
