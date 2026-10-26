python-coastclient
==================

0.1.0 (unreleased)
==================

The first release provides the ``openstack coast`` OpenStackClient plugin.
It uses OSC authentication and the Keystone ``app-hosting`` service catalog,
with no endpoint defaults or separate credential store. Python 3.10 or
later is required.

The client negotiates API microversions 1.0 through 1.31. Commands manage
stacks and servers, app groups and environments, Git, image and Dockerfile
apps,
template, image and Compose services, standalone databases, deployments
and logs, env variables, domains and DNS, storage and volumes, backups and
exports, quotas and events,
plans and flavors, and container terminals.

Env commands accept assignments, hidden prompts and dotenv files, and mask
output values. Coast stores env values in Barbican and exposes secret
values as null in API responses; the CLI displays ``********``.
``env unset KEY`` removes one key per command. Build-time variables are also
available during builds; use runtime-only secrets for credentials.

List, show and resource write commands support OSC formatters. Background
operations return accepted state; use show or list to check progress.
Delete and remove commands are silent on success.

See `the command reference <doc/source/commands.rst>`_ for command syntax
and `acceptance checks <doc/source/acceptance.rst>`_ for verification scope.

Private Git apps support generated ed25519 deploy keys and key rotation.
Only the public key is returned; Coast stores the private key in Barbican.
Add a generated key to the Git host before deploying. Dockerfile and Compose
edits save without deploying. Redeploy reuses the image for an unchanged
Dockerfile; changed Dockerfiles require a new app or a Git source.

Database backups require a Swift-compatible object-storage endpoint on the
cloud. They are not verified end to end in 0.1.0; full verification is
tracked for 0.1.1 (gozunga/coast#124).
