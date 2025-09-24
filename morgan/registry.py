"""Package registry functionality for target package registries.

This module provides abstractions for working with different target package registries,
for mirroring.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Callable


class Registry(ABC):
    """Abstract base class for package registries."""

    @abstractmethod
    def has_package(
        self,
        file_name: str,
        package_name: str,
        hash_alg: str,
        expected_hash: str | None = None,
    ) -> bool:
        """Check if a package file exists in the registry.

        Args:
            file_name: Name of the distribution file (e.g. "pkg-1.0.0.tar.gz")
            package_name: Name of the package the file belongs to
            hash_alg: Hash algorithm to use for verification
            expected_hash: Expected hash of the file; if None, existence alone
                is sufficient

        Returns:
            True if the file exists (and matches expected_hash when given),
            False otherwise

        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of this registry."""


class LocalRegistry(Registry):
    """Registry implementation that checks the local file system."""

    def __init__(self, hash_file_func: Callable, index_path: str):
        self.hash_file_func = hash_file_func
        self.index_path = index_path

    @property
    def name(self) -> str:
        return "Local"

    def has_package(
        self,
        file_name: str,
        package_name: str,
        hash_alg: str,
        expected_hash: str | None = None,
    ) -> bool:
        # if target already exists, verify its hash and only download if
        # there's a mismatch
        target = os.path.join(self.index_path, package_name, file_name)
        if not os.path.exists(target):
            return False

        # nothing else to do if there is no expected hash
        if not expected_hash:
            return True

        truehash = self.hash_file_func(target, hash_alg)
        return truehash == expected_hash
