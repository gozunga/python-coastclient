.. rest_method:: GET /v1/stacks/{stack_id}/servers
.. rest_method:: POST /v1/stacks/{stack_id}/servers
.. rest_method:: GET /v1/stacks/{stack_id}/servers/{server_id}
.. rest_method:: DELETE /v1/stacks/{stack_id}/servers/{server_id}
.. rest_method:: POST /v1/stacks/{stack_id}/servers/{server_id}/action

Customer server flavor (1.27)
=============================

GET list and show include ``flavor`` from microversion 1.27 for project
readers and admins. For example::

    {"id": "<flavor-id>", "name": "Small", "vcpus": 1,
     "ram": 1024, "disk": 10}

RAM is in MiB and disk in GiB. Bare metal and VMs without a saved flavor
return null. A removed Nova flavor returns only the saved reference in
``id`` and ``name: null``. Nova failures return sanitized 503.
``fields=flavor`` is supported; filtering and sorting by flavor are not.
Versions through 1.26 and write responses retain their existing shape.
Admin-only ``flavor_id`` and other detail fields retain their policies.

List allowed flavors (1.27)
===========================

.. rest_method:: GET /v1/flavors

Requires ``OpenStack-API-Version: app-hosting 1.27`` and
``coast:flavor:get_all`` (project reader or admin). Returns 200::

    {"flavors": [{"id": "<flavor-id>", "name": "Small", "vcpus": 1,
                  "ram": 1024, "disk": 10, "plan": "small",
                  "roles": ["worker", "build"]}]}

``ram`` is MiB; ``disk`` is GiB. Only operator-mapped VM flavors are
included, ordered by plan name. ``roles`` contains the usable subset of
``control``, ``worker``, ``build``. Missing Nova flavors and roles with
ambiguous plan mappings are omitted; entries without usable roles are
omitted. An empty catalog returns ``{"flavors": []}``.

For add-server, send the entry's ``plan`` as ``server.plan``. For resize,
send it as ``resize.flavor`` or ``resize_workers.flavor``. Choose an entry
with the intended server role. Existing flavor name/ID mapping remains
supported. Resize also requires a strictly larger size and an idle ACTIVE
server/stack. Catalog inclusion does not guarantee Nova capacity.

No query parameters (400). Other methods return 405. Missing identity
returns 401; wrong role or scope returns 403; versions below 1.27 return
404; unsupported versions return 406. Invalid operator catalogs return
sanitized 500 and Nova failures return sanitized 503. Errors use the
existing ``faultstring``, ``faultcode``, ``debuginfo`` envelope.
