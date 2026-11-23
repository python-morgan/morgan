# Copyright 2024 Shamil Bikineyev
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import re
from collections import OrderedDict
from typing import TYPE_CHECKING, Iterable

import dateutil.parser  # type: ignore[import-untyped]
import packaging.version

if TYPE_CHECKING:
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version


def to_single_dash(filename):
    """https://packaging.python.org/en/latest/specifications/version-specifiers/#version-specifiers"""

    # selenium-2.0-dev-9429.tar.gz
    m = re.search(r"-[0-9].*-", filename)
    if m:
        s2 = filename[m.start() + 1 :]
        # 2.0-dev-9429.tar.gz
        s2 = s2.replace("-dev-", ".dev")
        # 2.0.dev9429.tar.gz
        s2 = s2.replace("-", ".")
        filename = filename[: m.start() + 1] + s2
    return filename
    # selenium-2.0.dev9429.tar.gz


def specifier_mentions_prerelease(specifier: SpecifierSet) -> bool:
    """Return True if the specifier names a prerelease version.

    For every operator except ===, the result is the same as packaging 24.2
    and newer give. A version after === is not counted because no final
    release matches a === pin on a prerelease, so Mirrorer selects that
    prerelease through the PEP 440 fallback.
    """
    # SpecifierSet.prereleases is not used because its result for < and >
    # differs between packaging 24.0 and 24.2
    for spec in specifier:
        if spec.operator in ("===", "!="):
            continue
        try:
            version = packaging.version.Version(spec.version)
        except packaging.version.InvalidVersion:
            # spec.version is not a version, for example the wildcard in ==1.*
            continue
        if version.is_prerelease:
            return True
    return False


def python_release_version(value: str) -> str:
    """Return the release part of an environment's Python version.

    "3.14.0rc1" becomes "3.14.0" and "3.13.0a0+local" becomes "3.13.0".
    Raises packaging.version.InvalidVersion if the value is not a valid
    version.
    """
    return packaging.version.Version(value).base_version


class Cache:
    def __init__(self, *, allow_prereleases: bool = False):
        self.allow_prereleases = allow_prereleases
        # str() of every processed requirement
        self.cache: set[str] = set()
        # maps a package name to the latest version that the last simple-case
        # requirement for that package selected
        self.selected_versions: dict[str, Version] = {}

    def check(self, req: Requirement) -> bool:
        if str(req) in self.cache:
            return True
        if not self.is_simple_case(req):
            return False
        version = self.selected_versions.get(req.name)
        if version is None:
            return False
        if version.is_prerelease and not self.allow_prereleases:
            # The recorded version was selected through the PEP 440 fallback
            # because no final release matched, but req selects a final
            # release if one satisfies it.
            return False
        # Mirrorer calls check() only for requirements that select one
        # version, which is the latest version that satisfies the requirement
        # and passes the other filters, such as the environment filter.
        # Let V be the recorded version and R the earlier requirement that
        # selected V.
        # R is a simple-case requirement without an upper bound, so every
        # version > V satisfies R.
        # No version > V passes the other filters, because R would have
        # selected it otherwise.
        # If V satisfies req, req therefore selects V too.
        # For example, if foo>=1.0 selected 2.5, then foo>=2.0 also selects
        # 2.5.
        return req.specifier.contains(version, prereleases=True)

    def add(self, req: Requirement, version: Version | None = None):
        self.cache.add(str(req))
        if version is not None and self.is_simple_case(req):
            self.selected_versions[req.name] = version

    def is_simple_case(self, req: Requirement) -> bool:
        if not req.marker and not req.extras:
            specifier = req.specifier
            if not specifier:
                return True
            # ruff: noqa: SLF001
            if all(spec.operator in (">", ">=") for spec in specifier._specs):
                # a specifier that names a prerelease can select a different
                # version than the bare requirement, so it is not a simple case
                return not specifier_mentions_prerelease(specifier)
        return False


def is_requirement_relevant(
    requirement: Requirement,
    envs: Iterable[dict],
    extras: set[str] | None = None,
) -> bool:
    """Determines if a requirement is relevant for any of the provided environments.

    Args:
        requirement: The requirement to evaluate.
        envs: The environments to check against.
        extras: Optional extras to consider during evaluation.

    Returns:
        True if the requirement has no marker or if its marker evaluates to
        True for at least one environment, False otherwise.
    """
    if not requirement.marker:
        return True

    # If no environments specified, assume relevant
    if not envs:
        return True

    for env in envs:
        # Create a copy of the environment to avoid modifying the original
        env_copy = env.copy()
        env_copy.setdefault("extra", "")
        if extras:
            env_copy["extra"] = ",".join(extras)

        if requirement.marker.evaluate(env_copy):
            return True

    return False


def filter_relevant_requirements(
    requirements: Iterable[Requirement],
    envs: Iterable[dict],
    extras: set[str] | None = None,
) -> set[Requirement]:
    """Filters a collection of requirements to only those relevant for the provided environments.

    Args:
        requirements: Requirements to filter.
        envs: The environments to check against.
        extras: Optional extras to consider during evaluation.

    Returns:
        Set of requirements relevant for at least one environment.
    """
    return {req for req in requirements if is_requirement_relevant(req, envs, extras)}


def touch_file(path: str, fileinfo: dict):
    """upload-time: 2025-05-28T18:46:29.349478Z"""
    time_str = fileinfo.get("upload-time")
    if not path or not time_str:
        return
    dt = dateutil.parser.parse(time_str)
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


class ListExtendingOrderedDict(OrderedDict):
    """An OrderedDict subclass that aggregates list values for duplicate keys.

    This class extends OrderedDict to provide special handling for list values.
    When a list value is assigned to an existing key, the new list is extended
    onto the existing list instead of replacing it.

    In the context of configparser, this allows for accumulating multiple values
    from different sections or repeated keys, such as in multiline requirements.

    Examples:
        >>> d = MultiOrderedDict()
        >>> d["key"] = [1, 2]
        >>> d["key"] = [3, 4]
        >>> d["key"]
        [1, 2, 3, 4]
        >>> d["other"] = "value"
        >>> d["other"] = "new_value"  # Non-list values behave normally
        >>> d["other"]
        'new_value'
    """

    def __setitem__(self, key, value):
        """Sets the value for the given key, extending lists if the key exists.

        Args:
            key: The dictionary key.
            value: The value to set. If this is a list and the key already exists,
                the list will be extended to the existing value instead of replacing it.
        """
        if isinstance(value, list) and key in self:
            self[key].extend(value)
        else:
            super().__setitem__(key, value)
