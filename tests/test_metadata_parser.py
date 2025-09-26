import io
from unittest.mock import patch

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from morgan.metadata import MetadataParser


class TestMetadataParser:
    @pytest.fixture
    def parser(self):
        """Basic parser instance for testing"""
        return MetadataParser("example-package-1.0.0.tar.gz")

    @pytest.fixture
    def metadata_content(self):
        """Sample metadata content as it would appear in a PKG-INFO file"""
        return b"""Metadata-Version: 2.1
Name: example-package
Version: 1.0.0
Summary: An example package for testing
Author: Test Author
Author-email: author@example.com
Requires-Python: >=3.7
Requires-Dist: requests>=2.0.0
Requires-Dist: flask>=2.0.0; extra == 'web'
Requires-Dist: pytest>=6.0.0; extra == 'test'
Requires-Dist: numpy>=1.20.0; python_version >= '3.8'
Provides-Extra: web
Provides-Extra: test
"""

    @pytest.fixture
    def pyproject_content(self):
        """Sample pyproject.toml content"""
        return b"""[build-system]
requires = ["setuptools>=42", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "example-package"
version = "1.0.0"
requires-python = ">=3.7"
dependencies = ["requests>=2.0.0"]

[project.optional-dependencies]
web = ["flask>=2.0.0"]
test = ["pytest>=6.0.0"]
"""

    @pytest.fixture
    def requires_txt_content(self):
        """Sample requires.txt content"""
        return b"""requests>=2.0.0
click>=8.0.0

[web]
flask>=2.0.0
jinja2>=3.0.0

[test]
pytest>=6.0.0
"""

    def test_init(self, parser):
        """Test that the parser initializes correctly"""
        assert parser.source_path == "example-package-1.0.0.tar.gz"
        assert parser.name is None
        assert parser.version is None
        assert parser.python_requirement is None
        assert parser.extras_provided == set()
        assert parser.core_dependencies == set()
        assert parser.optional_dependencies == {}
        assert parser.build_dependencies == set()

    def test_parse_metadata_file(self, parser, metadata_content):
        """Test parsing a metadata file with dependencies"""
        mock_fp = io.BytesIO(metadata_content)
        # pylint: disable=W0212
        parser._parse_metadata_file(mock_fp)  # noqa: SLF001

        assert parser.name == "example-package"
        assert parser.version == Version("1.0.0")
        assert str(parser.python_requirement) == ">=3.7"
        assert parser.extras_provided == {"web", "test"}

        assert len(parser.core_dependencies) == 2  # requests and numpy
        assert {dep.name for dep in parser.core_dependencies} == {
            "requests",
            "numpy",
        }

        assert set(parser.optional_dependencies.keys()) == {"web", "test"}
        assert len(parser.optional_dependencies["web"]) == 1
        assert next(iter(parser.optional_dependencies["web"])).name == "flask"

    def test_parse_pyproject(self, parser, pyproject_content):
        """Test parsing a pyproject.toml file"""
        mock_fp = io.BytesIO(pyproject_content)
        with patch(
            "tomli.load",
            return_value={
                "build-system": {"requires": ["setuptools>=42", "wheel"]},
                "project": {
                    "name": "example-package",
                    "version": "1.0.0",
                    "requires-python": ">=3.7",
                    "dependencies": ["requests>=2.0.0"],
                    "optional-dependencies": {
                        "web": ["flask>=2.0.0"],
                        "test": ["pytest>=6.0.0"],
                    },
                },
            },
        ):
            # pylint: disable=W0212
            parser._parse_pyproject(mock_fp)  # noqa: SLF001

        assert parser.name == "example-package"
        assert parser.version == Version("1.0.0")
        assert str(parser.python_requirement) == ">=3.7"

        assert len(parser.core_dependencies) == 1
        assert next(iter(parser.core_dependencies)).name == "requests"

        assert set(parser.optional_dependencies.keys()) == {"web", "test"}
        assert len(parser.build_dependencies) == 2
        assert {dep.name for dep in parser.build_dependencies} == {
            "setuptools",
            "wheel",
        }

    def test_parse_requirestxt(self, parser, requires_txt_content):
        """Test parsing a requires.txt file"""
        mock_fp = io.BytesIO(requires_txt_content)
        mock_fp.name = "package.egg-info/requires.txt"  # Not setup_requires.txt

        # pylint: disable=W0212
        parser._parse_requirestxt(mock_fp)  # noqa: SLF001

        assert len(parser.core_dependencies) == 2
        assert {dep.name for dep in parser.core_dependencies} == {
            "requests",
            "click",
        }

        assert set(parser.optional_dependencies.keys()) == {"web", "test"}
        assert len(parser.optional_dependencies["web"]) == 2
        assert len(parser.optional_dependencies["test"]) == 1
        assert {dep.name for dep in parser.optional_dependencies["web"]} == {
            "flask",
            "jinja2",
        }

    @pytest.mark.parametrize(
        ("extras", "python_version", "expected_count"),
        [
            (set(), "3.7", 3),  # numpy excluded due to marker
            (set(), "3.8", 4),  # numpy included due to marker
            ({"web"}, "3.7", 4),  # numpy exlcuded, with optional flask
            ({"web", "test"}, "3.8", 6),  # numpy included and all optionals
        ],
    )
    def test_dependencies_resolution_with_python_version(
        self,
        parser,
        extras,
        python_version,
        expected_count,
    ):
        """Test resolving dependencies with extras and environments"""
        # Set up dependencies
        parser.core_dependencies = {
            Requirement("requests>=2.0.0"),
            Requirement("numpy>=1.20.0; python_version >= '3.8'"),
        }
        parser.optional_dependencies = {
            "web": {Requirement("flask>=2.0.0")},
            "test": {Requirement("pytest>=6.0.0")},
        }
        parser.build_dependencies = {
            Requirement("setuptools>=42"),
            Requirement("wheel"),
        }

        deps = parser.dependencies(extras, [{"python_version": python_version}])
        assert len(deps) == expected_count


class TestMetadataParserRequiresDist:
    @pytest.fixture
    def parser(self):
        """Basic parser instance for testing"""
        return MetadataParser("example-package-1.0.0.tar.gz")

    def test_parse_requires_dist_simple(self, parser):
        """Test parsing a simple requirement without extras or markers"""
        # pylint: disable=W0212
        parser._parse_requires_dist("requests>=2.0.0")  # noqa: SLF001

        assert len(parser.core_dependencies) == 1
        req = parser.core_dependencies.pop()
        assert req.name == "requests"
        assert str(req.specifier) == ">=2.0.0"
        assert not req.extras
        assert req.marker is None

    def test_parse_requires_dist_with_env_marker(self, parser):
        """Test parsing a requirement with environment marker (non-extra)"""
        # pylint: disable=W0212
        parser._parse_requires_dist("numpy>=1.20.0; python_version >= '3.8'")  # noqa: SLF001

        assert len(parser.core_dependencies) == 1
        req = parser.core_dependencies.pop()
        assert req.name == "numpy"
        assert req.marker is not None

    def test_parse_requires_dist_with_extra(self, parser):
        """Test parsing a requirement with an extra marker"""
        # pylint: disable=W0212
        parser._parse_requires_dist('flask>=2.0.0; extra == "web"')  # noqa: SLF001

        assert len(parser.core_dependencies) == 0
        assert "web" in parser.optional_dependencies
        assert len(parser.optional_dependencies["web"]) == 1

        req = parser.optional_dependencies["web"].pop()
        assert req.name == "flask"
        assert str(req.specifier) == ">=2.0.0"
        assert req.marker is not None
