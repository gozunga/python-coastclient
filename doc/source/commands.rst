Resource commands
=================

Output and exit codes
---------------------

List and show headings use sentence case, retaining acronyms such as ID,
GiB, MiB and IP. A field has the same heading in either output; lists begin
with ID, Name and Status where available. JSON and YAML use those headings
as keys. Select a column with its heading, for example ``-c "Engine version"``.
Nested values retain their API field names. Write and action commands returning
resources print the same table as show and accept ``-f json``, ``-f yaml``
and ``-c FIELD`` with the same headings, for example ``ID`` and
``Pending changes``. Delete and remove commands print nothing on success.
A resource response missing its expected envelope is an error.

Size headings include the API units: ``Size (GiB)`` for storage and data
volumes, ``Volume (GiB)`` and ``Storage (GiB)`` for quota capacity,
and ``Size (bytes)`` for backups. Quota show uses these same capacity
labels in its Resource column. Stack data capacity is ``Data volume (GiB)``;
workload memory is ``Memory limit (MiB)`` and backup elapsed time is
``Duration (seconds)``. Export artifacts retain nested API keys, including
``size_bytes`` (bytes). Values are not converted. In the examples below,
``--stack`` selects the parent stack and ``-c`` selects output columns.
Storage and backup items require the UUID returned by their list commands.
For example::

   openstack coast volume list --stack shop -c "Size (GiB)"
   openstack coast storage show STORAGE_ID --stack shop -c "Size (GiB)"
   openstack coast quota defaults -c "Volume (GiB)" -c "Storage (GiB)"
   openstack coast backup show BACKUP_ID -c "Size (bytes)"


Successful commands exit 0. API and connection errors exit 1. Invalid
command-line arguments exit 2 through argparse. A missing resource names
the addressed resource and supplied identifier, for example
``Stack 'reference' not found.`` for either a name or an ID. A missing
parent on a child collection names that parent. Empty references are
usage errors (exit 2). An empty ``--project`` reports
``Project ID must not be empty.``.
Conflict errors retain the API message and include the current status when
returned in the error response. No additional status request is made.

The plugin provides the commands below. Stack, server, app group, environment,
app and service references accept exact names or UUIDs. Names resolve within
the selected parent where a list route is scoped. Storage commands that accept
both ``--stack`` and ``--service`` resolve app and service names within that
stack. Workload show, lifecycle, env, domain, logs and deployment commands
resolve names across the project; they do not accept ``--stack``. Duplicate
names within the lookup scope require an ID. Other child references require
IDs, except domains, which also accept FQDNs, and templates, which use slugs.

The resource a command acts on (its command noun) is positional; other
resources that scope it use ``--options``, such as ``logs --service SERVICE``.
``deploy SERVICE`` and ``app exec APP`` / ``service exec SERVICE`` keep their
positional targets. ``stack event list STACK`` keeps its positional stack
for parity with OpenStack Heat's ``openstack stack event list <stack>``.

The client uses the existing OSC session and ``app-hosting`` catalog.
There is no endpoint default, automatic wait, or POST retry when an
operation has an unknown outcome. OSC retains its normal token refresh
after HTTP 401. Use show and list to check progress after HTTP 202.

The commands follow the `Coast client guide
<https://github.com/gozunga/coast/blob/main/doc/source/client.md>`_
and the `Coast API reference
<https://github.com/gozunga/coast/blob/main/doc/source/api.md>`_.
They use the `OSC command interfaces
<https://docs.openstack.org/python-openstackclient/latest/contributor/plugins.html>`_
and Octavia list/show conventions. Tox installs dependencies using
OpenStack 2026.1 upper-constraints and
the minimum versions in ``requirements.txt``.

Administrators can select ``--all-projects`` (admin only) or
``--project PROJECT`` (admin only, project ID) on stack, server, appgroup,
environment, app, service, deployment, domain, storage, volume, export and
event lists. These options are mutually exclusive, add a Project ID column,
and retain scope on every page. Resource and parent references require
UUIDs with either option; project names are not resolved through Keystone.
Coast enforces authorization and returns HTTP 403 for denied reads.

The list query options are accepted from API 1.0, except storage lists,
which require 1.10. Reading another project's stack parent for server or
volume lists requires 1.18. With ``--project``, these two lists also send
``all_projects=true`` to permit the parent read. The API still reads parents
within the caller's project for appgroup, environment, deployment, domain,
storage and export lists; scope options do not bypass those parent checks.
App/service parent filters also retain the API's project-scoped validation.

``stack show``, ``server show`` and ``volume show`` accept
``--all-projects`` (admin only) for cross-project reads by UUID at API 1.18
or later. Admin stack fields use the headings Ingress security group ID
and Management security group ID. Other show commands retain their existing
options.
``stack event list STACK --all-projects`` (admin only) reads another
project's timeline at API 1.18 or later. Below 1.18 these reads fail with
``--all-projects requires Coast API 1.18 or later.`` The timeline's Project ID
comes from the stack read because timeline events omit ownership.
Scoped export lists also obtain Project ID from a single project-scoped
stack read because export responses omit ownership.


Microversions
-------------

The client accepts REST microversions 1.0 through 1.31. When neither
``--os-coast-api-microversion`` nor ``OS_COAST_API_MICROVERSION`` is set,
discovery selects the newest common version. Explicit selections outside
the server bounds fail. Older versions may omit newer response fields.
Feature-specific calls preserve a newer selected version.
``--os-coast-api-version`` / ``OS_COAST_API_VERSION`` selects command major
version 1 by default, or an explicit microversion within major version 1.
An explicit microversion here takes precedence over the microversion option.
Database commands require API 1.29; default discovery selects the newest
version supported by both client and server.

Stack create
------------

.. code-block:: console

   openstack coast stack create NAME --plan PLAN
       [--description TEXT] [--worker kind=vm,plan=PLAN[,image=IMAGE]]
       [--data-volume-gb SIZE] [--build-server] [--image IMAGE]
       [--build-server-plan PLAN] [--build-server-image IMAGE]
       [--default-domain DOMAIN] [--dns-zone UUID] [--dns-trust UUID]

``--worker`` can repeat under microversion 1.0. Each entry requires ``kind``
(``vm`` or ``baremetal``) and ``plan``. ``--plan`` selects the controller plan
from ``plan list``; ``--description`` describes the stack.
``--data-volume-gb`` sets data volume size as a positive integer in GiB.
``--build-server`` requests a dedicated build server. Omitted options are not
sent; Coast applies its defaults and operator policy.

Select microversion 1.1 with the global ``--os-coast-api-microversion 1.1``
option. At 1.1, ``--worker`` is required and each entry must use ``kind=vm``.
Controller ``--image``, worker ``image``, ``--build-server-plan``, and
``--build-server-image`` require 1.1. Build plan and image selections also
require ``--build-server``. Omitted images use the operator defaults.
An omitted build plan selects the unique build-compatible VM plan; no
match or multiple matches produce an API error. No build server is
requested unless ``--build-server`` is given.
Coast validates plan roles and allowed images.
The CLI does not query Nova or Glance to make those selections.

At API 1.22+, create also accepts ``--default-domain``, ``--dns-zone`` and
``--dns-trust`` to link DNS for new apps. A domain without a zone selects
the most specific matching project zone; a zone without a domain uses the
zone name. Omission sends no explicit DNS link. For example::

   openstack coast stack create shop --plan PLAN \
       --worker kind=vm,plan=WORKER_PLAN --default-domain example.com

Create prints the fields returned in the ``stack`` envelope, with the
transitional status unchanged. It does not claim that provisioning is done.

Stack list
----------

.. code-block:: console

   openstack coast stack list [--long] [--limit SIZE] [--marker UUID]
       [--all-projects | --project PROJECT]
       [--sort-key FIELD] [--sort-dir asc|desc]

Default columns are ID, Name, Status, Plan, Driver, Engine version, and
Created at. Driver appears only when returned by the API.
``--long`` adds Description and Default domain; Fault is excluded.
Created at contains the returned ``created_at`` timestamp.
``--limit`` is a positive API page size, not a total output limit.
Positive integer options reject invalid input with
"Use an integer greater than zero." Non-negative integer options report
"Use zero or a positive integer."
``--marker`` starts after a resource UUID; ``--sort-key`` selects an API
field and ``--sort-dir`` selects ascending or descending order.
The client follows next-page links for the same discovered collection.
Missing values stay empty; no response fields are invented.

Stack show
----------

.. code-block:: console

   openstack coast stack show STACK
       [--all-projects]

Show prints returned fields only, including ``created_at`` when present.
Admin fields such as ``network_id``,
``engine_ref``, ``task_state``, and ``fault`` appear only if Coast returns
them. The client does not fetch them with another credential.

Stack updates and upgrades
--------------------------

``stack set STACK`` accepts ``--name``, ``--description``,
``--default-domain``, ``--dns-zone`` and ``--dns-trust``.
Clear DNS settings with
``--no-default-domain``, ``--no-dns-zone`` and ``--no-dns-trust``,
respectively. Each clearing flag is mutually exclusive with its setter.
Setters reject the literal ``null``.
``stack upgrade STACK --image IMAGE_ID`` submits an app engine upgrade;
``stack upgrade rollback STACK`` submits app engine upgrade rollback.
Both print the returned stack, without waiting for completion. The stack
accepts a name or UUID; duplicate names require an ID. Images require an
explicit Glance UUID because this client has no image lookup helper. Choose
a published app engine server image. The selected Coast microversion is
preserved, and API validation and busy-stack errors are shown to the caller.
STACK is a required positional argument.

Python callers can use ``client.stacks.upgrade(stack_id, image_id)`` and
``client.stacks.upgrade_rollback(stack_id)``. Both return the stack dictionary
and preserve Keystone HTTP exceptions.
These actions return accepted state and do not wait for completion.

Stack events
------------

.. code-block:: console

   openstack coast stack event list STACK [--limit N] [--marker ID]
       [--resource-type {stack,server,app,service}]
       [--outcome {start,success,failure}] [--long]
       [--all-projects]

STACK is an exact stack name or UUID. Duplicate names require a UUID.
Both event commands show the newest 50 matching events by default.
Use ``--limit N`` to return at most N events in total, following next
links as needed. Use ``--marker ID`` with an event ID to continue after
that event in newest-first order. Repeat with the last displayed event ID
to read older history. ``--outcome {start,success,failure}`` filters outcomes.

Default columns are Created at, Resource type, Resource ID, Action,
Outcome and Message. ``--long`` puts ID first and adds Stack ID and
Request ID. Stack events also include Detail as JSON, including ``null``
when unavailable.

Stack timelines use the API's newest-first order. From API 1.8, this
endpoint accepts limit, marker, resource_type and outcome query parameters.
The client sends ``--outcome`` to the API; accepted values are start,
success and failure. The project events endpoint also supports sorting;
the default is created_at descending. Use ``--sort-key FIELD`` and
``--sort-dir {asc,desc}`` on ``event list`` to change that order.
The limit and marker follow the selected order.

``--resource-type`` accepts stack, server, app and service. App and
service require API 1.9 or later; lower selections report
"App and service event filters need Coast API 1.9 or later."

Stack events require API 1.8 or later. Discovery rejects
versions outside the server's advertised bounds without a downgrade.
Older selections or incompatible server bounds report
"Stack events need Coast API 1.8 or later".
At API 1.8, authorization and missing-resource errors keep their HTTP status.

Stack worker resize
-------------------

.. code-block:: console

   openstack coast flavor list
   openstack coast stack resize STACK --flavor FLAVOR

``STACK`` accepts a name or ID, resolved as for ``stack upgrade``.
``FLAVOR`` is a flavor name from ``openstack coast flavor list``.
The command replaces every worker one at a time, with larger flavors only.
Use ``server resize`` to change a single server.

Sends ``{"resize_workers": {"flavor": "FLAVOR"}}`` to
``POST /v1/stacks/{stack_id}/action`` using the selected microversion
(Coast API 1.26 or later is required). It prints the stack returned with
HTTP 202 without waiting. Poll ``openstack coast stack show STACK``
for completion.
Every worker must move to a strictly larger VM flavor; otherwise the API
returns 400 with an explanation. A stack that is not ACTIVE or a busy
server results in 409. API error messages are shown with their HTTP status,
including 404 when the stack is not found.

Stack delete
------------

.. code-block:: console

   openstack coast stack delete STACK [--snapshot-volumes]
       [--snapshot-retention-days DAYS] [--export-before-delete]

``--snapshot-volumes`` requests data volume snapshots before deletion;
``--snapshot-retention-days`` sets their retention period.
``--export-before-delete`` requests an export before deletion.
Retention days must be a non-negative integer and require
``--snapshot-volumes``. Export can be requested alone or with snapshots.
These options use the existing unwrapped API deletion body. With no options,
the request has no body. Coast enforces operator retention limits.

Server add
----------

.. code-block:: console

   openstack coast server add [NAME] --stack STACK --kind vm|baremetal
       --role worker|build --plan PLAN

``--stack`` selects the parent stack, ``--kind`` selects VM or bare metal,
and ``--role`` selects worker or build.
This command provisions a managed server. It does not enroll a BYO server
or add a controller. ``--plan`` is required; select an operator plan from
``openstack coast plan list``. Coast validates the request and
operator provisioning rules. Add prints returned ``server`` fields, without
waiting for provisioning. Omit NAME to use the next ``<role>-N`` name.

Server list
-----------

.. code-block:: console

   openstack coast server list --stack STACK [--limit SIZE] [--marker UUID]
       [--all-projects | --project PROJECT]
       [--sort-key FIELD] [--sort-dir asc|desc]

Default columns are ID, Name, Status, Kind, Role, Floating IP, Hostname,
and Flavor.
Pagination options have the same meaning as stack list.

Server show
-----------

.. code-block:: console

   openstack coast server show SERVER --stack STACK [--all-projects]

``--all-projects`` is admin only and requires UUID references and API 1.18
or later. Show prints the returned public server fields.

Server resize
-------------

.. code-block:: console

   openstack coast server resize SERVER --stack STACK --flavor FLAVOR

Sends ``{"resize": {"flavor": "FLAVOR"}}`` to the stack-scoped server action
at microversion 1.26 or later, including when an older version was selected
globally. A newer selected version is preserved.
``FLAVOR`` is an operator flavor name or ID; the API also accepts plan names.
The command prints the server returned with HTTP 202, typically in
``FAILOVER`` status. It does not wait for completion. Use
``server show SERVER --stack STACK`` to check for
``ACTIVE`` or ``ERROR``.

The API requires a strictly larger VM flavor and active, idle resources.
A 400 response explains invalid or non-growing flavors, including guidance
to add a smaller server and move applications. A 409 explains busy or
inactive resources. These API messages are shown with their HTTP status.
Retained volumes do not grow. An API that lacks 1.26 reports its version
error; the command does not retry the action at an older version.

Server flavors and workload defaults
------------------------------------

Use ``openstack coast flavor list`` to list allowed VM sizes. It requests
``GET /v1/flavors`` at microversion 1.27, preserving a newer selected version
and leaving the selected version unchanged for other calls. It accepts no
query parameters. An older API reports that it needs Coast API 1.27 or later.
Columns are ID, Name, Plan, vCPUs, RAM (MiB), Disk (GiB), and Roles.
Roles use the same alphabetical order and table/JSON/YAML formatting as
``plan list``.

Choose an entry supporting the intended server role. Pass its Plan to
``server add --plan`` (sent as ``server.plan``) or ``server resize --flavor``
(sent as ``resize.flavor``). Resize also accepts the flavor name or ID and
requires a strictly larger size.

``server list`` and ``server show`` use the same Flavor formatter: the
flavor name, the saved ID if the flavor was removed, or blank for null/absent
flavors. Tables display the
name, vCPUs, RAM in MiB and disk in GiB; structured formatter output retains
the returned flavor object. Flavor data is available from API 1.27.

At API 1.28, image and Git sources default to apps when kind is omitted.
CLI app and image-service creation send explicit ``kind=app`` and
``kind=service`` respectively.

Server failover
---------------

``server failover SERVER --stack STACK`` submits failover without waiting
and prints the returned server. Coast enforces administrator authorization.

Server remove
-------------

.. code-block:: console

   openstack coast server remove SERVER --stack STACK

The request has no body. Coast enforces ownership, controller protection,
and resource conflicts.

Output and errors
-----------------

List uses OSC Lister. Commands returning a resource use OSC ShowOne.
They support ``-f json``, ``-f yaml``, and ``-c COLUMN``. Show, create, and
add use the shared headings as keys in machine output and retain nested
values, including null values. For example:

.. code-block:: console

   openstack coast stack show STACK -f json -c ID -c Status
   openstack coast server list --stack STACK -f yaml

Delete and remove use OSC Command and print nothing on success. They do
not support formatter flags or wait for deletion.

API errors exit 1. Messages preserve the HTTP status and include the API
fault message for structured 4xx errors, except 404s, which identify the
addressed resource. Conflicts append a returned current status.
Server failures and proxy responses retain generic messages, such as
Service Unavailable for 503. Authentication,
authorization, schema, microversion, and other API failures also remain
errors. An uncertain write is not retried.

Stack ingress rules
-------------------

.. code-block:: console

   openstack coast stack ingress rule list --stack STACK
   openstack coast stack ingress rule create --stack STACK --protocol tcp
       --port 8443 --source 198.51.100.0/24
   openstack coast stack ingress rule delete RULE_ID --stack STACK

The stack accepts an exact name or UUID. Rule references take an ID.
Omitting ``--source`` uses the API default ``0.0.0.0/0``. TCP and UDP
require ``--port N`` or ``--port N:M`` (1-65535); ICMP omits ports.
``--protocol`` selects TCP, UDP or ICMP; ``--source`` limits incoming
traffic to an IPv4 CIDR and ``--description`` labels the rule.
Ingress commands require a selected API microversion of at least 1.25.
Changes apply at the next successful enabled repair sweep. Managed rules
cannot be deleted. For example::

   openstack coast stack ingress rule list --stack shop
   openstack coast stack ingress rule create --stack shop --protocol icmp

App group commands
------------------

.. code-block:: console

   openstack coast appgroup create NAME --stack STACK [--description TEXT]
   openstack coast appgroup list --stack STACK [--limit SIZE]
       [--all-projects | --project PROJECT]
       [--marker UUID] [--sort-key FIELD] [--sort-dir asc|desc]
   openstack coast appgroup set APPGROUP [--name NAME] [--description TEXT]
   openstack coast appgroup show APPGROUP
   openstack coast appgroup delete APPGROUP

Create and list use the stack's nested app group collection. Show and delete
use the top-level app group item route; they do not require a stack flag.
Resource references accept exact names or UUIDs. Create sends ``name`` and an
optional ``description`` in the ``appgroup`` envelope. Omitted descriptions
are not sent. No parent or project ID is added to the body.

Default list columns are ID, Name, Status, and Stack ID. Pagination and OSC
output options have the same meaning as for stack commands. Create prints
fields returned by Coast; show also fetches summaries of current environments,
apps and services. Both use consistent headings in machine output. Create
preserves the returned transitional status.

Environment commands
--------------------

.. code-block:: console

   openstack coast environment create NAME --appgroup APPGROUP
       [--description TEXT]
   openstack coast environment list --appgroup APPGROUP [--limit SIZE]
       [--all-projects | --project PROJECT]
       [--marker UUID] [--sort-key FIELD] [--sort-dir asc|desc]
   openstack coast environment set ENVIRONMENT [--appgroup APPGROUP]
       [--name NAME] [--description TEXT]
   openstack coast environment show ENVIRONMENT [--appgroup APPGROUP]
   openstack coast environment delete ENVIRONMENT [--appgroup APPGROUP]

These commands manage app group environments, not stacks. Create and
list use the app group's nested environment collection. Show and delete
use the top-level environment item route; they do not require an
app group flag. Use ``--appgroup`` to scope a name to one app group.
Create sends ``name`` and an optional ``description``
in the ``environment`` envelope. No parent or project ID is added to the
body. Default list columns are ID, Name, Status, and App group ID.
Pagination and output follow the app group commands.

App group and environment deletion
----------------------------------

Delete sends no request body and has no cascade or wait option.
It prints nothing on success.
The command does not poll or claim that deletion is complete. Use the
existing show command to check progress; Coast returns 404 after deletion.

Coast enforces ownership and delete constraints. The stack must be ACTIVE,
and the deleted resource must be ACTIVE or ERROR. A non-deleted environment
blocks app group deletion; a non-deleted service blocks environment
deletion. These conflicts and invalid resource states return HTTP 409.
Other-project parents and resources return HTTP 404 without exposing their
existence. These failures remain nonzero CLI errors; the client does not
fetch children, bypass policy, or delete them automatically.

App and service commands
------------------------

Coast has two workload nouns (Q148), with no ``--type`` flag. An **app** is
an application built from git or a registry image. A **service** is a
Coast workload from a template (for example Postgres) or a registry image.
Both live
in an environment of an app group and are rows of ``/v1/services``; the
response field ``kind`` is ``app`` or ``service``.

.. code-block:: console

   openstack coast app create NAME --appgroup APPGROUP --environment ENVIRONMENT
       (--repo-url URL [GIT_OPTIONS] | --image IMAGE --tag TAG)
       --server SERVER [--description TEXT] [--ports PORTS]
       [--cpu-limit NUMBER] [--memory-limit-mb SIZE]
       [--health-check-path PATH] [--env-file FILE|-] [--no-deploy]
   openstack coast app list [--appgroup APPGROUP] [--environment ENVIRONMENT]
       [--all-projects | --project PROJECT]
       [--limit SIZE] [--marker UUID] [--sort-key FIELD] [--sort-dir asc|desc]
   openstack coast app show|delete|start|stop|restart APP

   openstack coast service create NAME --appgroup APPGROUP --environment ENVIRONMENT
       (--template TEMPLATE | --image IMAGE --tag TAG)
       --server SERVER [--description TEXT] [--ports PORTS]
       [--cpu-limit NUMBER] [--memory-limit-mb SIZE]
       [--health-check-path PATH] [--env-file FILE|-] [--no-deploy]
   openstack coast service list [--appgroup APPGROUP] [--environment ENVIRONMENT]
       [--all-projects | --project PROJECT]
       [--limit SIZE] [--marker UUID] [--sort-key FIELD] [--sort-dir asc|desc]
   openstack coast service show|delete|start|stop|restart SERVICE

``--appgroup`` and ``--environment`` select the workload's parents, and
``--server`` selects its worker. ``--image`` names a registry image and
``--tag`` selects its tag. ``--description`` describes the workload.
``--cpu-limit`` sets CPU cores (fractions are accepted);
``--memory-limit-mb`` sets memory in MiB.

``app create`` sends a git source when ``--repo-url`` is given and an image
source when ``--image`` is given; giving both, or neither, is rejected.
``app list`` sends ``kind=app`` and ``service list`` sends ``kind=service``.
Without parent filters, lists include all accessible workloads of that kind.
Parent filters reject missing app groups or environments, including UUIDs.
When both filters are given, the environment must belong to the app group.
Both accept ``--long`` to add Server ID, Kind, Created at and Updated at.
Their parent column is App group ID.
Deploy, deployment, env, domain and logs commands take the name or ID of
either an app or a service. Show, delete, start, stop, and restart also
accept either. Help uses APP for app arguments and SERVICE for service
arguments.
Names must match exactly and be unique across apps and services in the
project. Use an ID when names are duplicated. UUIDs need no name lookup.

Resource IDs are UUIDs. ``--repo-url`` names the Git repository. ``--branch``
selects its branch, ``--build-pack`` selects the build method,
``--dockerfile-path`` and ``--compose-path`` locate build files within the
repository, and ``--base-directory`` sets the build base directory. Git
requires ``--repo-url`` and permits ``--branch``, ``--build-pack
nixpacks|dockerfile|compose|static``, ``--dockerfile-path``,
``--compose-path``, and ``--base-directory``. Image requires ``--image`` (a
registry image name) and an explicit ``--tag``. ``service create`` accepts
``--template TEMPLATE``, a Coast template ID such as ``postgresql`` (see
``template list --stack shop``), or ``--image IMAGE --tag TAG`` for an image
service. App and image-service creation send explicit workload kinds using API
1.11 or later. Options from the other source are rejected before a resource
request. Coast validates source content, parent ownership, placement, and
lifecycle state.

Worker selection is required. UUIDs are accepted directly. Other omitted
optional fields are not sent. CPU limits must be positive and
finite; memory limits must be positive integers. ``--no-deploy`` sends
``deploy: false``.

``--env-file FILE|-`` reads initial dotenv variables from a UTF-8 file or
stdin, using the same parser and per-key markers as ``env set`` below.
It sends ``service.env`` as a mapping of keys to objects containing ``value``
and any marked ``secret`` or ``build_time`` flags before the first deployment.
Initial env requires API 1.0 or later; API 1.6 changes its build-time default.
Masked values are rejected. For example::

   openstack coast app create web --appgroup website --environment production \
       --server worker-1 --image nginx --tag stable --env-file .env

App and service create/set accept ``--health-check none|http``. New apps
use ``none`` by default at API create time. Ports are optional with ``none``;
Create and set accept ``--ports`` as a comma-separated string (up to 255
characters). Set accepts ``--no-ports`` to clear the field to null; it is
mutually exclusive with ``--ports``. Every deployment must
succeed and its container must run stably. ``http`` adds an HTTP probe.
``--health-check-port PORT`` and ``--health-check-path PATH`` can be set
independently to edit stored probe settings. They cannot be combined with
``--health-check none``; the API validates the settings. The API defaults
the probe port to the first app port and the path to ``/``. The path uses the
existing ``healthcheck_path``
wire field.
App and service set accept ``--no-health-check-port`` to send
``health_check_port: null``, restoring selection from the first app port.
It is mutually exclusive with ``--health-check-port``; omitting both
preserves the stored port.
Existing apps retain HTTP probing until changed.

App and service set accept ``--enable-default-domain`` or
``--disable-default-domain`` to send ``default_domain_enabled`` as true or
false. Show includes Primary URL, Default domain enabled, Task owner and
Task started at when returned.

Sending any health field requires microversion 1.4 or newer. Omitting
``--health-check`` preserves the existing mode on set. For example::

   openstack coast app set web --health-check none
   openstack coast app set web --health-check http --health-check-port 8080 \
       --health-check-path /ready

App and service list columns are ID, Name, Status, App group ID, and
Environment ID. A Health check column is added when the API returns
``health_check``.
Show prints returned public fields with OSC formatters and shared
headings. Show also adds ``url`` and ``worker_ip``. ``url`` is the
returned ``default_url``. ``worker_ip`` is the worker ``floating_ip`` from
``GET /v1/stacks/{stack_id}/servers/{server_id}``. Coast has no
``GET /v1/servers/{server_id}`` route. If that show call fails, show reads
the stack server list. If neither call returns an address, show uses the
first ``A`` value in ``dns_records``. A lookup error does not hide the
service. Existing fields stay. List supports the existing pagination
options.

``app set APP`` accepts ``--branch``,
``--build-pack nixpacks|dockerfile|compose|static``, ``--dockerfile-path``,
``--compose-path`` and ``--base-directory`` with the same values as create.
Only supplied fields are sent in the ``service.source`` update envelope.
Omitted source settings are preserved. The repository URL cannot be changed.
Image apps and template services report
``Git source options require a Git app.`` for these options.
Git options cannot be combined with ``--image`` or ``--tag``; this reports
``Git source options cannot be combined with --image or --tag.``

``app set APP`` and ``service set SERVICE`` accept ``--no-cpu-limit`` and
``--no-memory-limit`` to clear the corresponding limit. Each is mutually
exclusive with its setter (``--cpu-limit`` or ``--memory-limit-mb``).
CPU limits on create and set must be positive finite numbers; invalid values
report ``Use a positive finite CPU limit.``
Worker names on create resolve within the selected app group's stack.

Source validation reports "Specify --repo-url or --image." for an app
without a source, or "Specify --template or --image." for a service.
Conflicts report "Choose either --repo-url or --image/--tag." or
"Choose either --template or --image." Image sources require both options:
"--image requires --tag." or "--tag requires --image."

App and service set accept ``--name``, ``--description``, ``--image`` and
``--tag`` as well as the health, port, domain and resource-limit options
above. Image changes use the ``service.source`` envelope; omitted settings
are preserved and Coast validates workload support.

App webhooks
------------

.. code-block:: console

   openstack coast app webhook show --app APP
   openstack coast app webhook regenerate --app APP

APP accepts a name or UUID. These calls use API 1.3 or later and preserve
a newer selected microversion. Show prints the returned deploy and provider
webhook URLs; regenerate issues a new token and invalidates the old URLs.
App show includes visible webhook URLs when returned.

Container terminal
------------------

.. code-block:: console

   openstack coast app exec APP [-- COMMAND [ARGUMENT ...]]
   openstack coast service exec SERVICE [-- COMMAND [ARGUMENT ...]]

``app exec`` opens a terminal in an app. ``service exec`` opens a terminal
in a service. The target is a name or UUID. A name must match one resource
of that kind. Use an ID when names are duplicated.

With no command, the client opens an interactive terminal. When standard
input is a terminal, the client puts it in raw mode and restores the
previous mode on exit and on signals. The client sends the window size at
the start and when the window size changes. The client exits with the
remote status.

With a command, the client does not change the local terminal. It runs
that command, writes the remote output, and exits with the remote status.
Put ``--`` before the command when an argument starts with a dash.
For example::

   openstack coast app exec web
   openstack coast service exec db -- id

The console request is ``POST /v1/apps/{id}/console`` or
``POST /v1/services/{id}/console``. That call uses at least microversion 1.9
and does not change the selected microversion. The client sends
``{"command": ["COMMAND", "ARGUMENT", ...]}`` only when a command is
given. With no command, the request has no body.

The response supplies a ``wss://`` address. The client connects to it with
the same CA settings as the OpenStack session. The client does not print
the address. A server that rejects microversion 1.9 reports that a
container terminal needs Coast API 1.9 or later. Coast microversion
selection includes 1.9. The default selects the newest common version.

Deployment commands
-------------------

.. code-block:: console

   openstack coast deploy SERVICE [--source-ref REF]
   openstack coast deployment list --service SERVICE [--limit SIZE]
       [--all-projects | --project PROJECT]
       [--marker UUID] [--sort-key FIELD] [--sort-dir asc|desc]
       [-c Fault]
   openstack coast deployment show DEPLOYMENT_ID --service SERVICE
   openstack coast deployment log DEPLOYMENT_ID --service SERVICE
   openstack coast deployment rollback DEPLOYMENT_ID --service SERVICE
   openstack coast deployment cancel DEPLOYMENT_ID --service SERVICE

Deploy always sends the ``deployment`` envelope. Without ``--source-ref``,
it is empty and Coast selects the source reference. Rollback creates a
new deployment and prints that new resource, not the target deployment.
List columns are ID, Status, Number, Source ref, Resolved ref, and Trigger.
``-c Fault`` adds the Fault column. That text is wide, so it is not a
default column. Show prints public fields only, including ``fault`` when
the API returns it. ``fault`` is the short failure reason. Internal
deployment metadata is not shown. API 1.5 and later also return
``runtime_log``, shown as Runtime log (select with ``-c 'Runtime log'``).
A null Runtime log is displayed as ``None`` in tables and ``null`` in
JSON/YAML.
``deployment list --long --service web`` adds Server ID, Rollback of,
Started at, Finished at, Created at and Updated at. Fault remains opt-in.
Deployment log prints Deployment ID, Complete, and Lines with OSC ShowOne.
A false Complete value does not cause a second request. Unavailable output
prints the API reason or note to stderr. Output captured at failure prints
a notice with its capture time and deployment ID to stderr before output.
These notices also appear with ``-f json`` or ``-f yaml``; Deployment ID,
Complete and Lines remain unchanged.

Logs and asynchronous output
----------------------------

.. code-block:: console

   openstack coast logs --service SERVICE [--since TIMESTAMP] [--tail N]

App, service and container logs use OSC Lister with Timestamp, Stream, and Line
columns.
``--tail`` accepts zero or a positive integer. ``--since`` is passed
unchanged; Coast validates the ISO UTC timestamp. Omitted filters are not
sent; the live API defaults to 200 lines and zero returns no lines.
Both ``logs`` and ``deployment log`` make one log request per invocation.
Neither command accepts ``--follow`` or polls for more output, including when
a deployment reports Complete as false.
Log reads are one-shot JSON API reads, not streams. Empty app or service
logs print an empty list with ``-f json``. When the API reports output
unavailable, its reason or note is printed to stderr. Stored output captured
at failure prints a notice with the capture time and deployment ID to stderr
before the rows. Other API notes are also printed to stderr. Notices apply
to table, JSON and YAML formats; the log rows and columns stay unchanged.
Returned masking stays unchanged.
HTTP 503 reports Service Unavailable without engine error details;
HTTP 500 remains an error.

App/service create and set, start/stop/restart, deploy and deployment
rollback/cancel use ShowOne. They accept ``-f json`` and ``-c FIELD``
with the same headings as show. Delete prints nothing on success.
These commands do not wait, poll, retry an uncertain write, or claim
completion. Use show or list to check progress. App and service create
require ``--server``: a worker server in the app group's stack.

Env commands
------------

.. code-block:: console

   openstack coast env set --service SERVICE [KEY=VALUE ...] [KEY] [--from-file FILE|-] [--secret|--no-secret] [--build-time|--no-build-time]
   openstack coast env replace --service SERVICE [KEY=VALUE ...] [KEY ...]
       [--from-file FILE] [--secret | --no-secret]
       [--build-time | --no-build-time]
   openstack coast env unset KEY --service SERVICE
   openstack coast env list --service SERVICE [-f json|yaml]

Set merges all supplied keys in one PATCH. Replace uses PUT.
Replace deletes all other keys; include every key to retain.
Set requires at least one key; replace also accepts an explicitly empty file
or stdin to clear all keys. Unset sends one key as null.
``--service`` selects the app or service whose variables are being edited.
Pairs split at the first ``=``; empty values and further equals signs are
preserved. Duplicate command-line keys fail. Keys must match
``[A-Za-z_][A-Za-z0-9_]*`` and have at most 255 characters.

Argument values are visible in shell history, the process list and ``--debug``
output. For secrets, use a bare KEY or ``--from-file``. A bare KEY prompts
without echo on a terminal. Redirected stdin supplies exactly one bare KEY.
Exactly one trailing LF or CRLF is removed; all other content is preserved.
Empty stdin (including input empty after removing that newline) is rejected
with the key name. Use ``KEY=`` for an intentional empty value. The display
mask is validated after removing the trailing newline. For example::

   openstack coast env set --service web APP_MODE=production LOG_LEVEL=info
   openstack coast env set --service web DATABASE_PASSWORD --secret --no-build-time
   openstack coast env set --service web DATABASE_PASSWORD --secret --no-build-time < value-file
   openstack coast env set --service web --from-file .env

Use ``--from-file -`` to read dotenv entries from stdin. It cannot be
combined with any bare KEY; the conflict fails before reading stdin.
Dotenv accepts literal single quotes, escaped double quotes and optional
``export``. Inline comments stay outside quotes (after whitespace for unquoted
values).
Duplicate keys and invalid lines report line numbers; no interpolation occurs.
Comma-combinable ``# secret``, ``# runtime-only`` and ``# build-time`` markers
set per-key flags; runtime-only plus build-time is invalid. CLI flags apply
where the corresponding marker is absent; contradictions name the key and line.
Arguments override file entries. ``********`` means keep the stored value and
is rejected: omit that key. Protect files containing secrets. For example,
load a file containing ``API_TOKEN=token # secret, runtime-only`` with::

   openstack coast env set --service web --from-file .env

For ``env set`` (PATCH), omitted flags preserve stored flags on existing keys;
new keys use create defaults. For
``env replace`` (PUT), omitted flags use create defaults for every key.
An explicitly supplied empty dotenv file (``--from-file FILE``) or empty
stdin (``--from-file -``) sends ``{"env": {}}`` and clears all keys.
``env set`` continues to reject empty input.
The secret create default is false. The build-time create default is true
for non-template apps and services at API 1.6+ and false otherwise.
``--build-time`` also makes the
variable available during image builds (build args). Every variable is
available at runtime.
Non-template workloads default to build-time on (API 1.6+); template services
are runtime-only
and reject build-time on. ``--secret`` stores values write-only.

Coast stores env values in Barbican; its database holds secret references.
Values marked secret are write-only through the API. On deployment, the
conductor retrieves the values and passes them to the app engine for runtime
use and, when build-time is enabled, for builds. Secret does not prevent a
build from copying a value into image layers or output; use
``--secret --no-build-time`` for runtime credentials.
The API returns null for secret values.

``-field=password`` prints only the password field.
To copy it from HashiCorp Vault into a runtime-only secret::

   vault kv get -field=password secret/web | openstack coast env set --service web DATABASE_PASSWORD --secret --no-build-time

The display mask ``********`` is rejected as an input value.

List columns are Key, Value, Secret, and Version. Use
``openstack coast env list --service web --long`` to include Build time;
older responses that omit the flag leave that cell blank. All non-null
values are masked as ``********``, as are write-only secrets; other null
stays null.
Set, replace and unset print the masked returned env object as JSON without
its envelope. For apps and image services,
changes take effect on the next deployment. For template services already
provisioned in the app engine, Coast submits env updates immediately.
Transport payload logging is disabled.

Domain commands
---------------

.. code-block:: console

   openstack coast domain add [DOMAIN] --service SERVICE
       [--primary | --no-primary] [--zone DNS_ZONE_ID] [--host HOST]
       [--dns-trust TRUST_ID] [--record-type {A,AAAA,CNAME}]
       [--auto-dns | --no-auto-dns] [--tls letsencrypt|none]
   openstack coast domain set DOMAIN --service SERVICE [--fqdn FQDN]
       [--primary | --no-primary] [--zone DNS_ZONE_ID] [--host HOST]
       [--dns-trust TRUST_ID] [--record-type {A,AAAA,CNAME}]
       [--auto-dns | --no-auto-dns] [--tls letsencrypt|none]
   openstack coast domain list --service SERVICE [--limit SIZE]
       [--all-projects | --project PROJECT]
       [--marker UUID] [--sort-key FIELD] [--sort-dir asc|desc]
   openstack coast domain show DOMAIN --service SERVICE
   openstack coast domain verify DOMAIN --service SERVICE
   openstack coast domain remove DOMAIN --service SERVICE

DOMAIN is the lowercase FQDN, for example ``www.example.com``. Coast validates
the name and enforces ownership and conflicts. An omitted auto-DNS flag
is not sent; the API default is false. The client makes no DNS calls.
Add prints the returned domain, including DNS records. Verify submits
``{"verify": {}}`` to Coast. Domain arguments accept an FQDN or UUID; the
service option accepts an app or service name or UUID.

``--primary`` makes the domain primary; the mutually exclusive
``--no-primary`` sends ``primary=false`` to clear that selection.
``--dns-trust TRUST_ID`` selects the delegated DNS trust (``dns_trust_id``).
``--record-type`` chooses A, AAAA or CNAME for automatic DNS management.
On set, ``--fqdn`` changes the fully qualified domain name.
For example, rename a domain and select its managed DNS record type::

   openstack coast domain set old.example.com --service web \
       --fqdn web.example.com --auto-dns --record-type A \
       --dns-trust TRUST_ID --primary

``--auto-dns`` asks Coast to manage
DNS records; ``--no-auto-dns`` uses records you publish yourself.
``--host`` and ``--zone`` can replace the positional FQDN on add and
require ``--auto-dns``. An empty host
selects the zone apex. ``dns zone list [--dns-trust DNS_TRUST_ID]`` reads
Designate zones; omit the trust to list the caller project's zones.

``--tls`` accepts ``letsencrypt`` or ``none``. Add sends it only when the
flag is present, and that call uses at least microversion 1.2. Set sends only
supplied routing fields; supplying TLS uses at least microversion 1.2.
List, show,
verify, and remove keep the selected microversion. List columns are ID,
Status, FQDN, TLS, and Primary. Show prints returned public fields,
including ``tls`` and ``dns_records``. To read the DNS instructions after
adding a domain::

   openstack coast domain show web.example.com --service web -c 'DNS records'

Publish each returned record at your DNS provider, then submit verification::

   openstack coast domain verify web.example.com --service web

List follows collection pagination and uses OSC formatters.
Add, set and verify use ShowOne and accept ``-f json`` and ``-c FIELD``.
They use the same headings as show. Remove prints nothing on success.
HTTP 202 status is not proof of completion: no command waits or polls.

Quota commands
--------------

.. code-block:: console

   openstack coast quota show [--project PROJECT] [-f json|yaml] [-c FIELD]
   openstack coast quota set --project PROJECT [--environments LIMIT] [--servers LIMIT]
       [--baremetal-servers LIMIT] [--build-servers LIMIT] [--apps LIMIT]
       [--domains LIMIT] [--volume-gb LIMIT] [--storage-gb LIMIT]

Explicit project IDs must be UUIDs; their dashed or undashed spelling is
preserved for Keystone policy matching. Show defaults to the authenticated
project. A credential without a project must supply ``--project PROJECT``. There is
no project-name lookup. Show prints one row per returned resource limit,
with Resource, Limit, In use and Reserved columns. Limit is the configured
numeric limit (or null); usage comes from ``usage.in_use`` and
``usage.reserved``. Missing counters stay blank rather than becoming zero.
JSON and YAML contain a list of rows. It does not invent counters or limits.

Set requires at least one limit. Invalid limits report
"Use -1 for unlimited, 0 or more, or 'default'."
LIMIT is an integer at least -1, or the
literal ``default``. -1 means unlimited, zero blocks new allocations,
and default resets that limit to its configured default. The literal
``null`` is rejected. Omitted options leave limits unchanged.
The ``environments``
wire key counts stacks and ``apps`` counts services. Set uses ShowOne with
one heading per resource limit, such as ``Apps`` and ``Volume (GiB)``, and
accepts ``-f json`` and ``-c FIELD``. It prints the returned quota
without fabricated usage and never waits. The API returns 200 for a
successful update.

``quota set`` also accepts ``--storage-gb`` for persistent storage capacity in
GiB.

Quota commands use the selected microversion, including the negotiated
default.
Coast enforces own-project reader/admin access for show and admin-only
access for set. The client does not inspect roles or bypass authorization.
``quota list``, ``quota defaults`` and
``quota delete --project PROJECT`` expose
the administrative inventory, configured defaults and override reset.
Authorization is enforced by Coast policy.

Quota show uses an optional ``--project PROJECT``; set and delete require it.
Hyphenated options map to the existing
underscore wire keys. The literal ``default`` selects a config-default reset;
it is distinct from an omitted option. Empty set input is rejected locally.
Show presents limits and usage together as resource rows.


Other resource commands
-----------------------

The following groups use OSC formatters for list, show and accepted resource
output. Stack, server and service options accept names or UUIDs. DNS zone and
trust options require UUIDs. Child item references use IDs; templates use
slugs and containers use the IDs returned by ``container list``.

.. list-table:: Resource command groups
   :header-rows: 1
   :widths: 20 35 45

   * - Group
     - Commands
     - Parent / write options
   * - storage
     - list, show, create, set, delete, attach, detach
     - ``--stack``; at API 1.28, create requires
       ``--type volume|bind --service --container-path``. Volume mounts
       also require ``--name --size-gb``; bind mounts require ``--host-path``
       and omit name and size. ``--type`` selects the mount type; ``--service`` selects its workload,
       and ``--container-path`` gives the destination inside the container.
       ``--name`` labels a volume mount and ``--size-gb`` gives its size in GiB.
       ``--host-path`` gives the bind source path on the host.
       Both accept ``--read-only on|off`` for read-only or read-write access.
       Attach requires ``--service --container-path``. Set accepts name, container path,
       host path and read-only.
       List accepts ``--all-projects | --project PROJECT`` (admin only).
   * - volume
     - list, show, create, set, delete, snapshot
     - ``--stack``; create: ``--name --size-gb`` with optional
       ``--server --volume-type --mount-path``. Set extends
       ``--size-gb``. Coast enforces managed-volume restrictions.
       List accepts ``--all-projects | --project PROJECT`` (admin only).
       ``volume show VOLUME_ID --stack STACK [--all-projects]`` supports
       admin reads by UUID at API 1.18 or later.
   * - file-mount
     - list, show, create, set, delete
     - ``--service``; writes accept ``--container-path --resource-name``
       and ``--file`` with UTF-8 content. Create requires path and file;
       set permits metadata or content changes. Content is write-only.
       ``--file -`` reads stdin.
   * - backup
     - list, show, create, delete, download, restore; policy show/set
     - List/create/policy use ``--service``. Item commands take BACKUP_ID.
       Download requires a new destination ``--file`` and validates gzip
       before publishing it. Restore accepts ``--mode new_copy --name``
       or ``--mode overwrite --confirmation``. ``new_copy`` creates a service
       named by ``--name``; ``overwrite`` restores the original service and
       requires its current name in ``--confirmation``. Backups require a cloud
       Swift-compatible object-storage endpoint and are not verified end to end
       in 0.1.0; full verification is tracked for 0.1.1 (gozunga/coast#124).
       Policy set accepts
       ``--enabled on|off --engine --container --hour --minute
       --keep-daily --keep-weekly``. Scheduling times are UTC.
       The first policy update requires engine and container; engines are postgres, mysql, mariadb and mongodb. Omitted
       fields preserve a saved policy. New policies default to disabled,
       hour 2, minute 0, 14 daily and 8 weekly backups. Retention limits
       are 0-365 daily and 0-104 weekly.
   * - compose
     - show, set
     - ``--service``; set reads UTF-8 YAML using ``--file``. Save updates
       desired state; deploy separately to apply it. ``--file -`` reads stdin.
   * - container
     - list, show, logs
     - ``--service``; show/logs take the returned container ID such as
       ``c-web``. Logs accept ``--tail`` (0-10000, default 200) and
       ``--since`` (exclusive ISO timestamp; naive times mean UTC). Omit
       since for no time filter. Zero tail returns no lines.
   * - metrics
     - show
     - ``--service``; optional ``--range 15m|1h|6h|24h|7d|30d``.
       Omitting the range uses the API default of ``1h``.
       Example: ``openstack coast metrics show --service web --range 24h``.
   * - export
     - list, show, create, delete, renew
     - ``--stack``; create optionally takes repeatable
       ``--volume VOLUME_ID``. Renew signs download URLs without extending
       object retention.
       List accepts ``--all-projects | --project PROJECT`` (admin only).
   * - plan
     - list
     - Reads the operator catalog without pagination parameters. Columns are
       Name, Kind, Roles and Description. Roles use alphabetical order,
       as in flavor list: ``build, worker`` in tables and a real list in
       JSON/YAML. Descriptions are operator-provided text. Use flavor list
       to map flavors to plans.
   * - template
     - list, show
     - ``--stack STACK``; show takes a template slug.
   * - event
     - list
     - List accepts ``--all-projects | --project PROJECT`` (admin only).
       Project-wide events; filters include ``--resource-type``,
       ``--resource-id``, ``--stack``, ``--action``, ``--request-id`` and
       ``--outcome {start,success,failure}``. Supports ``--limit N``,
       ``--marker ID`` and ``--long`` as described under Stack events.
       Stack and resource ID filters include events of deleted resources.

Storage list
------------

.. code-block:: console

   openstack coast storage list --stack STACK [--service SERVICE]
       [--all-projects | --project PROJECT]
       [--limit SIZE] [--marker UUID] [--sort-key FIELD] [--sort-dir asc|desc]

STACK and SERVICE accept names or UUIDs. ``--service`` filters mounts using
Coast's service filter. App and service names resolve only within STACK.
The list follows all API pages.
Columns are ID, Type, Name, Source, Container path, Size (GiB), Read-only,
and Status, in that order. Source is the API-returned volume name or bind
host path at microversion 1.28; Container path is the mount destination,
matching ``storage show``. Earlier APIs may return a source classification.
Missing fields stay blank, including Type and Read-only before 1.28.
Bind mounts have no name or size. Read-only preserves the API boolean.

Mount create and set save the change; it applies on the next deploy.
When a write response requests redeployment, the client prints a deploy
reminder to stderr. For example, with a negotiated API 1.28::

   openstack coast storage create --stack shop --type volume \
       --service web --container-path /data --name data \
       --size-gb 10 --read-only off
   openstack coast deploy web
   openstack coast storage create --stack shop --type bind \
       --service web --container-path /data \
       --host-path /srv/data --read-only on
   openstack coast deploy web


Complete command help
---------------------

The reference below is built from the installed command parsers, including
all command descriptions and option help. Commands that start background
work return after the API accepts the request; use the matching show or
list command to check progress. Log reads return once. Container terminals
stay connected until the remote command or interactive session exits.

.. autoprogram-cliff:: openstack.coast.v1
   :application: openstack


List query filters
------------------

These options send exact-match API filters. Combine options to require all
matches; omitted options send no filter. Values are case-sensitive, including
uppercase statuses. Timestamp equality filters are intentionally omitted.
``--id ID`` filters by exact ID on ``stack list``, ``server list``,
``appgroup list``, ``environment list``, ``app list``, ``service list``,
``deployment list``, ``domain list``, ``volume list``, ``storage list``,
``export list`` and ``event list``. It sends the API filter ``id``.
Parent selectors described above still apply. Plans, flavors, templates,
backups, file mounts, containers, environment variables, quotas and DNS zones
have no additional public-field list filters.

``stack list``:

* ``--name NAME``: match name.
* ``--status STATUS``: match status. Choices: ``ACTIVE``, ``BUILDING``,
  ``DEGRADED``, ``DELETED``, ``DELETING``, ``ERROR``, ``PENDING``,
  ``ROLLING_BACK``, ``UPGRADING``.
* ``--plan PLAN``: match plan.
* ``--description DESCRIPTION``: match description.
* ``--default-domain DEFAULT_DOMAIN``: match default domain.
* ``--dns-zone-id DNS_ZONE_ID``: match dns zone id. Use an ID.
* ``--engine-version ENGINE_VERSION``: match engine version.
* ``--fault FAULT``: match fault text.

``server list``:

* ``--name NAME``: match name.
* ``--status STATUS``: match status. Choices: ``ACTIVE``, ``BUILDING``,
  ``DELETING``, ``ERROR``, ``FAILOVER``, ``PENDING``, ``UNHEALTHY``.
* ``--kind KIND``: match kind. Choices: ``vm``, ``baremetal``.
* ``--role ROLE``: match role. Choices: ``control``, ``worker``, ``build``.
* ``--hostname HOSTNAME``: match hostname.
* ``--floating-ip FLOATING_IP``: match floating IP.

``appgroup list``:

* ``--name NAME``: match name.
* ``--status STATUS``: match status. Choices: ``ACTIVE``, ``DELETED``,
  ``DELETING``, ``ERROR``, ``PENDING``.
* ``--description DESCRIPTION``: match description.

``environment list``:

* ``--name NAME``: match name.
* ``--status STATUS``: match status. Choices: ``ACTIVE``, ``DELETED``,
  ``DELETING``, ``ERROR``, ``PENDING``.
* ``--description DESCRIPTION``: match description.

``app list and service list``:

* ``--name NAME``: match name.
* ``--status STATUS``: match status. Choices: ``ACTIVE``, ``DEGRADED``,
  ``DELETING``, ``DEPLOYING``, ``ERROR``, ``PENDING``, ``STARTING``,
  ``STOPPED``.
* ``--stack STACK``: match stack ID. Accepts a name or UUID.
* ``--server SERVER``: match server ID. Accepts a name or UUID.
* ``--template-id TEMPLATE_ID``: match template ID. Use an ID.
* ``--description DESCRIPTION``: match description.
* ``--ports PORTS``: match ports.
* ``--replicas REPLICAS``: match replicas. Integer value.
* ``--memory-limit-mb MEMORY_LIMIT_MB``: match memory limit in MiB.
  Integer value.
* ``--auto-deploy`` / ``--no-auto-deploy``: require auto deploy true / false.
* ``--cpu-limit CPU_LIMIT``: match CPU limit in cores; a finite number.
* ``--health-check HEALTH_CHECK``: match HTTP probe mode; ``none`` or ``http``.
* ``--health-check-port HEALTH_CHECK_PORT``: match HTTP probe port;
  integer 1-65535.
* ``--health-check-path HEALTH_CHECK_PATH``: match the stored HTTP probe path;
  sends the API filter ``healthcheck_path``.
* ``--current-deployment-id CURRENT_DEPLOYMENT_ID``: match deployment ID.

``deployment list``:

* ``--status STATUS``: match status. Choices: ``ACTIVE``, ``BUILDING``,
  ``CANCELLED``, ``DEPLOYING``, ``FAILED``, ``PENDING``, ``ROLLED_BACK``,
  ``SUPERSEDED``.
* ``--number NUMBER``: match number. Integer value.
* ``--source-ref SOURCE_REF``: match source ref.
* ``--server SERVER``: match server ID. Accepts a name or UUID.
* ``--trigger TRIGGER``: match trigger. Choices: ``api``, ``rollback``,
  ``webhook``, ``reconcile``.
* ``--rollback-of ROLLBACK_OF``: match rollback of.
* ``--fault FAULT``: match fault text.

``domain list``:

* ``--status STATUS``: match status. Choices: ``ACTIVE``, ``ERROR``,
  ``PENDING_DNS``, ``PENDING_VERIFICATION``, ``VERIFIED``.
* ``--fqdn FQDN``: match FQDN.
* ``--tls TLS``: match TLS. Choices: ``letsencrypt``, ``none``.
* ``--auto-dns`` / ``--no-auto-dns``: require automatic DNS true / false.
* ``--dns-mode DNS_MODE``: match DNS mode. Choices: ``designate``, ``manual``.

``volume list``:

* ``--name NAME``: match name.
* ``--status STATUS``: match status. Choices: ``AVAILABLE``, ``CREATING``,
  ``DELETING``, ``ERROR``, ``IN_USE``, ``SNAPSHOTTING``.
* ``--server SERVER``: match server ID. Accepts a name or UUID.
* ``--volume-type VOLUME_TYPE``: match volume type.
* ``--mount-path MOUNT_PATH``: match mount path.
* ``--purpose PURPOSE``: match purpose. Choices: ``data``, ``docker``.
* ``--size-gb SIZE_GB``: match size in GiB. Integer value.

``storage list``:

* ``--name NAME``: match name.
* ``--status STATUS``: match status. Choices: ``AVAILABLE``, ``IN_USE``.
* ``--server SERVER``: match server ID. Accepts a name or UUID.
* ``--type TYPE``: match type. Choices: ``volume``, ``bind``.
* ``--container-path CONTAINER_PATH``: match container path.
* ``--host-path HOST_PATH``: match host path.
* ``--source SOURCE``: match source.
* ``--origin ORIGIN``: match origin.
* ``--read-only`` / ``--no-read-only``: require read-only true / false.
* ``--size-gb SIZE_GB``: match size in GiB. Integer value.

``export list``:

* ``--status STATUS``: match status. Choices: ``ACTIVE``, ``DELETING``,
  ``ERROR``, ``EXPIRED``, ``PENDING``, ``SNAPSHOTTING``, ``UPLOADING``.

``event list``:

* ``--message MESSAGE``: match message.

Database commands
-----------------

Database commands use API microversion 1.29. Choose engines and versions
from ``openstack coast database engine list``. This catalog shows Engine,
Name, Versions, Default, Port, Backups, Has database and Has username;
choices and capabilities come from the API catalog, defined in
``coast/common/engine_versions.py`` in the Coast repository. Has database
indicates whether an engine accepts ``--database``; Has username indicates
whether it accepts ``--username``. Both capabilities appear in all output
formats.

Create a database on a worker in the app group's stack::

  openstack coast database engine list
  openstack coast database create orders-db --engine ENGINE \
      --appgroup shop --environment production --server worker-1 --wait

Replace ENGINE with a catalog entry. ``--version`` selects a catalog
version; omission uses the engine default. ``--database`` names the database
inside the engine (default: ``app``), and ``--username`` names the login user
to create (default: ``app``). Only engines with databases or named users,
respectively, accept these options; see ``coast database engine list`` for
supported engines. Coast generates the password; read it with
``coast database credentials show``. ``--description`` is a free-text
description shown in list and show output. ``--cpu-limit`` (positive cores)
and ``--memory-limit-mb`` (positive MiB) configure resource limits.

``database list`` accepts ``--stack``, ``--appgroup``, ``--environment``,
``--server``, ``--engine`` and ``--status``, plus ``--limit``, ``--marker``,
``--sort-key`` and ``--sort-dir``. Administrators may use ``--all-projects``
or ``--project PROJECT_ID``; references then require UUIDs.
``--stack``, ``--appgroup``, ``--environment`` and ``--server`` restrict
results to databases in that parent (name or ID). ``--engine`` selects only
databases of that engine; see ``coast database engine list``. ``--status``
selects only databases in that status, for example ACTIVE or ERROR.
Placement references and database arguments accept names or IDs.

Read details, logs and credentials, or manage runtime state::

  openstack coast database show orders-db
  openstack coast database logs orders-db --tail 50
  openstack coast database credentials show orders-db
  openstack coast database stop orders-db --wait
  openstack coast database start orders-db --wait
  openstack coast database restart orders-db --wait
  openstack coast database delete orders-db --wait

Logs also accept ``--since`` with an ISO 8601 UTC timestamp. Start requires
STOPPED; stop and restart require ACTIVE. Create and actions return when
accepted unless ``--wait`` is given. Waiting polls for ACTIVE (STOPPED for
stop), fails on ERROR and times out after 20 minutes. Delete takes one or
more database references; ``--wait`` polls until each returns 404.

Credentials are secret and require the member role. By default passwords
and passwords embedded in URLs are masked in every output format.
``--show-password`` reveals them, including the root password if returned.
Protect the output. To extract just the password for a script::

  openstack coast database credentials show orders-db \
      --show-password -f value -c password

Credential fields keep their API names, including ``username``,
``database``, ``password``, ``internal_url`` and ``public_url``.

Update database limits and public access
----------------------------------------

``database set DATABASE`` accepts ``--name``, ``--description``,
``--cpu-limit`` (positive cores), ``--memory-limit-mb`` (positive MiB),
``--public | --no-public``, ``--public-port N`` (1024-65535) and
``--allow-from CIDR`` (IPv4 only). Omitted fields keep their saved values.
``database unset DATABASE --cpu-limit --memory-limit-mb`` removes either
or both limits by sending null values.

``database create`` also accepts ``--public``, ``--public-port`` and
``--allow-from``. Public access is off by default. Coast selects a free
port when omitted and defaults the allowed source to ``0.0.0.0/0`` when
public access is enabled. Choose a restricted source where possible.
For stack-only access, use the stack subnet as the source; apps on other
workers connect to the returned public private host and port.

``database show`` includes Public, Public URL and Allowed from. Public
URLs contain no password. When the API reports ``restart_required``, the
client prints a restart notice on stderr; use ``database restart DATABASE``.

::

  openstack coast database set orders-db --public --allow-from 198.51.100.0/24
  openstack coast database set orders-db --no-public
  openstack coast database unset orders-db --cpu-limit

Database backups
----------------

Database backups require a Swift-compatible object-storage endpoint on the
cloud. They are not verified end to end in 0.1.0; full verification is
tracked for 0.1.1 (gozunga/coast#124).

Use the catalog's Backups column to check engine support. Engines without
backups report ``Backups are not available for this engine.``.

::

  openstack coast database backup policy show orders-db
  openstack coast database backup policy set orders-db --enable \
    --hour 2 --minute 0 --keep-daily 14 --keep-weekly 8
  openstack coast database backup create orders-db
  openstack coast database backup list orders-db
  openstack coast backup show BACKUP_ID
  openstack coast backup download BACKUP_ID --file orders.dump.gz
  openstack coast backup restore BACKUP_ID --name orders-restored
  openstack coast backup delete BACKUP_ID

``database backup policy set`` accepts mutually exclusive ``--enable`` /
``--disable`` and optional ``--hour``, ``--minute``, ``--keep-daily`` and
``--keep-weekly``. Omitted fields retain their saved values. Coast derives
the engine and container; these commands do not accept either field.
Show, delete, download and restore use the existing ``coast backup`` commands.
``backup restore --name NAME`` defaults to a new copy: a database backup
creates a database with the same engine, version and placement, fresh
credentials and public access off. Overwrite restore requires
``--mode overwrite --confirmation DATABASE_NAME`` and takes a safety backup.

Private repositories and file sources
-------------------------------------

``app create`` accepts one source: ``--repo-url``, ``--image`` (with
``--tag``), ``--dockerfile FILE|-``, or ``--compose-file FILE|-``.
Repository build options such as ``--dockerfile-path`` describe paths in
your repository; ``--dockerfile`` uploads local file content instead.
All new file arguments accept UTF-8 files or ``-`` for stdin.

For an SSH repository, request an API-generated deploy key::

   openstack coast app create web --appgroup website --environment staging \
       --server worker-1 --repo-url git@example.com:org/web.git \
       --deploy-key generate

The server generates an ed25519 key. Creation prints its public key and
asks you to add it to your repository before running
``openstack coast deploy web``. Creating with a generated key does not
start a deployment. To upload an existing unencrypted private key, use
``--deploy-key-file FILE|-`` instead. These two flags are mutually
exclusive. Coast stores the private key in Barbican; it never appears in API
responses, CLI output or the dashboard. The API rejects
passphrase-protected keys with a clear error. SSH repositories require a
deploy key; HTTPS repositories cannot have one or embedded credentials.

``app show APP`` includes Deploy key public key and Deploy key fingerprint
(fields ``deploy_key_public_key`` and ``deploy_key_fingerprint``).
``app deploy-key show APP`` prints only the public key, suitable for piping.
Rotate using ``app set APP --deploy-key generate`` or
``app set APP --deploy-key-file FILE|-``. Add the replacement public key to
your Git host before deploying. Rotation saves without deploying.
These deploy-key operations need API version 1.30; a
server lacking it produces an error naming the required version.

Create from local content without a repository::

   openstack coast app create web --appgroup website --environment staging \
       --server worker-1 --dockerfile Dockerfile
   openstack coast app create bundle --appgroup website --environment staging \
       --server worker-1 --compose-file compose.yaml

Dockerfile creates an app; Compose creates a service with the usual compose
editor and containers views. Uploaded content is stored on the server and
is not echoed by writes. These sources need API version 1.31.

``app set APP --dockerfile FILE|-`` replaces the Dockerfile using
``PUT /v1/services/{id}/dockerfile`` with ``{"dockerfile": {"text": "..."}}``.
``dockerfile show APP`` prints only the stored text. Both need API version
1.31. Compose services use the existing ``compose show --service SERVICE``
and ``compose set --service SERVICE --file FILE|-`` commands (API 1.12).
Replacing either file saves without deploying. Run
``openstack coast deploy SERVICE``
to apply saved Compose changes. On redeploy, an unchanged Dockerfile is
skipped and the existing image is reused. A changed Dockerfile is refused
with: "This app can't use a changed Dockerfile. Create a new app from the
new Dockerfile, or deploy from a Git repository." Compose set reports that
the content was saved.

New source operations request their required microversion for that call,
preserving a newer selected version and leaving the selected version for
other calls unchanged.

Secret storage and teardown
---------------------------

Coast stores deploy private keys, database passwords (including root
passwords where applicable), all environment values, file-mount content,
and uploaded Dockerfile and Compose text in Barbican. Its database stores
references. App or database deletion removes owned secrets, including
references retained by deployment snapshots; stack teardown also removes
stack-owned secrets. Environment rotation and unset retain old values while
deployment snapshots reference them. Private deploy keys are never returned;
database credentials require the explicit credentials command described above.
