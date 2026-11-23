# Copyright 2026 RISE FX GmbH
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=missing-function-docstring,missing-module-docstring
import pytest

from morgan import create_arg_parser


@pytest.fixture
def make_mirror_args():
    """Return a factory that builds parsed mirror-subcommand arguments."""

    def _make(index_path, config, *extra_args):
        return create_arg_parser().parse_args(
            [
                "mirror",
                "--index-path",
                str(index_path),
                "--config",
                str(config),
                *extra_args,
            ],
        )

    return _make
