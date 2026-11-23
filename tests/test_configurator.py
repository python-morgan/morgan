# Copyright 2026 RISE FX GmbH
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=missing-function-docstring,missing-class-docstring,missing-module-docstring
import importlib
import platform

from morgan import configurator


class TestGenerateEnv:
    def test_unparsable_python_version(self, monkeypatch, capsys):
        # CPython built from an untagged commit reports a version that
        # packaging cannot parse.
        monkeypatch.setattr(platform, "python_version", lambda: "3.13.0a0+")
        # The module-level code of morgan.configurator must not fail on such
        # a Python.
        importlib.reload(configurator)

        configurator.generate_env()

        assert "python_full_version = 3.13.0a0+" in capsys.readouterr().out
