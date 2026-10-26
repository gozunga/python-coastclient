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

"""Expected presentation headings, shared across command tests."""
from osc_lib.command import command

from coastclient.osc.v1 import utils


def labelled(data, cls=None):
    if cls is not None and not issubclass(
            cls, (command.ShowOne, command.Lister)):
        return data
    if isinstance(data, dict):
        return {utils.label(key): value for key, value in data.items()}
    if isinstance(data, list):
        return [labelled(row) for row in data]
    return data
