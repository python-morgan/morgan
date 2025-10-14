import hashlib
from pathlib import Path
from typing import Callable

import pytest

from morgan.registry import LocalRegistry, Registry


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
