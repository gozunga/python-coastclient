CLI acceptance
==============

Offline unit suites exercise OSC command loading, HTTP routing, output,
pagination, masking and error handling using mocked responses. The route
inventory test compares installed commands with a checked-in API reference
snapshot; it does not prove coverage of every current API action or field.
Use a current read-only Coast checkout for an updated route comparison.
Offline tests do not prove engine, DNS, TLS, or cloud behavior.

Live harness
------------

The ``coastclient/tests/functional/test_acceptance.py`` harness expects
lowercase resource output keys and a quota ``usage`` object. The CLI emits
sentence case headings and quota rows with Resource, Limit, In use and
Reserved columns. The harness also omits the required ``--server`` on app
creation. Several write commands are called without ``-f json``, although
the harness always parses non-empty stdout as JSON. Worker creation and
removal use member credentials, while the server policy requires manager
or admin. Cleanup tracks the explicitly created environment but omits the
app group's automatically created ``production`` environment, which blocks
app group deletion. These code gaps must be corrected before using the
harness for acceptance; its described flow is intended coverage, not a
passing acceptance result. The configuration below describes its inputs.


Live runs belong on the independent acceptance runner, outside a coding
lane. Use an isolated DevStack Coast deployment with a supported, pinned engine
version from Coast
``coast/common/engine_versions.py`` (``SUPPORTED_ENGINE_VERSIONS``).
Use a dedicated acceptance project with no unrelated resources. Create
cloud profiles for reader, member, manager, and admin credentials in that
project, plus a foreign project's member. Keep credentials in protected
OSC cloud files, not in test arguments or the acceptance config. Use
operator-selected plans, images, source revisions, and a test domain.
Do not use a production project: the test changes quotas and creates and
deletes billable resources.

Create a private JSON config file. Replace every placeholder below.
The sample has no endpoint, plan, image, or port defaults.

.. code-block:: json

   {
     "clouds": {
       "reader": "test-reader",
       "member": "test-member",
       "manager": "test-manager",
       "admin": "test-admin",
       "foreign": "other-project-member"
     },
     "project_id": "PROJECT_UUID",
     "foreign_project_id": "OTHER_PROJECT_UUID",
     "plan": "CONTROL_PLAN",
     "worker_plan": "WORKER_PLAN",
     "repo_url": "https://example.com/public-sample.git",
     "first_ref": "FIRST_REVISION",
     "second_ref": "SECOND_REVISION",
     "domain": "sample.example.com",
     "url": "https://sample.example.com/",
     "first_marker": "FIRST_REVISION_RESPONSE_TEXT",
     "second_marker": "SECOND_REVISION_RESPONSE_TEXT",
     "quota_restore": {
       "environments": null,
       "servers": null,
       "baremetal_servers": null,
       "build_servers": null,
       "apps": null,
       "domains": null,
       "volume_gb": null
     },
     "timeout": 900,
     "poll_interval": 5
   }

``quota_restore`` must contain the original stored override for each key,
not the effective limits from quota show. Use null only when that key had
no override. Have the operator record these values before the test;
``openstack coast quota list`` provides the administrative override inventory.
The test registers quota restoration
before its first update and runs it even after a failed assertion. The
other quotas must permit one stack, two workers, a service, a domain, and
their storage. If 1.1 selects an initial build server, allow its resources
as well. Optional ``image``, ``build_server_plan``, and
``build_server_image`` config keys select existing 1.1 inputs. The harness
adds ``--build-server`` only when ``build_server_plan`` is present, so a
build image by itself fails client validation.

The domain must support Coast's existing auto-DNS path. The public sample
must work with the existing git source defaults and have two revisions
with distinct HTTPS response markers. Use a publicly trusted certificate
or install the operator CA in the test runner's trust store. The test
does not disable TLS verification. Start from a clean environment without
conflicting OS authentication variables; the named OSC profiles supply
authentication and catalog selection.

After the code gaps above are resolved, configure the harness inputs.
``COAST_ACCEPTANCE_CONFIG`` names the private JSON config;
``OS_CLIENT_CONFIG_FILE`` and ``OS_CLIENT_SECURE_FILE`` name the protected
OSC cloud files. ``COAST_ACCEPTANCE_REQUIRED=1`` makes missing config fail.
The intended invocation is:

.. code-block:: console

   export COAST_ACCEPTANCE_CONFIG=/path/to/private/acceptance.json
   export OS_CLIENT_CONFIG_FILE=/path/to/private/clouds.yaml
   export OS_CLIENT_SECURE_FILE=/path/to/private/secure.yaml
   COAST_ACCEPTANCE_REQUIRED=1 tox -e functional

With no config, optional local runs skip with a reason. If every test
skips, stestr returns a nonzero exit status; this is not acceptance success.
With
``COAST_ACCEPTANCE_REQUIRED=1``, missing config fails. The config-gated
GitLab functional job uses this required setting. The lead must supply
the private config and cloud files on a DevStack-capable runner before
running that job. An absent ``app-hosting``
catalog service fails before
resource creation; it is not a skip.

The intended flow selects 1.0 and 1.1 globally. Feature-specific calls
can raise the version for an individual request: app creation uses at least
1.11, so these are not strictly 1.0-only and 1.1-only API runs.
The flow is written to create a stack,
app group, environment, extra worker, and Git app using OSC only.
It checks reader writes, foreign-project reads, quota access for all four
roles, non-admin quota writes, and over-quota service creation. It deploys
two revisions, reads deployment and service logs, and checks rollback has
a new deployment ID. It sets, lists, and unsets an env value without
printing it. It adds and verifies the domain, checks both HTTPS revisions
and rollback, and exercises manager stop, start, and restart commands.
Only the test harness polls, with operator-supplied finite timeouts.
Resource write commands return without polling.

Cleanup attempts to remove domains and services before their parents and the
extra worker, then the tracked environment, app group and stack before
the next microversion run. It does not track the default production
environment. It waits for owned IDs to disappear
from existing list commands. All cleanup steps are attempted after a
failure. If the process is killed or a cleanup request fails, the operator
must inspect resources with the harness-generated name and restore the recorded
quota overrides. Do not repeat an uncertain write automatically.

Record evidence
---------------

Keep sanitized unit/tox and functional tails, the Coast and client commit
IDs, the pinned engine version, both microversion results, role/isolation
results, HTTPS revision and rollback results, and final cleanup status.
Do not include tokens, cloud files, env input, or remote engine errors.
A green offline run alone does not demonstrate live acceptance.
