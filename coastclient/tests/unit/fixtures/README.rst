Coast API route inventory
=========================

These rest_method declarations were extracted from gozunga/coast main,
commit d70ee49, API 1.24. The upstream files currently use .rst; the test
accepts both .rst and .inc. Only declarations are stored here.

Set COAST_API_REF to a read-only Coast checkout's api-ref/source directory
to check the current upstream inventory without changing this fixture.
Refresh the declarations when adopting a newer API. Tests never fetch the
network. existing-routes.json maps the original commands, whose request
methods and paths are exercised by the existing command tests. New command
routes come from the installed command classes. The only exclusions are
GET / and GET /v1, used internally for discovery.

The stack ingress rule declarations supplement this snapshot with the
API 1.25 contract from Coast issue 50 (m73/ingress-rules-api).

The server flavor sections are copied from Coast main 0cbbf4f, API 1.27,
including the GET /v1/flavors declaration and its response contract.
