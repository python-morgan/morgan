# Copyright 2022 Ido Perlmuter
# SPDX-License-Identifier: Apache-2.0

import pytest

from morgan import server


@pytest.mark.parametrize(
    ("accept_option", "exp_dict"),
    [
        (server.GENL_HTML_TYPE, {"mime": server.GENL_HTML_TYPE, "priority": 0}),
        (
            f"{server.GENL_HTML_TYPE};q=1",
            {"mime": server.GENL_HTML_TYPE, "priority": 1},
        ),
        (
            f"{server.GENL_HTML_TYPE};q=0.9",
            {"mime": server.GENL_HTML_TYPE, "priority": 0.9},
        ),
        (
            f"{server.GENL_HTML_TYPE} ; q=0.9",
            {"mime": server.GENL_HTML_TYPE, "priority": 0.9},
        ),
        (
            f"{server.GENL_HTML_TYPE};q=0.9&charset=UTF-8",
            {"mime": server.GENL_HTML_TYPE, "priority": 0.9},
        ),
        (
            f"{server.GENL_HTML_TYPE};charset=UTF-8&q=0.9",
            {"mime": server.GENL_HTML_TYPE, "priority": 0.9},
        ),
    ],
)
def test_parse_accept_option(accept_option, exp_dict):
    got_dict = server.parse_accept_option(accept_option)
    assert got_dict == exp_dict


@pytest.mark.parametrize(
    ("accept_header", "exp_mime"),
    [
        (None, server.PYPI_HTML_TYPE_V1),
        (
            "text/xml",
            None,
        ),
        (
            server.PYPI_JSON_TYPE_V1,
            server.PYPI_JSON_TYPE_V1,
        ),
        (
            f"{server.PYPI_JSON_TYPE_V1};q=0.5, {server.GENL_HTML_TYPE}; q=1",
            server.GENL_HTML_TYPE,
        ),
        (
            "*/*",
            server.PYPI_HTML_TYPE_V1,
        ),
        (
            "text/xml;q=1,*/*; q=0.5",
            server.PYPI_HTML_TYPE_V1,
        ),
    ],
)
def test_parse_accept_header(accept_header, exp_mime):
    got_mime = server.parse_accept_header(accept_header)
    assert got_mime == exp_mime
