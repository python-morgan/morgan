import base64
import os

import pytest
from packaging.requirements import Requirement

from morgan import Mirrorer, create_arg_parser
from morgan.utils import filter_relevant_requirements, is_requirement_relevant


class TestEnvironmentEvaluation:
    @pytest.fixture
    def python_environments(self):
        """Sample environments with different Python versions"""
        return [
            {
                "python_version": "3.7",
                "sys_platform": "linux",
                "platform_machine": "x86_64",
            },
            {
                "python_version": "3.8",
                "sys_platform": "linux",
                "platform_machine": "x86_64",
            },
            {
                "python_version": "3.9",
                "sys_platform": "linux",
                "platform_machine": "x86_64",
            },
        ]

    @pytest.fixture
    def platform_environments(self):
        """Sample environments with different platforms"""
        return [
            {
                "python_version": "3.8",
                "sys_platform": "linux",
                "platform_machine": "x86_64",
            },
            {
                "python_version": "3.8",
                "sys_platform": "win32",
                "platform_machine": "x86_64",
            },
            {
                "python_version": "3.8",
                "sys_platform": "darwin",
                "platform_machine": "x86_64",
            },
        ]

    def test_simple_requirement_always_relevant(self, python_environments):
        """Test that a requirement without markers is always relevant"""
        req = Requirement("simple-package")

        result = is_requirement_relevant(req, python_environments)

        assert result

    def test_simple_requirement_with_empty_environments(self):
        """Test that a requirement without markers is always relevant even with empty environments"""
        req = Requirement("simple-package")

        result = is_requirement_relevant(req, [])

        assert result

    @pytest.mark.parametrize(
        ("requirement_str", "expected"),
        [
            ('package; python_version < "3.8"', True),
            ('package; python_version > "3.9"', False),
            ('package; python_version < "3.6"', False),
        ],
        ids=["py37_only", "py37_and_above", "py35_and_below"],
    )
    def test_requirement_with_python_version_marker(
        self,
        requirement_str,
        expected,
        python_environments,
    ):
        """Test requirements with Python version markers"""
        req = Requirement(requirement_str)

        result = is_requirement_relevant(req, python_environments)

        assert result == expected

    @pytest.mark.parametrize(
        ("requirement_str", "expected"),
        [
            ('package; sys_platform == "linux"', True),
            ('package; sys_platform == "linux" or sys_platform == "win32"', True),
            ('package; sys_platform == "freebsd"', False),
        ],
        ids=["linux_only", "linux_or_windows", "freebsd_only"],
    )
    def test_requirement_with_platform_marker(
        self,
        requirement_str,
        expected,
        platform_environments,
    ):
        """Test requirements with platform markers"""
        req = Requirement(requirement_str)

        result = is_requirement_relevant(req, platform_environments)

        assert result == expected

    @pytest.mark.parametrize(
        ("extras", "expected"),
        [
            ({"test"}, True),  # With matching extra
            ({"other"}, False),  # With non-matching extra
            (None, False),  # No extras provided
        ],
        ids=["with_test_extra", "with_other_extra", "no_extras"],
    )
    def test_requirement_with_extra_marker(self, extras, expected, python_environments):
        """Test requirements with extra markers"""
        req = Requirement('package; extra == "test"')

        result = is_requirement_relevant(req, python_environments, extras=extras)

        assert result == expected

    @pytest.mark.parametrize(
        ("requirement_str", "extras", "expected"),
        [
            ('package; python_version >= "3.8" and extra == "test"', {"test"}, True),
            ('package; python_version >= "3.8" and extra == "test"', {"other"}, False),
            ('package; python_version >= "3.9" and extra == "test"', {"test"}, True),
            ('package; python_version >= "3.9" or extra == "test"', {"test"}, True),
            ('package; python_version >= "3.9" or extra == "test"', None, True),
        ],
        ids=[
            "py38_plus_with_test_extra",
            "py38_plus_with_wrong_extra",
            "py39_plus_with_test_extra",
            "py39_or_test_extra_with_extra",
            "py39_or_test_extra_no_extras",
        ],
    )
    def test_complex_requirement_with_combined_markers(
        self,
        requirement_str,
        extras,
        expected,
        python_environments,
    ):
        """Test requirements with combined markers"""
        req = Requirement(requirement_str)

        result = is_requirement_relevant(req, python_environments, extras=extras)

        assert result == expected

    @pytest.mark.parametrize(
        ("extras", "expected_count", "expected_names"),
        [
            (None, 3, {"always-relevant", "py37-only", "py38-plus"}),
            ({"test"}, 4, {"always-relevant", "py37-only", "py38-plus", "test-extra"}),
        ],
        ids=["no_extras", "with_test_extra"],
    )
    def test_filter_relevant_requirements(
        self,
        extras,
        expected_count,
        expected_names,
        python_environments,
    ):
        """Test filtering a collection of requirements"""
        requirements = [
            Requirement("always-relevant"),
            Requirement('py37-only; python_version < "3.8"'),
            Requirement('py38-plus; python_version >= "3.8"'),
            Requirement('test-extra; extra == "test"'),
            Requirement('not-relevant; python_version < "3.6"'),
        ]

        filtered = filter_relevant_requirements(
            requirements,
            python_environments,
            extras=extras,
        )

        assert len(filtered) == expected_count
        assert {req.name for req in filtered} == expected_names

    def test_filter_with_empty_requirements(self):
        """Test filtering with empty requirements list"""
        requirements: list[Requirement] = []
        environments = [{"python_version": "3.8"}]

        filtered = filter_relevant_requirements(requirements, environments)

        assert len(filtered) == 0

    def test_filter_with_empty_environments(self):
        """Test filtering with empty environments list"""
        requirements = [
            Requirement("package1"),
            Requirement('package2; python_version >= "3.8"'),
        ]
        environments: list[dict] = []

        filtered = filter_relevant_requirements(requirements, environments)

        assert len(filtered) == 2


def create_auth_header(user: str, passwd: str):
    # ruff: noqa: UP012
    s = base64.b64encode(f"{user}:{passwd}".encode("utf-8")).decode("ascii")
    return ("Authorization", f"Basic {s}")


class TestNetrc:
    """Tests for Index's .netrc evaluation"""

    @pytest.fixture
    def temp_index_path(self, tmp_path):
        # Create minimal config file
        config_path = os.path.join(tmp_path, "morgan.ini")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                """
                [env.test_env]
                python_version = 3.10
                sys_platform = linux
                platform_machine = x86_64
                """,
            )
        return tmp_path

    @pytest.fixture
    def temp_netrc(self, tmp_path):
        # Create minimal config file
        fpath = os.path.join(tmp_path, ".netrc")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(
                """\
default login xxx password yyy

machine example.com
login daniel
password qwerty
""",
            )
        return fpath

    def test_default(self, temp_index_path, temp_netrc):
        args = create_arg_parser().parse_args(
            [
                "mirror",
                "--index-path",
                str(temp_index_path),
                "--config",
                os.path.join(temp_index_path, "morgan.ini"),
                "--netrc-file",
                str(temp_netrc),
            ],
        )
        m = Mirrorer(args)
        assert m.index.auth_header == create_auth_header("xxx", "yyy")

    def test_example(self, temp_index_path, temp_netrc):
        args = create_arg_parser().parse_args(
            [
                "mirror",
                "--index-path",
                str(temp_index_path),
                "--index-url",
                "https://example.com/simple",
                "--config",
                os.path.join(temp_index_path, "morgan.ini"),
                "--netrc-file",
                str(temp_netrc),
            ],
        )
        m = Mirrorer(args)
        assert m.index.auth_header == create_auth_header("daniel", "qwerty")
