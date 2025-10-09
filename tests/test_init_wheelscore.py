"""Tests for the _calculate_scores_for_wheel() method in morgan."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# ruff: noqa: SLF001

import argparse
import os
from typing import NamedTuple

import pytest

from morgan import Mirrorer


class TestCalculateScoresForWheel:
    """Tests for _calculate_scores_for_wheel() method."""

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
    def mirrorer(self, temp_index_path):
        args = argparse.Namespace(
            index_path=temp_index_path,
            index_url="https://example.com/simple/",
            config=os.path.join(temp_index_path, "morgan.ini"),
            mirror_all_versions=False,
            package_type_regex=r"(whl|zip|tar\.gz)",
            mirror_all_wheels=True,
        )
        return Mirrorer(args)

    class Tag(NamedTuple):
        """Mock tag object for testing."""

        interpreter: str
        abi: str
        platform: str

    @staticmethod
    def make_tag(interpreter, abi, platform):
        """Create mock tag objects."""
        return TestCalculateScoresForWheel.Tag(interpreter, abi, platform)

    def test_non_wheel_gets_maximum_score(self, mirrorer):
        """Non-wheel files (sdists) should always get maximum score."""
        file = {"is_wheel": False}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (10000000000, 10000000000)

    def test_wheel_with_no_tags(self, mirrorer):
        """Wheels without tags should get zero scores."""
        file = {"is_wheel": True, "tags": []}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (0, 0)

    def test_wheel_with_missing_tags_key(self, mirrorer):
        """Wheels without tags key should get zero scores."""
        file = {"is_wheel": True}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (0, 0)

    @pytest.mark.parametrize(
        ("interpreter", "expected_py_score"),
        [
            ("cp38", 308),
            ("cp39", 309),
            ("cp310", 310),
            ("cp311", 311),
            ("cp312", 312),
        ],
        ids=["cp38", "cp39", "cp310", "cp311", "cp312"],
    )
    def test_cpython_version_scoring(self, mirrorer, interpreter, expected_py_score):
        """CPython versions should be scored correctly (major*100 + minor)."""
        tag = self.make_tag(interpreter, "none", "any")
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (expected_py_score, 0)

    @pytest.mark.parametrize(
        ("interpreter", "expected_py_score"),
        [
            ("py3", 300),
            ("py38", 308),
            ("py39", 309),
            ("py310", 310),
        ],
        ids=["py3_major_only", "py38", "py39", "py310"],
    )
    def test_generic_python_version_scoring(
        self,
        mirrorer,
        interpreter,
        expected_py_score,
    ):
        """Generic Python tags should be scored correctly."""
        tag = self.make_tag(interpreter, "none", "any")
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (expected_py_score, 0)

    @pytest.mark.parametrize(
        ("platform", "expected_platform_score"),
        [
            ("manylinux_2_17_x86_64", 217),
            ("manylinux_2_28_x86_64", 228),
            ("manylinux_2_35_aarch64", 235),
            ("manylinux_2_5_i686", 205),
        ],
        ids=["manylinux_2_17", "manylinux_2_28", "manylinux_2_35", "manylinux_2_5"],
    )
    def test_manylinux_modern_format_scoring(
        self,
        mirrorer,
        platform,
        expected_platform_score,
    ):
        """Modern manylinux format should extract version correctly."""
        tag = self.make_tag("cp311", "cp311", platform)
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (311, expected_platform_score)

    @pytest.mark.parametrize(
        ("platform", "expected_platform_score"),
        [
            ("manylinux2014_x86_64", 90),
            ("manylinux2010_x86_64", 80),
            ("manylinux1_x86_64", 70),
            ("manylinux1_i686", 70),
        ],
        ids=["manylinux2014", "manylinux2010", "manylinux1_x86_64", "manylinux1_i686"],
    )
    def test_manylinux_deprecated_format(
        self,
        mirrorer,
        platform,
        expected_platform_score,
    ):
        """Deprecated manylinux formats should get fixed scores."""
        tag = self.make_tag("cp311", "cp311", platform)
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (311, expected_platform_score)

    def test_platform_any(self, mirrorer):
        """Universal wheels (platform 'any') should get zero platform score."""
        tag = self.make_tag("py3", "none", "any")
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (300, 0)

    @pytest.mark.parametrize(
        ("platform", "expected_platform_score"),
        [
            ("win_amd64", 0),  # No underscore-digit pattern
            ("win32", 0),  # No underscore-digit pattern
            ("macosx_10_13_intel", 1013),  # Matches regex: 10*100 + 13
            ("macosx_11_0_arm64", 1100),  # Matches regex: 11*100 + 0
        ],
        ids=["windows_64bit", "windows_32bit", "macos_intel", "macos_arm64"],
    )
    def test_non_manylinux_platforms(
        self,
        mirrorer,
        platform,
        expected_platform_score,
    ):
        r"""Platforms with [a-z]+_(\d+)_(\d+) pattern get scored."""
        tag = self.make_tag("cp311", "cp311", platform)
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (311, expected_platform_score)

    @pytest.mark.parametrize(
        ("platform", "expected_platform_score"),
        [
            ("musllinux_1_1_x86_64", 101),
            ("musllinux_1_2_aarch64", 102),
        ],
        ids=["musllinux_1_1", "musllinux_1_2"],
    )
    def test_musllinux_platform_scoring(
        self,
        mirrorer,
        platform,
        expected_platform_score,
    ):
        """Musllinux platforms should be scored similar to manylinux."""
        tag = self.make_tag("cp311", "cp311", platform)
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (311, expected_platform_score)

    def test_complete_wheel_tag_scoring(self, mirrorer):
        """Complete tag should calculate both scores correctly."""
        tag = self.make_tag("cp311", "cp311", "manylinux_2_28_x86_64")
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (311, 228)

    def test_multiple_tags_uses_first_valid_match(self, mirrorer):
        """When multiple tags exist, should use first valid tag with both scores."""
        tags = [
            self.make_tag("cp311", "cp311", "manylinux_2_28_x86_64"),
            self.make_tag("cp310", "cp310", "manylinux_2_17_x86_64"),
        ]
        file = {"is_wheel": True, "tags": tags}
        score = mirrorer._calculate_scores_for_wheel(file)
        # Should get scores from first tag
        assert score == (311, 228)

    def test_multiple_tags_select_newest_python_version(self, mirrorer):
        """Should select tag with python version first over platform_score."""
        tags = [
            self.make_tag("pp38", "none", "any"),  # Unsupported, skip
            self.make_tag("cp311", "cp311", "any"),  # Valid py, no platform -> (311, 0)
            self.make_tag("cp310", "cp310", "manylinux_2_28_x86_64"),  # -> (310, 228)
        ]
        file = {"is_wheel": True, "tags": tags}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (311, 0)

    def test_tag_with_python_but_no_platform_score(self, mirrorer):
        """Tag with valid Python but no manylinux should have partial score."""
        tag = self.make_tag("cp311", "cp311", "win_amd64")
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (311, 0)

    def test_interpreter_without_version(self, mirrorer):
        """Interpreter without version should be skipped."""
        tag = self.make_tag("something_strange", "none", "any")
        file = {"is_wheel": True, "tags": [tag]}
        score = mirrorer._calculate_scores_for_wheel(file)
        assert score == (0, 0)

    def test_scoring_enables_correct_sorting(self, mirrorer):
        """Higher scores should sort higher for wheel selection."""
        files = [
            {
                "is_wheel": True,
                "tags": [self.make_tag("cp38", "cp38", "manylinux_2_17_x86_64")],
            },
            {
                "is_wheel": True,
                "tags": [self.make_tag("cp311", "cp311", "manylinux_2_28_x86_64")],
            },
            {
                "is_wheel": True,
                "tags": [self.make_tag("cp39", "cp39", "manylinux_2_17_x86_64")],
            },
        ]

        scores = [mirrorer._calculate_scores_for_wheel(f) for f in files]

        # Verify scores are in expected order
        assert scores[0] == (308, 217)  # cp38, manylinux_2_17
        assert scores[1] == (311, 228)  # cp311, manylinux_2_28 (highest)
        assert scores[2] == (309, 217)  # cp39, manylinux_2_17

        # Verify sorting works as expected
        sorted_files = sorted(zip(files, scores), key=lambda x: x[1], reverse=True)
        assert sorted_files[0][1] == (311, 228)  # Best score first
