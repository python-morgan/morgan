import os

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from morgan.utils import (
    Cache,
    filter_relevant_requirements,
    is_requirement_relevant,
    specifier_mentions_prerelease,
    touch_file,
)


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


class TestTouchFile:
    def test_sets_mtime_to_upload_time(self, tmp_path):
        """The mtime is set from PyPI's upload-time, parsed by python-dateutil"""
        path = tmp_path / "package-1.0.0.tar.gz"
        path.write_bytes(b"")

        touch_file(str(path), {"upload-time": "2025-05-28T18:46:29.349478Z"})

        # 2025-05-28 18:46:29.349478 UTC; 2 s tolerance because mtime
        # resolution differs between filesystems
        assert path.stat().st_mtime == pytest.approx(1748457989.349478, abs=2)

    def test_without_upload_time_leaves_mtime(self, tmp_path):
        """Files without upload-time keep their mtime"""
        path = tmp_path / "package-1.0.0.tar.gz"
        path.write_bytes(b"")
        os.utime(path, (1_000_000_000, 1_000_000_000))

        touch_file(str(path), {})

        assert path.stat().st_mtime_ns == 1_000_000_000 * 10**9


class TestCache:
    @pytest.mark.parametrize(
        ("first", "selected", "second"),
        [
            ("foo>=1.0", "1.6.0", "foo>=1.7.0rc1"),
            ("foo>=1.7.0rc1", "1.7.0rc1", "foo"),
        ],
        ids=["stable_then_prerelease", "prerelease_then_bare"],
    )
    def test_prerelease_specifier_does_not_share_cache_entry(
        self,
        first,
        selected,
        second,
    ):
        cache = Cache()
        cache.add(Requirement(first), Version(selected))

        assert not cache.check(Requirement(second))

    def test_plain_lower_bounds_share_cache_entry(self):
        cache = Cache()
        cache.add(Requirement("foo>=1.0"), Version("2.5"))

        assert cache.check(Requirement("foo>=2.0"))
        assert cache.check(Requirement("foo"))

    def test_lower_bound_above_selected_version_misses(self):
        cache = Cache()
        cache.add(Requirement("foo>=1.0"), Version("2.5"))

        assert not cache.check(Requirement("foo>3.0"))

    def test_prerelease_selected_by_fallback_does_not_share_cache_entry(self):
        # foo>2.5 selected 3.0b1 through the fallback, but the bare
        # requirement selects the final release 2.5
        cache = Cache()
        cache.add(Requirement("foo>2.5"), Version("3.0b1"))

        assert not cache.check(Requirement("foo"))

    def test_identical_requirement_hits(self):
        cache = Cache()
        cache.add(Requirement("foo>2.5"), Version("3.0b1"))

        assert cache.check(Requirement("foo>2.5"))

    def test_recorded_prerelease_hits_when_prereleases_allowed(self):
        cache = Cache(allow_prereleases=True)
        cache.add(Requirement("foo>2.5"), Version("3.0b1"))

        assert cache.check(Requirement("foo>=1.0"))

    def test_requirement_added_without_version_only_matches_itself(self):
        cache = Cache()
        cache.add(Requirement("foo"))

        assert cache.check(Requirement("foo"))
        assert not cache.check(Requirement("foo>=1.0"))


class TestSpecifierMentionsPrerelease:
    @pytest.mark.parametrize(
        ("specifier", "expected"),
        [
            (">=1.7.0rc1", True),
            ("==2.0.0rc1", True),
            ("~=2.0rc1", True),
            ("<=2.0rc1", True),
            (">2.0rc1", True),
            ("<3.0.0dev", True),
            (">=1.31.5,<3.0.0dev", True),
            ("", False),
            (">=1.0", False),
            ("==1.*", False),
            ("!=2.0rc1", False),
            ("===1.0-custom", False),
            ("===2.0rc1", False),
        ],
    )
    def test_specifier_mentions_prerelease(self, specifier, expected):
        # the result must not depend on the installed packaging version
        assert specifier_mentions_prerelease(SpecifierSet(specifier)) is expected
