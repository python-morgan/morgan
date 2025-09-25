import gzip
import io
import json
import urllib.error
from http.client import HTTPMessage
from typing import BinaryIO
from unittest.mock import MagicMock, patch

import pytest
from packaging.version import Version

from morgan import Mirrorer
from morgan.metadata import PyPIMetadataParser


class TestPyPIMetadataParser:
    @pytest.fixture
    def basic_package_data(self):
        """Basic package data from PyPI JSON API"""
        return {
            "info": {
                "name": "example-package",
                "version": "1.0.0",
                "requires_python": ">=3.7",
                "requires_dist": ["requests>=2.0.0"],
            },
        }

    @pytest.fixture
    def realistic_package_data(self):
        """Complex package data with extras and conditional dependencies"""
        return {
            "info": {
                "author": "Kenneth Reitz",
                "author_email": "me@kennethreitz.org",
                "bugtrack_url": None,
                "classifiers": [
                    "Development Status :: 5 - Production/Stable",
                    "Environment :: Web Environment",
                    "Intended Audience :: Developers",
                    "License :: OSI Approved :: Apache Software License",
                    "Natural Language :: English",
                    "Operating System :: OS Independent",
                    "Programming Language :: Python",
                    "Programming Language :: Python :: 3",
                    "Programming Language :: Python :: 3 :: Only",
                    "Programming Language :: Python :: 3.10",
                    "Programming Language :: Python :: 3.11",
                    "Programming Language :: Python :: 3.12",
                    "Programming Language :: Python :: 3.13",
                    "Programming Language :: Python :: 3.14",
                    "Programming Language :: Python :: 3.9",
                    "Programming Language :: Python :: Implementation :: CPython",
                    "Programming Language :: Python :: Implementation :: PyPy",
                    "Topic :: Internet :: WWW/HTTP",
                    "Topic :: Software Development :: Libraries",
                ],
                "description": "# Requests",
                "description_content_type": "text/markdown",
                "docs_url": None,
                "download_url": None,
                "downloads": {"last_day": -1, "last_month": -1, "last_week": -1},
                "dynamic": [
                    "Author",
                    "Author-Email",
                    "Classifier",
                    "Description",
                    "Description-Content-Type",
                    "Home-Page",
                    "License",
                    "License-File",
                    "Project-Url",
                    "Provides-Extra",
                    "Requires-Dist",
                    "Requires-Python",
                    "Summary",
                ],
                "home_page": "https://requests.readthedocs.io",
                "keywords": None,
                "license": "Apache-2.0",
                "license_expression": None,
                "license_files": ["LICENSE"],
                "maintainer": None,
                "maintainer_email": None,
                "name": "requests",
                "package_url": "https://pypi.org/project/requests/",
                "platform": None,
                "project_url": "https://pypi.org/project/requests/",
                "project_urls": {
                    "Documentation": "https://requests.readthedocs.io",
                    "Homepage": "https://requests.readthedocs.io",
                    "Source": "https://github.com/psf/requests",
                },
                "provides_extra": ["security", "socks", "use-chardet-on-py3"],
                "release_url": "https://pypi.org/project/requests/2.32.5/",
                "requires_dist": [
                    "charset_normalizer<4,>=2",
                    "idna<4,>=2.5",
                    "urllib3<3,>=1.21.1",
                    "certifi>=2017.4.17",
                    'PySocks!=1.5.7,>=1.5.6; extra == "socks"',
                    'chardet<6,>=3.0.2; extra == "use-chardet-on-py3"',
                ],
                "requires_python": ">=3.9",
                "summary": "Python HTTP for Humans.",
                "version": "2.32.5",
                "yanked": False,
                "yanked_reason": None,
            },
        }

    def setup_mock_response(self, mock_urlopen, data, gzip_encoded=False):
        """Helper to set up a mock HTTP response"""
        mock_response = MagicMock()
        mock_response.headers = {}

        if gzip_encoded:
            mock_response.headers["Content-Encoding"] = "gzip"
            # Create gzipped content
            json_bytes = json.dumps(data).encode("utf-8")
            gzipped = io.BytesIO()
            with gzip.GzipFile(fileobj=gzipped, mode="wb") as f:
                f.write(json_bytes)
            gzipped.seek(0)
            # Make the mock response behave like a file-like object for gzip.GzipFile
            mock_response.read = gzipped.read
            mock_response.read1 = gzipped.read1
        else:
            mock_response.read.return_value = json.dumps(data).encode("utf-8")

        # Mock the context manager behavior
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

    def test_basic_parsing(self, basic_package_data):
        """Test basic metadata parsing from PyPI"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            self.setup_mock_response(mock_urlopen, basic_package_data)

            parser = PyPIMetadataParser("example-package", "1.0.0")
            result = parser.parse_pypi()

            assert result.name == "example-package"
            assert result.version == Version("1.0.0")
            assert str(result.python_requirement) == ">=3.7"
            assert len(result.core_dependencies) == 1
            assert next(iter(result.core_dependencies)).name == "requests"

            # Verify the URL was correctly constructed
            mock_urlopen.assert_called_once()
            request = mock_urlopen.call_args[0][0]
            assert (
                request.full_url == "https://pypi.org/pypi/example-package/1.0.0/json"
            )

    def test_complex_parsing_with_extras(self, realistic_package_data):
        """Test parsing with extras and conditional dependencies"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            self.setup_mock_response(mock_urlopen, realistic_package_data)

            parser = PyPIMetadataParser("requests", "2.32.5")
            result = parser.parse_pypi()

            assert result.name == "requests"
            assert result.version == Version("2.32.5")
            assert str(result.python_requirement) == ">=3.9"
            assert result.extras_provided == {"security", "socks", "use-chardet-on-py3"}

            # Check core dependencies
            assert len(result.core_dependencies) == 4
            core_dep_names = {dep.name for dep in result.core_dependencies}
            assert "charset_normalizer" in core_dep_names
            assert "idna" in core_dep_names
            assert "urllib3" in core_dep_names
            assert "certifi" in core_dep_names

            # Check optional dependencies
            assert set(result.optional_dependencies.keys()) == {
                "socks",
                "use-chardet-on-py3",
            }
            assert len(result.optional_dependencies["socks"]) == 1
            assert next(iter(result.optional_dependencies["socks"])).name == "PySocks"
            assert len(result.optional_dependencies["use-chardet-on-py3"]) == 1
            assert (
                next(iter(result.optional_dependencies["use-chardet-on-py3"])).name
                == "chardet"
            )

    def test_gzip_encoded_response(self, basic_package_data):
        """Test handling of gzip-encoded responses"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            self.setup_mock_response(
                mock_urlopen,
                basic_package_data,
                gzip_encoded=True,
            )

            parser = PyPIMetadataParser("example-package", "1.0.0")
            result = parser.parse_pypi()

            assert result.name == "example-package"
            assert result.version == Version("1.0.0")

            # Verify correct headers were sent
            request = mock_urlopen.call_args[0][0]
            headers_lower = {
                key.lower(): value for key, value in request.headers.items()
            }  # RFC 2616 specifies headers to be case-insensitive
            assert headers_lower["accept-encoding"] == "gzip"

    def test_http_error_handling(self):
        """Test handling of HTTP errors"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                "https://pypi.org/pypi/nonexistent/1.0.0/json",
                404,
                "Not Found",
                HTTPMessage(),
                None,
            )

            parser = PyPIMetadataParser("nonexistent", "1.0.0")
            with pytest.raises(urllib.error.HTTPError):
                parser.parse_pypi()

    def test_version_mismatch_handling(self):
        """Test handling of version mismatch between requested and received"""
        data = {
            "info": {
                "name": "example-package",
                "version": "1.0.1",  # Different from requested version
            },
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            self.setup_mock_response(mock_urlopen, data)

            parser = PyPIMetadataParser("example-package", "1.0.0")
            with pytest.raises(ValueError, match="Version mismatch"):
                parser.parse_pypi()

    def test_version_equivalent_non_normalized(self):
        """Test that a PEP 440 equivalent but non-normalized version is accepted"""
        data = {
            "info": {
                "name": "example-package",
                "version": "0.6c1",  # PyPI-stored form of the normalized "0.6rc1"
            },
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            self.setup_mock_response(mock_urlopen, data)

            parser = PyPIMetadataParser("example-package", "0.6rc1")
            result = parser.parse_pypi()

            assert result.version == Version("0.6rc1")

    def test_parse_overridden(self):
        """Test that parse method is properly overridden to raise NotImplementedError"""
        parser = PyPIMetadataParser("example-package", "1.0.0")
        with pytest.raises(NotImplementedError):
            parser.parse(lambda x: MagicMock(spec=BinaryIO), "filename")


class TestMirrorerPyPIMetadata:
    @pytest.fixture
    def mirrorer_args(self):
        """Create mock args for Mirrorer initialization"""
        args = MagicMock()
        args.index_path = "/dummy/path"
        args.index_url = "https://pypi.org/simple/"
        args.config = "morgan.ini"
        args.target_url = None
        args.use_pypi_metadata = True
        return args

    @pytest.fixture
    def mirrorer(self, mirrorer_args):
        """Create a Mirrorer instance with mocked dependencies"""
        with patch("morgan.Mirrorer._find_target_registry"), patch(
            "configparser.ConfigParser.read",
        ), patch("configparser.ConfigParser.__getitem__", return_value={}):
            mirrorer = Mirrorer(mirrorer_args)
            # pylint: disable=W0212
            mirrorer._hash_file = MagicMock(return_value="hash123")  # type: ignore[method-assign] # noqa: SLF001
            return mirrorer

    def test_extract_metadata_from_pypi(self, mirrorer):
        """Test _extract_metadata_from_pypi method"""
        package = "example-package"
        version = Version("1.0.0")

        with patch("morgan.metadata.PyPIMetadataParser.parse_pypi") as mock_parse_pypi:
            mock_parse_pypi.return_value = MagicMock()

            # pylint: disable=W0212
            mirrorer._extract_metadata_from_pypi(package, version)  # noqa: SLF001

            # Verify PyPIMetadataParser was initialized with correct parameters
            mock_parse_pypi.assert_called_once()

    def test_extract_metadata_from_pypi_http_error(self, mirrorer):
        """Test error handling in _extract_metadata_from_pypi"""
        package = "example-package"
        version = Version("1.0.0")

        with patch(
            "morgan.metadata.PyPIMetadataParser.parse_pypi",
        ) as mock_parse_pypi:
            mock_parse_pypi.side_effect = urllib.error.HTTPError(
                "url",
                404,
                "Not Found",
                HTTPMessage(),
                None,
            )

            with pytest.raises(urllib.error.HTTPError):
                # pylint: disable=W0212
                mirrorer._extract_metadata_from_pypi(package, version)  # noqa: SLF001
