# Copyright 2025 RISE FX GmbH
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=missing-function-docstring,missing-class-docstring,missing-module-docstring
from __future__ import annotations

import argparse
import contextlib
import email.message
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile

import packaging.requirements
import packaging.tags
import packaging.utils
import packaging.version
import pytest

import morgan
from morgan import (
    PYPI_ADDRESS,
    Mirrorer,
    is_safe_filename,
    parse_interpreter,
    parse_requirement,
    server,
)


class TestParseInterpreter:
    @pytest.mark.parametrize(
        ("interpreter_string", "expected_name", "expected_version"),
        [
            ("cp38", "cp", "3.8"),
            ("cp3", "cp", "3"),
            ("cp310", "cp", "3.10"),
            ("cp3_10", "cp", "3.10"),
            ("py38", "py", "3.8"),
            ("something_strange", "something_strange", None),
        ],
        ids=[
            "typical_cpython",
            "cpython_no_minor_version",
            "cpython_two_digit_minor",
            "cpython_with_underscore",
            "generic_python",
            "unrecognized_format",
        ],
    )
    def test_parse_interpreter_components(
        self,
        interpreter_string,
        expected_name,
        expected_version,
    ):
        name, version = parse_interpreter(interpreter_string)
        assert name == expected_name
        assert version == expected_version


class TestParseRequirement:
    def test_parse_basic_requirement(self):
        req = parse_requirement("requests")
        assert req.name == "requests"
        assert str(req.specifier) == ""
        assert not req.extras

    def test_parse_versioned_requirement(self):
        req = parse_requirement("requests>=2.0.0")
        assert req.name == "requests"
        assert str(req.specifier) == ">=2.0.0"
        assert not req.extras

    def test_parse_requirement_with_extras(self):
        req = parse_requirement("requests[security,socks]>=2.0.0")
        assert req.name == "requests"
        assert str(req.specifier) == ">=2.0.0"
        assert set(req.extras) == {"security", "socks"}

    def test_normalize_package_name(self):
        req = parse_requirement("Requests-HTTP")
        assert req.name == "requests-http"


class TestMirrorer:
    @pytest.fixture
    def temp_index_path(self, tmpdir):
        # Create minimal config file
        config_path = os.path.join(tmpdir, "morgan.ini")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                """
                [env.test_env]
                python_version = 3.10
                sys_platform = linux
                platform_machine = x86_64
                platform_tag = manylinux

                [requirements]
                requests = >=2.0.0
                """,
            )
        return tmpdir

    def test_mirrorer_initialization(self, temp_index_path, make_mirror_args):
        args = make_mirror_args(
            temp_index_path,
            os.path.join(temp_index_path, "morgan.ini"),
        )

        mirrorer = Mirrorer(args)

        assert mirrorer.index_path == str(temp_index_path)
        assert mirrorer.index_url == PYPI_ADDRESS
        assert "test_env" in mirrorer.envs
        assert mirrorer.envs["test_env"]["python_version"] == "3.10"
        assert mirrorer.envs["test_env"]["sys_platform"] == "linux"
        assert mirrorer.envs["test_env"]["platform_machine"] == "x86_64"
        assert not mirrorer.mirror_all_versions
        assert not mirrorer.prerelease

    def test_server_file_copying(self, temp_index_path, make_mirror_args):
        args = make_mirror_args(
            temp_index_path,
            os.path.join(temp_index_path, "morgan.ini"),
        )
        mirrorer = Mirrorer(args)

        mirrorer.copy_server()

        expected_serverpath = os.path.join(temp_index_path, "server.py")
        assert os.path.exists(expected_serverpath), (
            "server.py should be copied to index_path"
        )

        with open(server.__file__, "rb") as original_server, open(
            expected_serverpath,
            "rb",
        ) as copied_server:
            assert original_server.read() == copied_server.read(), (
                "Copied file should match source"
            )

    def test_file_hashing(self, temp_index_path, make_mirror_args):
        args = make_mirror_args(
            temp_index_path,
            os.path.join(temp_index_path, "morgan.ini"),
        )
        mirrorer = Mirrorer(args)

        test_data = b"test content for hashing"
        test_file = os.path.join(temp_index_path, "test_artifact.whl")
        with open(test_file, "wb") as file:
            file.write(test_data)

        expected_hash = hashlib.sha256(test_data).hexdigest()

        # pylint: disable=W0212
        digest = mirrorer._hash_file(test_file, "sha256")  # noqa: SLF001

        assert digest == expected_hash, "Returned hash should match sha256 digest"
        hash_file = test_file + ".hash"
        assert os.path.exists(hash_file), "Hash file should be created"
        with open(hash_file, encoding="utf-8") as file:
            assert file.read() == f"sha256={expected_hash}", (
                "Hash file content should be correctly formatted"
            )


class TestFilterFiles:
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
                platform_tag = manylinux2014_x86_64
                """,
            )
        return tmp_path

    @pytest.fixture
    def make_mirrorer(self, temp_index_path, make_mirror_args):
        # Return a function that creates mirrorer instances
        def _make_mirrorer(
            mirror_all_versions,
            mirror_all_wheels=False,
            prerelease=False,
        ):
            extra_args = ["--index-url", "https://example.com/simple"]
            if mirror_all_versions:
                extra_args.append("--mirror-all-versions")
            if mirror_all_wheels:
                extra_args.append("--mirror-all-wheels")
            if prerelease:
                extra_args.append("--prerelease")
            args = make_mirror_args(
                temp_index_path,
                os.path.join(temp_index_path, "morgan.ini"),
                *extra_args,
            )
            return Mirrorer(args)

        return _make_mirrorer

    @staticmethod
    def make_file(filename, overrides=None):
        fileinfo = {
            "filename": filename,
            "hashes": {
                "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
            "url": f"https://example.com/{filename}",
        }
        if overrides:
            fileinfo.update(overrides)
        return fileinfo

    @pytest.fixture
    def sample_files(self):
        # the expected versions of the tests below rely on the prerelease
        # 1.7.0rc1 being excluded by default
        return [
            self.make_file("sample_package-1.7.0rc1.tar.gz"),
            self.make_file("sample_package-1.6.0.tar.gz"),
            self.make_file("sample_package-1.5.2.tar.gz"),
            self.make_file("sample_package-1.5.1.tar.gz"),
            self.make_file("sample_package-1.4.9.tar.gz"),
        ]

    @pytest.fixture
    def sample_wheels(self):
        return [
            self.make_file(
                "sample_package-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
                {"is_wheel": True},
            ),
            self.make_file(
                "sample_package-2.0.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                {"is_wheel": True},
            ),
            self.make_file(
                "sample_package-1.6.0-cp311-cp311-manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
                {"is_wheel": True},
            ),
            self.make_file(
                "sample_package-1.6.0-cp310-cp310-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                {"is_wheel": True},
            ),
            self.make_file(
                "sample_package-1.5.2-cp310-cp310-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                {"is_wheel": True},
            ),
            self.make_file(
                "sample_package-1.5.2-cp39-cp39-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                {"is_wheel": True},
            ),
            self.make_file(
                "sample_package-1.5.1-cp39-cp39-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                {"is_wheel": True},
            ),
            self.make_file(
                "sample_package-1.5.1-cp38-cp38-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                {"is_wheel": True},
            ),
            self.make_file(
                "sample_package-1.4.9-cp38-cp38-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                {"is_wheel": True},
            ),
            self.make_file(
                "sample_package-1.4.9-cp37-cp37-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                {"is_wheel": True},
            ),
        ]

    @pytest.fixture
    def sample_package_files(self):
        """Fixture providing v2.0.0 (incompatible) and v1.0.0 (compatible) files."""
        # Version 2.0.0 files - NOT compatible (requires Python 3.11+, but env is 3.10)
        v2_whl = self.make_file(
            "sample_package-2.0.0-py3-none-any.whl",
            {
                "version": packaging.version.Version("2.0.0"),
                "is_wheel": True,
                "requires-python": ">=3.11",
            },
        )
        v2_tar = self.make_file(
            "sample_package-2.0.0.tar.gz",
            {
                "version": packaging.version.Version("2.0.0"),
                "is_wheel": False,
                "requires-python": ">=3.11",
            },
        )

        # Version 1.0.0 files - Compatible (works with Python 3.9+, and env is 3.10)
        v1_whl = self.make_file(
            "sample_package-1.0.0-py3-none-any.whl",
            {
                "version": packaging.version.Version("1.0.0"),
                "is_wheel": True,
                "requires-python": ">=3.9",
            },
        )
        v1_tar = self.make_file(
            "sample_package-1.0.0.tar.gz",
            {
                "version": packaging.version.Version("1.0.0"),
                "is_wheel": False,
                "requires-python": ">=3.9",
            },
        )
        return v1_whl, v1_tar, v2_whl, v2_tar

    @staticmethod
    def extract_versions(files) -> list[str]:
        if not files:
            return []

        return [str(file["version"]) for file in files]

    def filter_files(self, mirrorer, requirement_string, files, required_by=None):
        requirement = parse_requirement(requirement_string)
        # pylint: disable=W0212
        return mirrorer._filter_files(  # noqa: SLF001
            requirement=requirement,
            required_by=required_by,
            files=files,
        )

    @pytest.mark.parametrize(
        ("version_spec", "expected_versions"),
        [
            (">=1.5.0", ["1.6.0", "1.5.2", "1.5.1"]),
            (">=1.5.0,<1.6.0", ["1.5.2", "1.5.1"]),
            ("==1.5.1", ["1.5.1"]),
            (">2.0.0", []),
        ],
        ids=["basic_range", "complex_range", "exact_match", "no_match"],
    )
    def test_filter_files_with_all_versions_mirrored(
        self,
        make_mirrorer,
        sample_files,
        version_spec,
        expected_versions,
    ):
        """Test that file filtering correctly handles different version specifications."""
        mirrorer = make_mirrorer(
            mirror_all_versions=True,
        )

        filtered_files = self.filter_files(
            mirrorer,
            f"sample_package{version_spec}",
            sample_files,
        )

        assert self.extract_versions(filtered_files) == expected_versions

    @pytest.mark.parametrize(
        ("version_spec", "expected_versions"),
        [
            (">=1.5.0", ["1.6.0"]),
            (">=1.5.0,<1.6.0", ["1.5.2"]),
            ("==1.5.1", ["1.5.1"]),
            (">2.0.0", []),
        ],
        ids=["basic_range", "complex_range", "exact_match", "no_match"],
    )
    def test_filter_files_with_latest_version_mirrored(
        self,
        make_mirrorer,
        sample_files,
        version_spec,
        expected_versions,
    ):
        """Test that file filtering correctly handles different version specifications."""
        mirrorer = make_mirrorer(mirror_all_versions=False)

        filtered_files = self.filter_files(
            mirrorer,
            f"sample_package{version_spec}",
            sample_files,
        )

        assert self.extract_versions(filtered_files) == expected_versions

    @pytest.mark.parametrize(
        "filename",
        [
            # platform-specific sdist
            "expandvars-0.6.0.macosx-10.15-x86_64.tar.gz",
            # sdist without a version
            "sample_package.tar.gz",
            # wheel with too few name components
            "sample_package-1.0.0-py3-none.whl",
            # wheel with a non-PEP 440 version
            "sample_package-1.0.0.foo-py3-none-any.whl",
        ],
        ids=[
            "sdist_legacy_platform_version",
            "sdist_missing_version",
            "wheel_missing_tags",
            "wheel_invalid_version",
        ],
    )
    def test_parse_version_skips_invalid_filenames(
        self,
        make_mirrorer,
        filename,
        capsys,
    ):
        """
        A file whose name packaging cannot parse is dropped without output,
        and the files before and after it are still parsed. Any output
        means the generic exception handler caught the error.
        """
        mirrorer = make_mirrorer(mirror_all_versions=True)
        before = self.make_file("sample_package-1.0.0.tar.gz")
        invalid = self.make_file(filename)
        after = self.make_file("sample_package-2.0.0-py3-none-any.whl")

        # pylint: disable=W0212
        files = mirrorer._parse_version_and_tags_in_files(  # noqa: SLF001
            [before, invalid, after],
        )
        # pylint: disable=W0212
        files = mirrorer._filter_files_for_valid_versions(files)  # noqa: SLF001

        assert files == [before, after]
        assert before["version"] == packaging.version.Version("1.0.0")
        assert after["version"] == packaging.version.Version("2.0.0")
        assert "version" not in invalid
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_parse_version_skips_invalid_version_quietly(
        self,
        make_mirrorer,
        monkeypatch,
        capsys,
    ):
        """
        A bare InvalidVersion drops the file without output, and the files
        before and after it are still parsed. The parser is patched because
        packaging 24 wraps InvalidVersion in InvalidSdistFilename, so no
        real filename raises it.
        """

        def raise_invalid_version(_filename):
            msg = "Invalid version: 'x'"
            raise packaging.version.InvalidVersion(msg)

        monkeypatch.setattr(
            packaging.utils,
            "parse_sdist_filename",
            raise_invalid_version,
        )
        mirrorer = make_mirrorer(mirror_all_versions=True)
        before = self.make_file("sample_package-1.0.0-py3-none-any.whl")
        sdist = self.make_file("sample_package-1.5.0.tar.gz")
        after = self.make_file("sample_package-2.0.0-py3-none-any.whl")

        # pylint: disable=W0212
        files = mirrorer._parse_version_and_tags_in_files(  # noqa: SLF001
            [before, sdist, after],
        )
        # pylint: disable=W0212
        files = mirrorer._filter_files_for_valid_versions(files)  # noqa: SLF001

        assert files == [before, after]
        assert before["version"] == packaging.version.Version("1.0.0")
        assert after["version"] == packaging.version.Version("2.0.0")
        assert "version" not in sdist
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_parse_version_reports_unexpected_exception(
        self,
        make_mirrorer,
        monkeypatch,
        capsys,
    ):
        """
        An unexpected exception from the filename parser drops the file,
        prints "Skipping file" and a traceback, and the files before and
        after it are still parsed.
        """

        def raise_value_error(_filename):
            msg = "unexpected"
            raise ValueError(msg)

        monkeypatch.setattr(
            packaging.utils,
            "parse_sdist_filename",
            raise_value_error,
        )
        mirrorer = make_mirrorer(mirror_all_versions=True)
        before = self.make_file("sample_package-1.0.0-py3-none-any.whl")
        sdist = self.make_file("sample_package-1.5.0.tar.gz")
        after = self.make_file("sample_package-2.0.0-py3-none-any.whl")

        # pylint: disable=W0212
        files = mirrorer._parse_version_and_tags_in_files(  # noqa: SLF001
            [before, sdist, after],
        )
        # pylint: disable=W0212
        files = mirrorer._filter_files_for_valid_versions(files)  # noqa: SLF001

        assert files == [before, after]
        assert before["version"] == packaging.version.Version("1.0.0")
        assert after["version"] == packaging.version.Version("2.0.0")
        assert "version" not in sdist
        captured = capsys.readouterr()
        assert "Skipping file sample_package-1.5.0.tar.gz" in captured.out
        assert "Traceback" in captured.err
        assert "ValueError: unexpected" in captured.err

    def test_skipping(self, make_mirrorer):
        """Test that requirement not skipping."""
        mirrorer = make_mirrorer(mirror_all_versions=False)
        required_by = packaging.requirements.Requirement("pyjwt[crypto]==2.10.1")
        requirement = packaging.requirements.Requirement(
            'cryptography>=3.4.0; extra == "crypto"',
        )

        res = {}
        with contextlib.suppress(urllib.error.HTTPError):
            # if skipping then None
            res = mirrorer._mirror(  # noqa: SLF001
                requirement=requirement,
                required_by=required_by,
            )
            # if not skipping then HTTPError

        assert res is not None

    @pytest.mark.parametrize(
        ("version_spec", "expected_versions"),
        [
            (">=1.5.0", ["1.6.0", "1.5.2", "1.5.1"]),
            (">=1.5.0,<1.6.0", ["1.5.2", "1.5.1"]),
            ("==1.5.1", ["1.5.1"]),
            (">2.0.0", []),
        ],
        ids=["basic_range", "complex_range", "exact_match", "no_match"],
    )
    def test_filter_files_with_all_wheels_mirrored(
        self,
        make_mirrorer,
        sample_wheels,
        version_spec,
        expected_versions,
    ):
        """Test that file filtering correctly handles different version specifications."""
        mirrorer = make_mirrorer(mirror_all_versions=True)

        filtered_files = self.filter_files(
            mirrorer,
            f"sample_package{version_spec}",
            sample_wheels,
        )

        assert set(self.extract_versions(filtered_files)) == set(expected_versions)

    @pytest.mark.parametrize(
        ("version_spec", "expected_versions"),
        [
            (">=1.5.0", ["1.6.0"]),
            (">=1.5.0,<1.6.0", ["1.5.2"]),
            ("==1.5.1", ["1.5.1"]),
            (">2.0.0", []),
        ],
        ids=["basic_range", "complex_range", "exact_match", "no_match"],
    )
    def test_filter_files_with_latest_wheel_mirrored(
        self,
        make_mirrorer,
        sample_wheels,
        version_spec,
        expected_versions,
    ):
        """Test that file filtering correctly handles different version specifications."""
        mirrorer = make_mirrorer(mirror_all_versions=False)

        filtered_files = self.filter_files(
            mirrorer,
            f"sample_package{version_spec}",
            sample_wheels,
        )

        assert set(self.extract_versions(filtered_files)) == set(expected_versions)

    @pytest.mark.parametrize(
        ("version_spec", "expected_versions", "number_of_wheels"),
        [
            (">=1.5.0", ["1.6.0", "1.5.2", "1.5.1"], 5),
            (">=1.5.0,<1.6.0", ["1.5.2", "1.5.1"], 4),
            ("==1.5.1", ["1.5.1"], 2),
        ],
        ids=["basic_range", "complex_range", "exact_match"],
    )
    def test_filter_files_with_best_wheel_mirrored(
        self,
        make_mirrorer,
        sample_wheels,
        version_spec,
        expected_versions,
        number_of_wheels,
    ):
        """Test that file filtering correctly handles different version specifications."""
        mirrorer = make_mirrorer(mirror_all_versions=True, mirror_all_wheels=True)

        filtered_files = self.filter_files(
            mirrorer,
            f"sample_package{version_spec}",
            sample_wheels,
        )

        assert set(self.extract_versions(filtered_files)) == set(expected_versions)
        assert len(filtered_files) == number_of_wheels, (
            f"Expected {number_of_wheels} wheel(s) to be selected, but got {len(filtered_files)}"
        )

    def test_filter_files_selects_latest_compatible_version_of_wheel_and_sdist(
        self,
        make_mirrorer,
        sample_package_files,
    ):
        """Check that no incompatible wheel or sdist is being mirrored when multiple versions are available."""
        mirrorer = make_mirrorer(mirror_all_versions=False)

        files = sample_package_files

        filtered_files = self.filter_files(mirrorer, "sample_package>=1.0", files)

        filenames = {f["filename"] for f in filtered_files}
        assert {
            "sample_package-1.0.0-py3-none-any.whl",
            "sample_package-1.0.0.tar.gz",
        } == set(filenames), f"Wrong packages selected. Got: {filenames}"

    def test_filter_files_selects_latest_compatible_version_of_wheel_only(
        self,
        make_mirrorer,
        sample_package_files,
    ):
        """Check that no incompatible wheel or sdist is being mirrored when multiple versions are available."""
        mirrorer = make_mirrorer(mirror_all_versions=False)

        v1_whl, _, v2_whl, v2_tar = sample_package_files

        files = [v1_whl, v2_whl, v2_tar]

        filtered_files = self.filter_files(mirrorer, "sample_package>=1.0", files)

        filenames = {f["filename"] for f in filtered_files}
        assert {
            "sample_package-1.0.0-py3-none-any.whl",
        } == set(filenames), f"Wrong packages selected. Got: {filenames}"

    def test_filter_files_selects_latest_compatible_version_of_sdist_only(
        self,
        make_mirrorer,
        sample_package_files,
    ):
        """Check that no incompatible wheel or sdist is being mirrored when multiple versions are available."""
        mirrorer = make_mirrorer(mirror_all_versions=False)

        _, v1_tar, v2_whl, v2_tar = sample_package_files

        files = [v1_tar, v2_whl, v2_tar]

        filtered_files = self.filter_files(mirrorer, "sample_package>=1.0", files)

        filenames = {f["filename"] for f in filtered_files}
        assert {
            "sample_package-1.0.0.tar.gz",
        } == set(filenames), f"Wrong packages selected. Got: {filenames}"

    def test_prerelease_flag_includes_prereleases(self, make_mirrorer, sample_files):
        mirrorer = make_mirrorer(mirror_all_versions=True, prerelease=True)

        filtered_files = self.filter_files(
            mirrorer,
            "sample_package>=1.5.0",
            sample_files,
        )

        assert self.extract_versions(filtered_files) == [
            "1.7.0rc1",
            "1.6.0",
            "1.5.2",
            "1.5.1",
        ]

    @pytest.mark.parametrize(
        "version_spec",
        [
            # the specifier names a prerelease, which allows prereleases
            "==1.7.0rc1",
            # === is not counted as naming a prerelease, but no final release
            # matches the pin, so the PEP 440 fallback selects the prerelease
            "===1.7.0rc1",
        ],
        ids=["version_matching", "arbitrary_equality"],
    )
    def test_prerelease_pin_mirrored_without_flag(
        self,
        make_mirrorer,
        sample_files,
        version_spec,
    ):
        mirrorer = make_mirrorer(mirror_all_versions=False)

        filtered_files = self.filter_files(
            mirrorer,
            f"sample_package{version_spec}",
            sample_files,
        )

        assert self.extract_versions(filtered_files) == ["1.7.0rc1"]

    @pytest.mark.parametrize(
        "version_spec",
        [">=1.5.0rc1", ">=1.5,<2.0.0dev"],
        ids=["prerelease_lower_bound", "prerelease_exclusion_upper_bound"],
    )
    def test_specifier_naming_prerelease_allows_prerelease_over_final(
        self,
        make_mirrorer,
        sample_files,
        version_spec,
    ):
        # final releases also satisfy these specifiers, so 1.7.0rc1 is
        # selected because the specifier names a prerelease and not through
        # the fallback
        mirrorer = make_mirrorer(mirror_all_versions=False)

        filtered_files = self.filter_files(
            mirrorer,
            f"sample_package{version_spec}",
            sample_files,
        )

        assert self.extract_versions(filtered_files) == ["1.7.0rc1"]

    def test_bare_requirement_excludes_prereleases(self, make_mirrorer, sample_files):
        # an empty SpecifierSet contains prereleases since packaging 26, but
        # Morgan must still exclude them
        mirrorer = make_mirrorer(mirror_all_versions=False)

        filtered_files = self.filter_files(mirrorer, "sample_package", sample_files)

        assert self.extract_versions(filtered_files) == ["1.6.0"]

    def test_only_prereleases_available_falls_back_to_prereleases(
        self,
        make_mirrorer,
        capsys,
    ):
        # no final release satisfies the requirement because the package has
        # only published prereleases
        mirrorer = make_mirrorer(mirror_all_versions=False)
        files = [
            self.make_file("sample_package-0.48b0.tar.gz"),
            self.make_file("sample_package-0.47b0.tar.gz"),
        ]

        filtered_files = self.filter_files(mirrorer, "sample_package", files)

        assert self.extract_versions(filtered_files) == ["0.48b0"]
        assert "No final release matches" in capsys.readouterr().out

    def test_prereleases_used_when_no_final_release_satisfies_specifier(
        self,
        make_mirrorer,
    ):
        # a final release exists but does not satisfy the specifier
        mirrorer = make_mirrorer(mirror_all_versions=True)
        files = [
            self.make_file("sample_package-2.0.0rc1.tar.gz"),
            self.make_file("sample_package-2.0.0b1.tar.gz"),
            self.make_file("sample_package-1.0.0.tar.gz"),
        ]

        filtered_files = self.filter_files(mirrorer, "sample_package>1.0", files)

        assert self.extract_versions(filtered_files) == ["2.0.0rc1", "2.0.0b1"]

    def test_non_matching_prerelease_is_not_used_as_fallback(
        self,
        make_mirrorer,
        capsys,
    ):
        # the fallback only considers prereleases that satisfy the specifier
        mirrorer = make_mirrorer(mirror_all_versions=True)
        files = [
            self.make_file("sample_package-2.0.0rc1.tar.gz"),
        ]

        filtered_files = self.filter_files(mirrorer, "sample_package<1.0", files)

        assert filtered_files is None
        assert "No final release matches" not in capsys.readouterr().out

    def test_dev_release_treated_as_prerelease(self, make_mirrorer):
        files = [
            self.make_file("sample_package-1.0.0.dev1.tar.gz"),
            self.make_file("sample_package-0.9.0.tar.gz"),
        ]

        mirrorer = make_mirrorer(mirror_all_versions=False)
        filtered_files = self.filter_files(mirrorer, "sample_package", files)
        assert self.extract_versions(filtered_files) == ["0.9.0"]

        mirrorer = make_mirrorer(mirror_all_versions=False, prerelease=True)
        filtered_files = self.filter_files(mirrorer, "sample_package", files)
        assert self.extract_versions(filtered_files) == ["1.0.0.dev1"]

    def test_arbitrary_equality_with_non_pep440_version_does_not_raise(
        self,
        make_mirrorer,
        sample_files,
    ):
        # the version in a === specifier need not be a valid PEP 440 version,
        # and such a version must not abort the mirror run
        mirrorer = make_mirrorer(mirror_all_versions=False)

        filtered_files = self.filter_files(
            mirrorer,
            "sample_package===1.0-custom",
            sample_files,
        )

        assert filtered_files is None


class TestEnvironmentPythonVersion:
    @pytest.fixture
    def make_mirrorer(self, tmp_path, make_mirror_args):
        # Return a function that creates a mirrorer whose single environment
        # has the given python_full_version and python_version
        def _make_mirrorer(python_full_version, python_version="3.14"):
            config_path = os.path.join(tmp_path, "morgan.ini")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    f"""
                    [env.test_env]
                    python_version = {python_version}
                    python_full_version = {python_full_version}
                    sys_platform = linux
                    platform_machine = x86_64
                    platform_tag = manylinux.*_x86_64
                    """,
                )
            return Mirrorer(make_mirror_args(tmp_path, config_path))

        return _make_mirrorer

    @staticmethod
    def matches(mirrorer, fileinfo):
        # pylint: disable=W0212
        return Mirrorer._matches_environments(  # noqa: SLF001
            fileinfo,
            mirrorer._supported_pyversions,  # noqa: SLF001
            mirrorer._supported_platforms,  # noqa: SLF001
        )

    @pytest.mark.parametrize(
        ("fileinfo", "expected"),
        [
            # wheel built for the Python minor version of the environment
            (
                {
                    "filename": (
                        "sample_package-1.0.0-cp314-cp314-manylinux_2_17_x86_64.whl"
                    ),
                    "tags": [
                        packaging.tags.Tag("cp314", "cp314", "manylinux_2_17_x86_64"),
                    ],
                },
                True,
            ),
            # requires-python naming the Python minor version of the environment
            (
                {
                    "filename": "sample_package-1.0.0.tar.gz",
                    "requires-python": ">=3.14",
                },
                True,
            ),
            # requires-python naming a newer Python than that of the environment
            (
                {
                    "filename": "sample_package-1.0.0.tar.gz",
                    "requires-python": ">=3.15",
                },
                False,
            ),
            # requires-python satisfied by any recent Python
            (
                {
                    "filename": "sample_package-1.0.0.tar.gz",
                    "requires-python": ">=3.8",
                },
                True,
            ),
            # wheel tagged cp38, which Morgan accepts for CPython 3.8 and newer
            (
                {
                    "filename": "sample_package-1.0.0-cp38-none-any.whl",
                    "tags": [packaging.tags.Tag("cp38", "none", "any")],
                },
                True,
            ),
        ],
        ids=[
            "cp314_wheel",
            "requires_python_3_14",
            "requires_python_3_15",
            "requires_python_3_8",
            "cp38_wheel",
        ],
    )
    def test_prerelease_env_python_matches_as_release(
        self,
        make_mirrorer,
        fileinfo,
        expected,
    ):
        # python_full_version in the configuration may name a prerelease, for
        # example when generate_env wrote it on a release candidate
        # interpreter. Only its release part is compared, so 3.14.0rc1 is
        # compared as 3.14.0.
        mirrorer = make_mirrorer("3.14.0rc1")

        assert self.matches(mirrorer, fileinfo) is expected

    @pytest.mark.parametrize(
        ("python_full_version", "expected"),
        [
            # release candidate interpreter
            ("3.14.0rc1", "3.14.0"),
            # CPython development build
            ("3.13.0a0+", "3.13.0"),
        ],
        ids=["release_candidate", "dev_build"],
    )
    def test_env_python_reduced_to_release_version(
        self,
        make_mirrorer,
        python_full_version,
        expected,
    ):
        mirrorer = make_mirrorer(python_full_version)

        # pylint: disable=W0212
        assert mirrorer._supported_pyversions == [expected]  # noqa: SLF001

    @pytest.mark.parametrize(
        ("marker", "expected"),
        [
            ('python_full_version >= "3.8"', True),
            ('python_full_version < "3.13"', False),
        ],
        ids=["satisfied", "not_satisfied"],
    )
    def test_dev_build_env_python_evaluates_markers(
        self,
        make_mirrorer,
        marker,
        expected,
    ):
        # packaging 24.0 raises InvalidVersion when it evaluates a marker
        # against 3.13.0a0+
        mirrorer = make_mirrorer("3.13.0a0+", python_version="3.13")
        requirement = packaging.requirements.Requirement(f"sample_package; {marker}")

        assert mirrorer.envs["test_env"]["python_full_version"] == "3.13.0a0+local"
        assert (
            morgan.utils.is_requirement_relevant(requirement, mirrorer.envs.values())
            is expected
        )

    @pytest.mark.parametrize(
        "python_full_version",
        ["3.1O", "3.13.0a0++"],
        ids=["typo", "double_plus"],
    )
    def test_invalid_python_full_version_raises(
        self,
        make_mirrorer,
        python_full_version,
    ):
        with pytest.raises(
            ValueError,
            match=rf"^Invalid python_full_version {re.escape(repr(python_full_version))}"
            r" in section \[env\.test_env\]$",
        ):
            make_mirrorer(python_full_version)

    @pytest.mark.parametrize(
        "python_version",
        [
            "3.1O",
            "",
            # a trailing "+" is accepted only in python_full_version
            "3.13+",
        ],
        ids=["typo", "empty", "plus"],
    )
    def test_invalid_python_version_raises_with_valid_full_version(
        self,
        make_mirrorer,
        python_version,
    ):
        # python_version is used for marker evaluation even when
        # python_full_version is given, so it is validated too
        with pytest.raises(
            ValueError,
            match=rf"^Invalid python_version {re.escape(repr(python_version))} in ",
        ):
            make_mirrorer("3.14.0", python_version=python_version)


class TestMirrorDependencyErrors:
    @pytest.fixture
    def mirrorer(self, tmp_path, make_mirror_args):
        config_path = os.path.join(tmp_path, "morgan.ini")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                """
                [env.test_env]
                python_version = 3.10
                sys_platform = linux
                platform_machine = x86_64

                [requirements]
                toplevel = >=1.0
                """,
            )
        return Mirrorer(make_mirror_args(tmp_path, config_path))

    def test_mirror_continues_when_dependency_fails(self, mirrorer, monkeypatch):
        """A 404 on one dependency must not abort the remaining dependencies."""
        toplevel = parse_requirement("toplevel>=1.0")
        processed = []

        def fake_mirror(requirement, required_by=None):
            processed.append(requirement.name)
            if required_by is None:
                # inject one failing and one working dependency; the failing
                # one comes first so an uncaught error would skip the other
                return {
                    "missing-dep": {
                        "requirement": parse_requirement("missing-dep"),
                        "required_by": toplevel,
                    },
                    "present-dep": {
                        "requirement": parse_requirement("present-dep"),
                        "required_by": toplevel,
                    },
                }
            if requirement.name == "missing-dep":
                raise urllib.error.HTTPError(
                    url="https://example.com/missing-dep/",
                    code=404,
                    msg="Not Found",
                    hdrs=email.message.Message(),
                    fp=None,
                )
            return None

        # pylint: disable=W0212
        monkeypatch.setattr(mirrorer, "_mirror", fake_mirror)

        mirrorer.mirror("toplevel>=1.0")

        assert "present-dep" in processed, (
            "Dependencies after a failing one should still be processed"
        )


class TestMirrorCache:
    @pytest.fixture
    def make_mirrorer(self, tmp_path, make_mirror_args, monkeypatch):
        """Return a factory for a Mirrorer that uses a fake index.

        Processing a file of bar reports the dependency bar_requires. The
        factory returns the Mirrorer, the names of the processed files and the
        names of the packages requested from the index.
        """
        config_path = os.path.join(tmp_path, "morgan.ini")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("[env.test_env]\npython_version = 3.10\nplatform_tag = .*\n")
        processed: list[str] = []
        requested: list[str] = []

        def _make(*extra_args, versions=None, bar_requires="foo>=1.0"):
            if versions is None:
                versions = {"foo": ("1.0", "2.0", "2.5"), "bar": ("1.0",)}

            def fake_urlopen(request, timeout):
                name = request.full_url.rstrip("/").rsplit("/", 1)[-1]
                requested.append(name)
                index = {
                    "meta": {"api-version": "1.0"},
                    "files": [
                        {
                            "filename": f"{name}-{v}.tar.gz",
                            "url": f"{name}-{v}.tar.gz",
                            "hashes": {},
                        }
                        for v in versions[name]
                    ],
                }
                response = io.BytesIO(json.dumps(index).encode())
                response.url = request.full_url  # type: ignore[attr-defined]
                return response

            def fake_process_file(requirement, fileinfo):
                processed.append(fileinfo["filename"])
                if requirement.name != "bar":
                    return None
                return {
                    "foo": {
                        "requirement": parse_requirement(bar_requires),
                        "required_by": requirement,
                    },
                }

            monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
            mirrorer = Mirrorer(make_mirror_args(tmp_path, config_path, *extra_args))
            monkeypatch.setattr(mirrorer, "_process_file", fake_process_file)
            return mirrorer, processed, requested

        return _make

    def test_bound_selecting_prerelease_is_not_hidden_by_cache(self, make_mirrorer):
        # foo only has prereleases above 2.5, so foo>2.5 selects 3.0b1
        # through the fallback while the bare foo selects 2.5
        mirrorer, processed, _ = make_mirrorer(
            versions={"foo": ("1.0", "2.5", "3.0b1")},
        )

        mirrorer.mirror("foo")
        mirrorer.mirror("foo>2.5")

        assert processed == ["foo-2.5.tar.gz", "foo-3.0b1.tar.gz"]

    @pytest.mark.parametrize(
        "bar_requires",
        [
            # the version that this dependency records satisfies the
            # top-level requirement foo
            "foo>=1.0",
            # this dependency is the same string as the top-level requirement
            "foo",
        ],
    )
    def test_all_versions_of_toplevel_requirement_mirrored_after_dependency(
        self,
        make_mirrorer,
        bar_requires,
    ):
        mirrorer, processed, _ = make_mirrorer("-a", bar_requires=bar_requires)

        mirrorer.mirror("bar")
        mirrorer.mirror("foo")

        assert processed == [
            "bar-1.0.tar.gz",
            "foo-2.5.tar.gz",
            "foo-2.5.tar.gz",
            "foo-2.0.tar.gz",
            "foo-1.0.tar.gz",
        ]

    def test_all_versions_of_toplevel_requirement_mirrored_after_toplevel(
        self,
        make_mirrorer,
    ):
        mirrorer, processed, _ = make_mirrorer("-a")

        mirrorer.mirror("foo>=2.0")
        mirrorer.mirror("foo>=1.0")

        assert processed == [
            "foo-2.5.tar.gz",
            "foo-2.0.tar.gz",
            "foo-2.5.tar.gz",
            "foo-2.0.tar.gz",
            "foo-1.0.tar.gz",
        ]

    def test_dependency_after_toplevel_requirement_with_all_versions_cached(
        self,
        make_mirrorer,
    ):
        mirrorer, processed, requested = make_mirrorer("-a")

        mirrorer.mirror("foo")
        mirrorer.mirror("bar")

        assert requested == ["foo", "bar"]
        assert processed == [
            "foo-2.5.tar.gz",
            "foo-2.0.tar.gz",
            "foo-1.0.tar.gz",
            "bar-1.0.tar.gz",
        ]

    def test_toplevel_requirement_after_dependency_cached(self, make_mirrorer):
        mirrorer, processed, requested = make_mirrorer()

        mirrorer.mirror("bar")
        mirrorer.mirror("foo")

        assert requested == ["bar", "foo"]
        assert processed == ["bar-1.0.tar.gz", "foo-2.5.tar.gz"]


class TestIsSafeFilename:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("requests-2.0.0-py3-none-any.whl", True),
            ("requests-2.0.0.tar.gz", True),
            ("../../evil-1.0.tar.gz", False),
            ("/etc/cron.d/evil-1.0.tar.gz", False),
            ("..\\evil-1.0.whl", False),
            ("C:evil-1.0.tar.gz", False),
            ("evil-1.0.tar.gz:stream.tar.gz", False),
            ("evil\x00-1.0.tar.gz", False),
            ("..", False),
            (".", False),
            ("", False),
        ],
        ids=[
            "plain_wheel",
            "plain_sdist",
            "relative_traversal",
            "absolute_path",
            "backslash_traversal",
            "windows_drive_relative",
            "ntfs_alternate_data_stream",
            "nul_byte",
            "dotdot",
            "dot",
            "empty",
        ],
    )
    def test_is_safe_filename(self, filename, expected):
        assert is_safe_filename(filename) == expected


class TestDownloadFile:
    @pytest.fixture
    def mirrorer(self, tmp_path, make_mirror_args):
        config_path = os.path.join(tmp_path, "morgan.ini")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                """
                [env.test_env]
                python_version = 3.10
                sys_platform = linux
                platform_machine = x86_64

                [requirements]
                requests = >=2.0.0
                """,
            )
        return Mirrorer(make_mirror_args(tmp_path, config_path))

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/evil-1.0.tar.gz",
        ],
        ids=["file_scheme", "ftp_scheme"],
    )
    def test_download_file_rejects_non_http_schemes(self, mirrorer, url, tmp_path):
        fileinfo = {"url": url, "hashes": {"sha256": "irrelevant"}}
        target = os.path.join(tmp_path, "pkg", "evil-1.0.tar.gz")

        with pytest.raises(ValueError, match="unexpected URL scheme"):
            # pylint: disable=W0212
            mirrorer._download_file(fileinfo, target, "sha256")  # noqa: SLF001

        assert not os.path.exists(target)

    def test_process_file_rejects_unsafe_filename(self, mirrorer):
        fileinfo = {
            "filename": "../../evil-1.0.tar.gz",
            "url": "https://example.com/evil-1.0.tar.gz",
            "hashes": {"sha256": "irrelevant"},
        }
        requirement = parse_requirement("evil")

        with pytest.raises(ValueError, match="Unsafe filename"):
            # pylint: disable=W0212
            mirrorer._process_file(requirement, fileinfo)  # noqa: SLF001

    def test_process_file_rejects_escaping_path(self, mirrorer, monkeypatch):
        # with is_safe_filename bypassed, the path containment check must still
        # reject a filename that escapes the package directory
        monkeypatch.setattr("morgan.is_safe_filename", lambda _filename: True)
        fileinfo = {
            "filename": "../../evil-1.0.tar.gz",
            "url": "https://example.com/evil-1.0.tar.gz",
            "hashes": {"sha256": "irrelevant"},
        }
        requirement = parse_requirement("evil")

        with pytest.raises(ValueError, match="Unsafe file path"):
            # pylint: disable=W0212
            mirrorer._process_file(requirement, fileinfo)  # noqa: SLF001


class TestMirrorConfigValidation:
    def test_mirror_reports_missing_requirements_section(
        self,
        tmp_path,
        make_mirror_args,
    ):
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
        args = make_mirror_args(tmp_path, config_path, "--skip-server-copy")

        with pytest.raises(ValueError, match=r"no \[requirements\] section"):
            morgan.mirror(args)


class TestMirrorRequirementLines:
    def test_mirror_splits_requirement_lines(self, tmp_path, monkeypatch):
        config_path = os.path.join(tmp_path, "morgan.ini")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                """
                [env.test_env]
                python_version = 3.10
                sys_platform = linux
                platform_machine = x86_64

                [requirements]
                foo =
                  ==2.0
                  ==2.1
                qux = ==1
                qux = ==2
                bar = ==1.0
                baz =
                """,
            )
        args = argparse.Namespace(
            index_path=str(tmp_path),
            index_url=PYPI_ADDRESS,
            config=config_path,
            mirror_all_versions=False,
            package_type_regex=r"(whl|zip|tar\.gz)",
            mirror_all_wheels=False,
            prerelease=False,
            target_url=None,
            use_pypi_metadata=False,
            skip_server_copy=True,
        )
        recorded = []

        def fake_mirror(_self, requirement_string):
            recorded.append(requirement_string)

        monkeypatch.setattr(Mirrorer, "mirror", fake_mirror)

        morgan.mirror(args)

        assert recorded == [
            "foo==2.0",
            "foo==2.1",
            "qux==1",
            "qux==2",
            "bar==1.0",
            "baz",
        ], (
            "mirror() must be called once for every non-empty line, and not "
            "for an empty line"
        )


class _FakeResponse:
    """Replaces the HTTP response that urllib.request.urlopen returns."""

    def __init__(self, url, body):
        self.url = url
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return None


class _FakeMetadata:
    """Replaces metadata.MetadataParser and returns fixed dependencies."""

    def __init__(self, deps):
        self._deps = deps

    def dependencies(self, extras, envs):
        return {parse_requirement(dep) for dep in self._deps}


# The versions of each package that the fake index in TestNoDeps serves.
NO_DEPS_INDEX = {
    "bar": ["1.0"],
    "foo": ["2.0", "2.1"],
}
# The dependencies that each package declares in TestNoDeps.
NO_DEPS_DEPENDENCIES = {
    "bar": ["foo>=2.0"],
    "foo": [],
}


class TestNoDeps:
    @pytest.fixture
    def config_path(self, tmp_path):
        config_path = os.path.join(tmp_path, "morgan.ini")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                """
                [env.test_env]
                python_version = 3.10
                sys_platform = linux
                platform_machine = x86_64

                [requirements]
                bar = ==1.0
                foo = ==2.0
                """,
            )
        return config_path

    @pytest.fixture
    def make_args(self, tmp_path, config_path):
        def _make_args(**overrides):
            kwargs = {
                "index_path": str(tmp_path),
                "index_url": PYPI_ADDRESS,
                "config": config_path,
                "mirror_all_versions": False,
                "package_type_regex": r"(whl|zip|tar\.gz)",
                "mirror_all_wheels": False,
                "prerelease": False,
                "target_url": None,
                "use_pypi_metadata": False,
                "skip_server_copy": True,
            }
            kwargs.update(overrides)
            return argparse.Namespace(**kwargs)

        return _make_args

    @pytest.fixture
    def downloads(self, monkeypatch):
        """
        Replace the index, the download and the metadata parser with fakes
        that serve the packages in NO_DEPS_INDEX. Return the list that
        collects the name of every file the mirrorer downloads.
        """
        downloaded = []

        def fake_urlopen(request, timeout=None):
            name = request.full_url.rstrip("/").rsplit("/", 1)[-1]
            files = [
                {
                    "filename": f"{name}-{version}-py3-none-any.whl",
                    "url": f"{name}-{version}-py3-none-any.whl",
                    "hashes": {"sha256": "0" * 64},
                }
                for version in NO_DEPS_INDEX[name]
            ]
            body = json.dumps(
                {"meta": {"api-version": "1.0"}, "name": name, "files": files},
            ).encode()
            return _FakeResponse(request.full_url, body)

        def fake_download_file(_self, fileinfo, target, hashalg):
            downloaded.append(fileinfo["filename"])
            return True

        def fake_extract_metadata_from_file(_self, filepath):
            name = os.path.basename(filepath).split("-", 1)[0]
            return _FakeMetadata(NO_DEPS_DEPENDENCIES[name])

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr(Mirrorer, "_download_file", fake_download_file)
        monkeypatch.setattr(
            Mirrorer,
            "_extract_metadata_from_file",
            fake_extract_metadata_from_file,
        )
        return downloaded

    @staticmethod
    def make_fileinfo(filename):
        return {
            "filename": filename,
            "url": f"https://example.com/{filename}",
            "hashes": {"sha256": "0" * 64},
            "version": packaging.version.Version("1.0"),
        }

    @pytest.mark.parametrize(
        ("no_deps", "expected"),
        [
            (
                True,
                ["bar-1.0-py3-none-any.whl", "foo-2.0-py3-none-any.whl"],
            ),
            (
                False,
                [
                    "bar-1.0-py3-none-any.whl",
                    "foo-2.0-py3-none-any.whl",
                    "foo-2.1-py3-none-any.whl",
                ],
            ),
        ],
        ids=["no_deps", "with_deps"],
    )
    def test_mirror_only_listed_requirements(
        self,
        make_args,
        downloads,
        no_deps,
        expected,
    ):
        morgan.mirror(make_args(no_deps=no_deps))

        assert sorted(downloads) == expected

    def test_dependencies_followed_without_attribute(self, make_args, downloads):
        # library callers may build the Namespace without no_deps
        morgan.mirror(make_args())

        assert "foo-2.1-py3-none-any.whl" in downloads

    def test_metadata_file_is_written(self, make_args, tmp_path, monkeypatch):
        filename = "x-1.0-py3-none-any.whl"
        pkgdir = os.path.join(tmp_path, "x")
        os.makedirs(pkgdir)
        filepath = os.path.join(pkgdir, filename)
        with zipfile.ZipFile(filepath, "w") as whl:
            whl.writestr(
                "x-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                "Name: x\n"
                "Version: 1.0\n"
                "Requires-Dist: foo>=2.0\n",
            )

        mirrorer = Mirrorer(make_args(no_deps=True))
        monkeypatch.setattr(mirrorer, "_download_file", lambda *_args: True)

        # pylint: disable=W0212
        result = mirrorer._process_file(  # noqa: SLF001
            parse_requirement("x==1.0"),
            self.make_fileinfo(filename),
        )

        assert result is None
        assert os.path.isfile(f"{filepath}.metadata")

    def test_no_pypi_metadata_request(self, make_args, monkeypatch):
        mirrorer = Mirrorer(make_args(no_deps=True, use_pypi_metadata=True))
        monkeypatch.setattr(mirrorer, "_download_file", lambda *_args: True)
        pypi_requests = []

        def fake_extract_metadata_from_pypi(package, version):
            pypi_requests.append(package)
            return _FakeMetadata(["foo>=2.0"])

        monkeypatch.setattr(
            mirrorer,
            "_extract_metadata_from_pypi",
            fake_extract_metadata_from_pypi,
        )

        # pylint: disable=W0212
        result = mirrorer._process_file(  # noqa: SLF001
            parse_requirement("x==1.0"),
            self.make_fileinfo("x-1.0-py3-none-any.whl"),
        )

        assert result is None
        assert pypi_requests == []

    @pytest.mark.parametrize(
        ("extra_argv", "expected"),
        [([], False), (["--no-deps"], True)],
        ids=["without_flag", "with_flag"],
    )
    def test_cli_flag(self, config_path, tmp_path, monkeypatch, extra_argv, expected):
        captured: list[argparse.Namespace] = []
        monkeypatch.setattr(morgan, "mirror", captured.append)
        monkeypatch.setattr(
            sys,
            "argv",
            ["morgan", "-i", str(tmp_path), *extra_argv, "mirror"],
        )

        morgan.main()

        assert len(captured) == 1
        assert captured[0].no_deps is expected
