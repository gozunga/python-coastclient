python-coastclient
==================

python-coastclient 0.1.0 is the OpenStackClient plugin for Coast application
hosting. It adds ``openstack coast`` commands to manage stacks, servers,
apps, services, standalone databases, deployments, domains, storage and
backups.
Python 3.10 or later is required. The package is licensed under Apache-2.0.

Install the 0.1.0 release from your package index alongside OpenStackClient:

::

   pip install -c https://releases.openstack.org/constraints/upper/2026.1 python-openstackclient python-coastclient==0.1.0

The pip ``-c`` option applies the OpenStack 2026.1 dependency constraints;
``==0.1.0`` selects the client release.

Use your normal OpenStack authentication. Configure a cloud in
``clouds.yaml`` and select it with the ``OS_CLOUD`` environment variable or
``openstack --os-cloud CLOUD``;
an OpenRC file also works. OpenStackClient handles application credentials,
region, interface, CA certificates and TLS verification. Coast uses the
``app-hosting`` service catalog endpoint.

Start by listing plans and stacks, then inspect an existing stack and app:

::

   openstack coast plan list
   openstack coast stack list
   openstack coast stack show shop -c Name -c Status -c 'Engine version'
   openstack coast app list
   openstack coast logs --service web --tail 50

Replace ``shop`` and ``web`` with your resource names or IDs. ``-c`` selects
output columns, ``--service`` selects the app or service to read logs from,
and ``--tail 50`` requests the last 50 lines. Operations that start background
work return after the API accepts the request; use the matching show or list
command to check progress.

The full command reference is in
`doc/source/commands.rst <doc/source/commands.rst>`_,
including arguments, output headings, examples and generated command help.
Use ``openstack coast COMMAND --help`` for help in your installed version.

The client supports REST microversions 1.0 through 1.31 and selects the newest
version supported by both client and server by default. Set
``--os-coast-api-microversion`` or ``OS_COAST_API_MICROVERSION`` to choose an
explicit version within the server's advertised bounds. Feature-specific calls
may require a minimum microversion and preserve a newer selection.
``--os-coast-api-version`` / ``OS_COAST_API_VERSION`` defaults to command
major version ``1`` and also accepts an explicit 1.0–1.31 microversion, which
selects major version 1 and takes precedence over the microversion option. The
server supports 1.0–1.31, so default discovery selects 1.31.
