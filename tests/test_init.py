# pylint: disable=missing-function-docstring,missing-class-docstring,missing-module-docstring
from __future__ import annotations

import argparse
import hashlib
import os

import packaging.requirements
import packaging.version
import pytest

from morgan import PYPI_ADDRESS, Mirrorer, parse_interpreter, parse_requirement, server


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

    def test_mirrorer_initialization(self, temp_index_path):
        args = argparse.Namespace(
            index_path=temp_index_path,
            index_url="https://pypi.org/simple/",
            config=os.path.join(temp_index_path, "morgan.ini"),
            mirror_all_versions=False,
            package_type_regex="(whl|zip|tar.gz)",
            mirror_all_wheels=False,
        )

        mirrorer = Mirrorer(args)

        assert mirrorer.index_path == temp_index_path
        assert mirrorer.index_url == "https://pypi.org/simple/"
        assert "test_env" in mirrorer.envs
        assert mirrorer.envs["test_env"]["python_version"] == "3.10"
        assert mirrorer.envs["test_env"]["sys_platform"] == "linux"
        assert mirrorer.envs["test_env"]["platform_machine"] == "x86_64"
        assert not mirrorer.mirror_all_versions

    def test_server_file_copying(self, temp_index_path):
        args = argparse.Namespace(
            index_path=temp_index_path,
            index_url=PYPI_ADDRESS,
            config=os.path.join(temp_index_path, "morgan.ini"),
            mirror_all_versions=False,
            package_type_regex="(whl|zip|tar.gz)",
            mirror_all_wheels=False,
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

    def test_file_hashing(self, temp_index_path):
        args = argparse.Namespace(
            index_path=temp_index_path,
            index_url=PYPI_ADDRESS,
            config=os.path.join(temp_index_path, "morgan.ini"),
            mirror_all_versions=False,
            package_type_regex="(whl|zip|tar.gz)",
            mirror_all_wheels=False,
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
    def make_mirrorer(self, temp_index_path):
        # Return a function that creates mirrorer instances
        def _make_mirrorer(mirror_all_versions, mirror_all_wheels=False):
            args = argparse.Namespace(
                index_path=temp_index_path,
                index_url="https://example.com/simple",
                config=os.path.join(temp_index_path, "morgan.ini"),
                mirror_all_versions=mirror_all_versions,
                package_type_regex=r"(whl|zip|tar\.gz)",
                mirror_all_wheels=mirror_all_wheels,
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
        return [
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
        requirement = packaging.requirements.Requirement(
            f"sample_package{version_spec}",
        )

        # pylint: disable=W0212
        filtered_files = mirrorer._filter_files(  # noqa: SLF001
            requirement=requirement,
            required_by=None,
            files=sample_files,
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
        requirement = packaging.requirements.Requirement(
            f"sample_package{version_spec}",
        )

        # pylint: disable=W0212
        filtered_files = mirrorer._filter_files(  # noqa: SLF001
            requirement=requirement,
            required_by=None,
            files=sample_files,
        )

        assert self.extract_versions(filtered_files) == expected_versions

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
        requirement = packaging.requirements.Requirement(
            f"sample_package{version_spec}",
        )

        # pylint: disable=W0212
        filtered_files = mirrorer._filter_files(  # noqa: SLF001
            requirement=requirement,
            required_by=None,
            files=sample_wheels,
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
        requirement = packaging.requirements.Requirement(
            f"sample_package{version_spec}",
        )

        # pylint: disable=W0212
        filtered_files = mirrorer._filter_files(  # noqa: SLF001
            requirement=requirement,
            required_by=None,
            files=sample_wheels,
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
        requirement = packaging.requirements.Requirement(
            f"sample_package{version_spec}",
        )

        # pylint: disable=W0212
        filtered_files = mirrorer._filter_files(  # noqa: SLF001
            requirement=requirement,
            required_by=None,
            files=sample_wheels,
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
        requirement = packaging.requirements.Requirement("sample_package>=1.0")

        files = sample_package_files

        # pylint: disable=W0212
        filtered_files = mirrorer._filter_files(  # noqa: SLF001
            requirement=requirement,
            required_by=None,
            files=files,
        )

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
        requirement = packaging.requirements.Requirement("sample_package>=1.0")

        # pylint: disable=W0212
        filtered_files = mirrorer._filter_files(  # noqa: SLF001
            requirement=requirement,
            required_by=None,
            files=files,
        )

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
        requirement = packaging.requirements.Requirement("sample_package>=1.0")

        # pylint: disable=W0212
        filtered_files = mirrorer._filter_files(  # noqa: SLF001
            requirement=requirement,
            required_by=None,
            files=files,
        )

        filenames = {f["filename"] for f in filtered_files}
        assert {
            "sample_package-1.0.0.tar.gz",
        } == set(filenames), f"Wrong packages selected. Got: {filenames}"
