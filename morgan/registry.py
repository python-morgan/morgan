"""Package registry functionality for target package registries.

This module provides abstractions for working with different target package registries,
for mirroring.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Callable

from packaging.utils import canonicalize_name

logger = logging.getLogger(__name__)


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


class GitLabRegistry(Registry):
    """Registry implementation that checks a GitLab package registry."""

    def __init__(self, registry_url: str, project: str, token: str | None = None):
        self.registry_url = registry_url
        self.project = project
        self.token = token
        self.headers = {}
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"
        self._packages_cache: list | None = None
        self._package_files_cache: dict[int, list] = {}

    @property
    def name(self) -> str:
        return f"GitLab ({self.registry_url})"

    @staticmethod
    def _parse_link_header(link_header: str) -> dict[str, str]:
        """Parse the Link header according to RFC2068 section 19.6.2.4.

        Args:
            link_header: The Link header string to parse.

        Returns:
            A dictionary mapping relationship types to URLs.
        """
        if not link_header:
            return {}

        links: dict = {}

        # Process each link section (separated by commas)
        for section in link_header.split(","):
            section = section.strip()  # noqa: PLW2901
            if not section:
                continue

            GitLabRegistry._process_link_section(section, links)

        return links

    @staticmethod
    def _process_link_section(section: str, links: dict[str, str]) -> None:
        """Process a single link section and update the links dictionary."""
        parts = section.split(";")
        if not parts:
            logger.error(
                "Error: Invalid Link header section: %s. Ignoring section.",
                section,
            )
            return

        url_part = parts[0].strip()
        if not (url_part.startswith("<") and url_part.endswith(">")):
            logger.error(
                "Error: Unexpected Link header format: %s. Ignoring section.",
                url_part,
            )
            return

        url = url_part[1:-1]  # Remove <,> brackets

        # Process the parameters
        for param in parts[1:]:
            param = param.strip()  # noqa: PLW2901
            if "=" not in param:
                logger.error(
                    "Error: Unable to find '=' in link header: %s. Ignoring parameter.",
                    param,
                )
                continue

            name, value = param.split("=", 1)
            normalized_name = name.strip().lower()
            value = value.strip()

            if normalized_name == "rel":
                GitLabRegistry._add_rel_to_links(value, url, links)

    @staticmethod
    def _add_rel_to_links(value: str, url: str, links: dict[str, str]) -> None:
        """Add relation types to the links dictionary."""
        # If value is quoted, it may contain multiple space-separated relation types
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]  # Remove quotes
            # Handle multiple relation types in a quoted string
            for rel_type in value.split():
                links[rel_type.lower()] = url
        else:
            # Otherwise, it's a single relation type
            links[value.lower()] = url

    def _fetch_paginated_api(self, api_url: str) -> list:
        """Fetch paginated results from GitLab API.

        Args:
            api_url: The API URL to fetch from, including any query parameters

        Returns:
            List of all items from all pages

        Raises:
            RuntimeError: If the API request fails or returns unexpected format

        """
        results = []
        current_url: str | None = api_url + "?per_page=100"

        while current_url:
            # avoid potential CVE, see https://docs.astral.sh/ruff/rules/suspicious-url-open-usage/
            if not current_url.startswith(("http:", "https:")):
                msg = "URL must start with 'http:' or 'https:'"
                raise ValueError(msg)

            try:
                request = urllib.request.Request(current_url, headers=self.headers)  # noqa: S310
                with urllib.request.urlopen(request) as response:  # noqa: S310
                    page_data = json.load(response)

                    # Check response format
                    if not isinstance(page_data, list):
                        msg = f"Unexpected response format from {self.name}, expected a list: {page_data}"
                        raise TypeError(msg)

                    results.extend(page_data)

                    # Check for pagination in Link header
                    link_header = response.headers.get("Link")
                    if not link_header:
                        break

                    links = self._parse_link_header(link_header)
                    current_url = links.get("next")

            except (urllib.error.URLError, json.JSONDecodeError) as e:
                msg = f"Failed to read data from {current_url}"
                raise RuntimeError(msg) from e

        return results

    def _fetch_packages_list(self) -> list:
        """Fetch package files from the API and cache the result."""
        if self._packages_cache is not None:
            return self._packages_cache

        api_url = f"{self.registry_url}/api/v4/projects/{self.project}/packages"
        self._packages_cache = self._fetch_paginated_api(api_url)
        return self._packages_cache

    def _fetch_package_files(self, package_id: int) -> list:
        """Fetch package files from the API and cache the result."""
        if (
            package_id in self._package_files_cache
            and self._package_files_cache[package_id] is not None
        ):
            return self._package_files_cache[package_id]

        api_url = f"{self.registry_url}/api/v4/projects/{self.project}/packages/{package_id}/package_files"
        self._package_files_cache[package_id] = self._fetch_paginated_api(api_url)
        return self._package_files_cache[package_id]

    def has_package(
        self,
        file_name: str,
        package_name: str,
        hash_alg: str,
        expected_hash: str | None = None,
    ) -> bool:
        packages = self._fetch_packages_list()
        package_name = canonicalize_name(package_name)

        for package in packages:
            if canonicalize_name(package.get("name")) != package_name:
                continue

            for file_info in self._fetch_package_files(package.get("id")):
                # canonicalize_name() is only valid for package names; applying
                # it here would collapse distinct filenames
                if file_info.get("file_name") != file_name:
                    continue

                if file_info.get(f"file_{hash_alg}") == expected_hash:
                    return True
                # missing hash field (GitLab only stores md5/sha1/sha256) or a
                # genuine mismatch: treat the file as absent so the caller
                # re-downloads it, mirroring LocalRegistry semantics
                logger.warning(
                    "%s hash of %s in %s is missing or does not match the "
                    "expected value, treating file as missing",
                    hash_alg,
                    file_name,
                    self.name,
                )

        return False

    def clear_cache(self):
        """Clear the cached package files. Useful for testing or if registry content changes."""
        self._packages_cache = None
        self._package_files_cache = {}
