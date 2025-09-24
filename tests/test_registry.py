import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import pytest

from morgan.registry import GitLabRegistry, LocalRegistry, Registry


class TestRegistry:
    """Tests for the abstract Registry base class."""

    def test_registry_is_abstract(self):
        """Test that Registry cannot be instantiated directly."""
        with pytest.raises(TypeError):
            Registry()  # type: ignore[abstract]

    def test_registry_requires_has_package_implementation(self):
        """Test that subclasses must implement has_package method."""

        class IncompleteRegistry(Registry):
            @property
            def name(self) -> str:
                return "Incomplete"

        with pytest.raises(TypeError):
            IncompleteRegistry()  # type: ignore[abstract]

    def test_registry_requires_name_property(self):
        """Test that subclasses must implement name property."""

        class IncompleteRegistry(Registry):
            def has_package(
                self,
                file_name,
                package_name,
                hash_alg,
                expected_hash=None,
            ):
                return False

        with pytest.raises(TypeError):
            IncompleteRegistry()  # type: ignore[abstract]


class TestLocalRegistry:
    """Tests for the LocalRegistry implementation."""

    @pytest.fixture
    def mock_file_hashing(self) -> Callable[[Path, str], str]:
        """Create a mock hash function."""

        def hash_func(file_path: Path, hash_alg: str) -> str:
            # Simple mock that returns a predictable hash based on file content
            with open(file_path, "rb") as f:
                content = f.read()
                if hash_alg == "sha256":
                    return hashlib.sha256(content).hexdigest()
                if hash_alg == "md5":
                    return hashlib.md5(content).hexdigest()  # noqa: S324
                if hash_alg is None:
                    return "no_hash"

                msg = f"Unsupported hash algorithm: {hash_alg}"
                raise ValueError(msg)

        return hash_func

    @pytest.fixture
    def local_registry(self, tmp_path: Path, mock_file_hashing) -> LocalRegistry:
        """Create a LocalRegistry instance for testing."""
        return LocalRegistry(mock_file_hashing, str(tmp_path))

    @pytest.fixture
    def create_package_file(self, tmp_path: Path):
        """Factory fixture to create package files with specified parameters."""

        def _create_package_file(
            package_name: str,
            file_name: str,
            content: str = "test content",
        ) -> Path:
            """Create a package directory and file.

            Args:
                package_name: Name of the package directory
                file_name: Name of the package file
                content: Content to write to the file

            Returns:
                Path to the created file

            """
            package_dir = tmp_path / package_name
            package_dir.mkdir(parents=True, exist_ok=True)
            test_file = package_dir / file_name
            test_file.write_text(content)
            return test_file

        return _create_package_file

    def test_local_registry_initialization(self, mock_file_hashing, tmp_path: Path):
        """Test that LocalRegistry initializes correctly."""
        registry = LocalRegistry(mock_file_hashing, str(tmp_path))
        assert registry.hash_file_func == mock_file_hashing
        assert registry.index_path == str(tmp_path)

    def test_local_registry_name(self, local_registry: LocalRegistry):
        """Test that LocalRegistry returns correct name."""
        assert local_registry.name == "Local"

    def test_has_package_file_does_not_exist(self, local_registry: LocalRegistry):
        """Test has_package returns False when file doesn't exist."""
        result = local_registry.has_package(
            file_name="nonexistent-1.0.0.tar.gz",
            package_name="nonexistent",
            hash_alg="sha256",
            expected_hash="abc123",
        )
        assert result is False

    def test_has_package_file_exists_no_hash(self, tmp_path: Path, create_package_file):
        """Test has_package returns True when file exists and no hash is provided."""

        # Create registry with a real hash function for this test
        def simple_hash(_: Path, __: str) -> str:
            return "not_called"

        registry = LocalRegistry(simple_hash, str(tmp_path))

        # Create a test package directory and file
        create_package_file("test-package", "test-package-1.0.0.tar.gz")

        result = registry.has_package(
            file_name="test-package-1.0.0.tar.gz",
            package_name="test-package",
            hash_alg="sha256",
            expected_hash=None,
        )
        assert result is True

    @pytest.mark.parametrize(
        ("package_name", "file_name", "content", "hash_alg"),
        [
            ("my-test-package", "my-test-package-2.3.4.whl", "wheel content", "sha256"),
            ("empty-package", "empty-package-0.0.1.tar.gz", "", "sha256"),
            ("hash-test", "hash-test-1.0.0.tar.gz", "md5 content", "md5"),
            ("ignore-hash-test", "ignore-hash-test-1.0.tar.gz", "some content", None),
        ],
    )
    def test_has_package_with_matching_hash(  # noqa: PLR0913
        self,
        local_registry: LocalRegistry,
        mock_file_hashing,
        create_package_file,
        package_name: str,
        file_name: str,
        content: str,
        hash_alg: str,
    ):
        """Test has_package returns True with various files and matching hashes."""
        test_file = create_package_file(package_name, file_name, content)
        expected_hash = mock_file_hashing(test_file, hash_alg)

        result = local_registry.has_package(
            file_name=file_name,
            package_name=package_name,
            hash_alg=hash_alg,
            expected_hash=expected_hash,
        )
        assert result is True

    def test_has_package_file_exists_with_mismatched_hash(
        self,
        local_registry: LocalRegistry,
        create_package_file,
    ):
        """Test has_package returns False when file exists with mismatched hash."""
        # Create a test package directory and file
        create_package_file("test-package", "test-package-1.0.0.tar.gz")

        result = local_registry.has_package(
            file_name="test-package-1.0.0.tar.gz",
            package_name="test-package",
            hash_alg="sha256",
            expected_hash="wrong_hash_value",
        )
        assert result is False


class TestGitLabRegistry:
    """Tests for the GitLabRegistry implementation."""

    @pytest.fixture
    def gitlab_registry(self):
        """Create a GitLabRegistry instance for testing."""
        return GitLabRegistry(
            registry_url="https://gitlab.example.com",
            project="test-project",
            token="test-token",  # noqa: S106
        )

    def test_gitlab_registry_initialization_with_token(self):
        """Test that GitLabRegistry initializes correctly with various parameters."""
        # With token
        registry = GitLabRegistry(
            registry_url="https://gitlab.example.com",
            project="test-project",
            token="test-token",  # noqa: S106
        )
        assert registry.registry_url == "https://gitlab.example.com"
        assert registry.project == "test-project"
        assert registry.token == "test-token"  # noqa: S105
        assert registry.headers == {"Authorization": "Bearer test-token"}

    def test_gitlab_registry_initialization_without_token(self):
        """Test that GitLabRegistry initializes correctly with various parameters."""
        # Without token
        registry = GitLabRegistry(
            registry_url="https://gitlab.example.com",
            project="test-project",
        )
        assert registry.token is None
        assert not registry.headers

    def test_gitlab_registry_name(self, gitlab_registry):
        """Test that GitLabRegistry returns correct name."""
        assert gitlab_registry.name == "GitLab (https://gitlab.example.com)"

    def test_parse_link_header(self):
        """Test that _parse_link_header correctly parses different link header formats."""
        # Test with a standard link header format
        link_header = '<https://gitlab.example.com/api/v4/projects/1/packages?page=2>; rel="next", <https://gitlab.example.com/api/v4/projects/1/packages?page=3>; rel="last"'
        expected = {
            "next": "https://gitlab.example.com/api/v4/projects/1/packages?page=2",
            "last": "https://gitlab.example.com/api/v4/projects/1/packages?page=3",
        }
        # pylint: disable=W0212
        assert GitLabRegistry._parse_link_header(link_header) == expected  # noqa: SLF001

        # Test with no link header
        # pylint: disable=W0212
        assert not GitLabRegistry._parse_link_header("")  # noqa: SLF001

        # Test with quoted relation values
        link_header = '<https://gitlab.example.com/api/v4/projects/1/packages?page=2>; rel="next first"'
        expected = {
            "next": "https://gitlab.example.com/api/v4/projects/1/packages?page=2",
            "first": "https://gitlab.example.com/api/v4/projects/1/packages?page=2",
        }
        assert GitLabRegistry._parse_link_header(link_header) == expected  # noqa: SLF001

    @pytest.fixture(autouse=True)
    def mock_urlopen(self, monkeypatch):  # noqa: C901
        """Create a mock for urllib.request.urlopen."""

        class MockResponse:
            def __init__(self, data, headers=None):
                self.data = data
                self.headers = headers or {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps(self.data).encode("utf-8")

        def mock_urlopen(request):
            # Mock different API responses based on the URL
            url = request.get_full_url()

            if url.split("?")[0].endswith("projects/test-project/packages"):
                data = [
                    {
                        "id": 1,
                        "name": "test-package",
                        "version": "1.0.0",
                    },
                    {
                        "id": 2,
                        "name": "other-package",
                        "version": "2.0.0",
                    },
                ]
                return MockResponse(data)

            if url.split("?")[0].endswith("packages/1/package_files"):
                # Return files for test-package
                data = [
                    {
                        "id": 101,
                        "file_name": "test-package-1.0.0.tar.gz",
                        "file_sha256": "correct_hash_value",
                    },
                    {
                        "id": 102,
                        "file_name": "test-package-1.0.0.whl",
                        "file_sha256": "another_hash_value",
                    },
                ]
                return MockResponse(data)

            if url.split("?")[0].endswith("packages/2/package_files"):
                # Return files for other-package
                data = [
                    {
                        "id": 201,
                        "file_name": "other-package-2.0.0.tar.gz",
                        "file_sha256": "different_hash_value",
                    },
                ]
                return MockResponse(data)

            # Pagination test case page1
            if "pagination-test" in url and "page=2" not in url:
                # First page
                data = [{"id": 1, "name": "page-1-item"}]
                headers = {
                    "Link": '<https://gitlab.example.com/api/v4/pagination-test?page=2&per_page=100>; rel="next"',
                }
                return MockResponse(data, headers)

            # Pagination test case page2
            if "pagination-test" in url and "page=2" in url:
                # Second page
                data = [{"id": 2, "name": "page-2-item"}]
                return MockResponse(data)

            msg = "Mock 404"
            raise urllib.error.URLError(msg)

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        return mock_urlopen

    def test_fetch_packages_list(self, gitlab_registry):
        """Test fetching the package list from the API."""
        # pylint: disable=W0212
        packages = gitlab_registry._fetch_packages_list()  # noqa: SLF001
        assert len(packages) == 2
        assert packages[0]["name"] == "test-package"
        assert packages[1]["name"] == "other-package"

        # Test that the result is cached
        assert gitlab_registry._packages_cache is packages  # noqa: SLF001

    def test_fetch_package_files(self, gitlab_registry):
        """Test fetching package files from the API."""
        # pylint: disable=W0212
        files = gitlab_registry._fetch_package_files(1)  # noqa: SLF001
        assert len(files) == 2
        assert files[0]["file_name"] == "test-package-1.0.0.tar.gz"
        assert files[1]["file_name"] == "test-package-1.0.0.whl"

        # Test that the result is cached
        assert gitlab_registry._package_files_cache[1] is files  # noqa: SLF001

    def test_fetch_paginated_api(self, gitlab_registry):
        """Test that pagination works correctly."""
        # pylint: disable=W0212
        result = gitlab_registry._fetch_paginated_api(  # noqa: SLF001
            "https://gitlab.example.com/api/v4/pagination-test",
        )
        assert len(result) == 2
        assert result[0]["name"] == "page-1-item"
        assert result[1]["name"] == "page-2-item"

    def test_has_package_with_matching_hash(self, gitlab_registry):
        """Test has_package returns True when package exists with matching hash."""
        result = gitlab_registry.has_package(
            file_name="test-package-1.0.0.tar.gz",
            package_name="test-package",
            hash_alg="sha256",
            expected_hash="correct_hash_value",
        )
        assert result is True

    def test_has_package_with_non_existent_file(self, gitlab_registry):
        """Test has_package returns True when package exists with matching hash."""
        result = gitlab_registry.has_package(
            file_name="test-package-99.0.0.tar.gz",
            package_name="test-package",
            hash_alg="sha256",
            expected_hash="correct_hash_value",
        )
        assert result is False

    def test_has_package_with_non_existent_package(self, gitlab_registry):
        """Test has_package returns False when package doesn't exist."""
        result = gitlab_registry.has_package(
            file_name="non-existent-1.0.0.tar.gz",
            package_name="non-existent",
            hash_alg="sha256",
            expected_hash="some_hash",
        )
        assert result is False

    def test_has_package_with_mismatched_hash(self, gitlab_registry):
        """Test has_package returns False when hash doesn't match."""
        result = gitlab_registry.has_package(
            file_name="test-package-1.0.0.tar.gz",
            package_name="test-package",
            hash_alg="sha256",
            expected_hash="wrong_hash_value",
        )
        assert result is False

    def test_has_package_with_unavailable_hash_alg(self, gitlab_registry):
        """Test has_package returns False when the registry lacks the hash algorithm."""
        result = gitlab_registry.has_package(
            file_name="test-package-1.0.0.tar.gz",
            package_name="test-package",
            hash_alg="sha512",
            expected_hash="some_hash_value",
        )
        assert result is False

    # pylint: disable=W0212
    def test_clear_cache(self, gitlab_registry):
        """Test that clear_cache properly resets the cache."""
        # Before filling, ensure cache is empty
        assert gitlab_registry._packages_cache is None  # noqa: SLF001
        assert gitlab_registry._package_files_cache == {}  # noqa: SLF001

        # Fill the cache and verify it's populated
        gitlab_registry._fetch_packages_list()  # noqa: SLF001
        gitlab_registry._fetch_package_files(1)  # noqa: SLF001

        assert gitlab_registry._packages_cache is not None  # noqa: SLF001
        assert 1 in gitlab_registry._package_files_cache  # noqa: SLF001

        # Clear the cache and verify it's reset
        gitlab_registry.clear_cache()

        assert gitlab_registry._packages_cache is None  # noqa: SLF001
        assert gitlab_registry._package_files_cache == {}  # noqa: SLF001

    def test_api_request_failure(self, gitlab_registry, monkeypatch):
        """Test handling of API request failures."""

        def failing_urlopen(_):
            msg = "Mock connection error"
            raise urllib.error.URLError(msg)

        monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)

        with pytest.raises(RuntimeError, match="Failed to read data"):
            gitlab_registry._fetch_packages_list()  # noqa: SLF001

    def test_file_url_in_next_link_raises_value_error(
        self,
        gitlab_registry,
        monkeypatch,
    ):
        """Test that file:// URLs in Link header 'next' field raise ValueError."""

        class MockResponse:
            def __init__(self, data, headers=None):
                self.data = data
                self.headers = headers or {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps(self.data).encode("utf-8")

        def mock_urlopen_with_file_url(request):
            # First request returns data with a malicious file:// URL in the next link
            data = [{"id": 1, "name": "test-item"}]
            headers = {
                "Link": '<file:///etc/passwd>; rel="next"',
            }
            return MockResponse(data, headers)

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_with_file_url)

        # pylint: disable=W0212
        with pytest.raises(ValueError, match="URL must start with 'http:' or 'https:'"):
            gitlab_registry._fetch_paginated_api(  # noqa: SLF001
                "https://gitlab.example.com/api/v4/test",
            )
