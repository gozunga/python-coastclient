Coast client
============

.. include:: ../../README.rst

.. toctree::
   :maxdepth: 1

   commands
   acceptance

Upstream references
-------------------

The hooks follow the `OSC plugin contract
<https://docs.openstack.org/python-openstackclient/latest/contributor/plugins.html>`_
and `Octavia plugin
<https://opendev.org/openstack/python-octaviaclient/src/branch/master/octaviaclient/osc/plugin.py>`_.
The transport uses `keystoneauth discovery and adapters
<https://docs.openstack.org/keystoneauth/latest/api/keystoneauth1.adapter.html>`_.
The tox environments install with OpenStack 2026.1 upper-constraints.
Minimum dependency versions are declared in ``requirements.txt``; the
constraints file determines the versions installed by tox.
