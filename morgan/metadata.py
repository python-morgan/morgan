from __future__ import annotations

import email.parser
import gzip
import json
import re
import urllib.error
import urllib.request
from typing import IO, Any, Callable, Iterable

import tomli
from packaging.markers import Marker
from packaging.markers import Variable as MarkerVariable
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from morgan.utils import filter_relevant_requirements

METADATA_VERSION_11 = Version("1.1")
METADATA_VERSION_12 = Version("1.2")
METADATA_VERSION_21 = Version("2.1")


class MetadataParser:
    """
    MetadataParser is used to incrementally parse metadata sources from a Python
    package (which could be a wheel archive, a source archive, or just a
    directory). The major use of the class is to resolve dependencies of the
    package.

    The class supports several sources:
    - Metadata files compliant with Python's "core metadata specifications"
      (https://packaging.python.org/en/latest/specifications/core-metadata/),
      e.g. METADATA and PKG-INFO files.
    - The pyproject.toml file, as per PEP 621 (https://peps.python.org/pep-0621).
    - Setuptools-specific requires.txt and setup-requires.txt files
      (https://setuptools.pypa.io/en/latest/deprecated/python_eggs.html#requires-txt)

    Attributes:
    -----------
    source_path : str
        The name of the package archive that the object was
        created to parse.
    name : str
        The name of the package, in canonicalized form
    version : packaging.version.Version
        The version of the package
    python_requirement : packaging.specifiers.SpecifierSet
        A specification of the Python versions supported by the package.
    extras_provided : set[str]
        Extras provided by the package.
    core_dependencies: set[packaging.requirements.Requirement]
        Core dependencies of the package.
    optional_dependencies: dict[str, set[packaging.requirements.Requirement]]
        Optional dependencies of the package. A dictionary whose keys are
        either names of extras (from extras_provided) or environment marker
        constraints (e.g. :python_version<2.7).
    build_dependencies : set[packaging.requirements.Requirement]
        Dependencies required to build the package.
    """

    def __init__(self, source_path: str):
        """
        Object constructor.

        Parameters:
        -----------
        filename : str
            The name of the package archive that the object
            was created to parse.

        """

        self.source_path: str = source_path
        self.name: str | None = None
        self.version: Version | None = None
        self.python_requirement: SpecifierSet | None = None
        self.extras_provided: set[str] = set()
        self.core_dependencies: set[Requirement] = set()
        self.optional_dependencies: dict[str, set[Requirement]] = {}
        self.build_dependencies: set[Requirement] = set()

    # ruff: noqa: C901
    def parse(
        self,
        opener: Callable[[str], IO[bytes] | None],
        filename: str,
    ) -> None:
        """
        Parses a file, gathering whatever metadata can be gathered from it. Any
        file can be provided to the method, irrelevant files are simply ignored.

        Parameters
        ----------
        opener : Callable[[str], IO[bytes] | None]
            A function that can be used to open the file. The function takes one
            parameter, which is the file name, and returns a file object opened
            in binary mode.
        filename : str
            The file name that will be provided to the opener function. This can
            either be relative or absolute, so long as the opener function can
            open it. The name is important, as the method uses it to determine
            the kind of file it is.
        """

        parse_func: Callable[[IO[bytes]], Any] | None = None
        main_metadata_file = False

        if re.search(r"\.whl$", self.source_path):
            if re.fullmatch(r"[^/]+\.dist-info/METADATA", filename):
                parse_func = self._parse_metadata_file
                main_metadata_file = True
        elif re.search(r"\.zip$", self.source_path):
            if re.fullmatch(r"([^/]+/)?PKG-INFO", filename):
                parse_func = self._parse_metadata_file
                main_metadata_file = True
        elif re.search(r"\.tar\.gz$", self.source_path):
            if re.fullmatch(r"[^/]+/PKG-INFO", filename):
                parse_func = self._parse_metadata_file
                main_metadata_file = True
            elif re.fullmatch(
                r"[^/]+(/[^/]+)?\.egg-info/(setup_)?requires.txt",
                filename,
            ):
                parse_func = self._parse_requirestxt
            elif re.fullmatch(r"[^/]+/pyproject.toml", filename):
                parse_func = self._parse_pyproject

        if not parse_func:
            return

        # Our file_object can be either None or IO[bytes] because
        # TarFile.extractfile can return None
        file_object = opener(filename)
        if file_object is None:
            return

        with file_object as fp:
            if main_metadata_file:
                self._metadata_file = fp.read()
                fp.seek(0)
            parse_func(fp)

    def seen_metadata_file(self) -> bool:
        """
        Returns a boolean value if the archive's main METADATA file has already
        been read.
        """
        return hasattr(self, "_metadata_file")

    def write_metadata_file(self, target: str):
        """
        Writes the archive's main METADATA file to the target filepath, provided
        such a file was already read. The contents of the target file will be
        clobbered, if already existing. If the main metadata file has not been
        read yet, an exception will be raised.
        """
        if not hasattr(self, "_metadata_file"):
            # ruff: noqa: TRY002
            msg = "Main METADATA file has not been read yet"
            raise Exception(msg)

        with open(target, "wb") as out:
            out.write(self._metadata_file)

    def dependencies(self, extras: set[str], envs: Iterable[dict]) -> set[Requirement]:
        """
        Resolves the dependencies of the package, returning a set of
        requirements. Only requirements that are relevant to the provided extras
        and environments are returned.

        Parameters
        ----------
        extras : set[str] = set()
            A set of extras that the package was required with. For example, if
            the instance of this class is used to parse the metadata of the
            package "pymongo", and the requirement string for that package was
            "pymongo[snappy,zstd]", then the set of extras will be (snappy, zstd).
        envs: Iterable[dict] = []
            The list of environments for which Morgan is downloading package
            distributions. These are simple dictionaries whose keys match those
            defined by the "Environment Markers" section of PEP 508.

        Returns
        -------
        A set of packaging.requirements.Requirement objects.
        """

        deps = set()
        deps |= self.core_dependencies
        deps |= self.build_dependencies

        for extra in self.optional_dependencies:
            if ":" in extra:
                # this dependency includes a set of environment marker
                # specifications
                orig = extra
                # ruff: noqa: PLW2901, FIX002, TD003
                # TODO(grische): rewrite to avoid overwriting extra variable from the loop
                (extra, spec) = extra.split(":")
                if extra and extra not in extras:
                    continue
                marker = Marker(spec)
                for env in envs:
                    if marker.evaluate(env):
                        deps |= self.optional_dependencies[orig]
                        break
            elif extra in extras:
                deps |= self.optional_dependencies[extra]

        return filter_relevant_requirements(deps, envs, extras)

    def _add_core_requirements(self, reqs):
        self.core_dependencies |= {Requirement(dep) for dep in reqs}

    def _add_optional_requirements(self, extra, reqs):
        if extra not in self.optional_dependencies:
            self.optional_dependencies[extra] = set()
        self.optional_dependencies[extra] |= {Requirement(dep) for dep in reqs}

    def _parse_pyproject(self, fp: IO[bytes]):
        data = tomli.load(fp)
        project = data.get("project")

        if project is not None:
            (name, version) = (project.get("name"), project.get("version"))

            if name is not None:
                self.name = canonicalize_name(name)

            if version is not None:
                self.version = Version(version)

            if "requires-python" in project:
                self.python_requirement = SpecifierSet(project["requires-python"])

            if "dependencies" in project:
                self._add_core_requirements(project["dependencies"])

            if "optional-dependencies" in project:
                for extra in project["optional-dependencies"]:
                    self._add_optional_requirements(
                        extra,
                        project["optional-dependencies"][extra],
                    )

        build_system = data.get("build-system")
        if build_system is not None and "requires" in build_system:
            self.build_dependencies |= {
                Requirement(req) for req in build_system["requires"]
            }

    def _parse_metadata_file(self, fp: IO[bytes]):
        data = email.parser.BytesParser().parse(fp, headersonly=True)

        (name, version, metadata_version_str) = (
            data.get("Name"),
            data.get("Version"),
            data.get("Metadata-Version"),
        )
        if metadata_version_str is None:
            return

        metadata_version = Version(metadata_version_str)

        if name is not None:
            self.name = canonicalize_name(name)
        if version is not None:
            self.version = Version(version)

        if metadata_version >= METADATA_VERSION_21:
            self._parse_metadata_21(data)
        if metadata_version >= METADATA_VERSION_12:
            self._parse_metadata_12(data)
        elif metadata_version == METADATA_VERSION_11:
            self._parse_metadata_11(data)

    def _parse_metadata_21(self, data):
        provides_extra = data.get_all("Provides-Extra")
        if provides_extra is not None:
            self.extras_provided |= set(provides_extra)

    def _parse_metadata_12(self, data):
        requires_python = data.get("Requires-Python")
        if requires_python is not None:
            self.python_requirement = SpecifierSet(requires_python)

        requires_dist = data.get_all("Requires-Dist")
        if requires_dist is not None:
            for requirement_str in requires_dist:
                self._parse_requires_dist(requirement_str)

    def _parse_requires_dist(self, requirement_str: str):
        req = Requirement(requirement_str)
        extra = None
        if req.marker is not None:
            # ruff: noqa: SLF001
            for marker in req.marker._markers:
                if isinstance(marker[0], MarkerVariable) and marker[0].value == "extra":
                    extra = marker[2].value
                    break

        if not extra:
            self.core_dependencies.add(req)
            return

        if extra not in self.optional_dependencies:
            self.optional_dependencies[extra] = set()
        self.optional_dependencies[extra].add(req)

    def _parse_metadata_11(self, data):
        requires = data.get_all("Requires")
        if requires is not None:
            for requirement_str in requires:
                self.core_dependencies.add(Requirement(requirement_str))

    def _parse_requirestxt(self, fp: IO[bytes]):
        section = None
        content: list[str] = []
        for line_bytes in fp:
            line = line_bytes.strip().decode("UTF-8")
            if line.startswith("["):
                if line.endswith("]"):
                    if section or content:
                        if section:
                            self._add_optional_requirements(section, content)
                        elif fp.name.endswith("setup_requires.txt"):
                            self._add_build_requirements(content)
                        else:
                            self._add_core_requirements(content)
                    section = line[1:-1]
                    content = []
                else:
                    msg = "Invalid section heading"
                    raise ValueError(msg, line)
            elif line:
                content.append(line)

        if section:
            self._add_optional_requirements(section, content)
        elif fp.name.endswith("setup_requires.txt"):
            self._add_build_requirements(content)
        else:
            self._add_core_requirements(content)

    def _add_build_requirements(self, reqs):
        msg = "Setuptools build requirements not supported"
        raise NotImplementedError(msg)


class PyPIMetadataParser(MetadataParser):
    """Metadata Parser for PyPI's JSON API.

    This class extends MetadataParser to parse metadata from PyPI's JSON API
    without needing an opener/filename for local files.
    """

    def __init__(self, package_name: str, version_str: str):
        super().__init__(f"pypi:{package_name}-{version_str}")
        self.package_name = package_name
        self.version_str = version_str

    def parse(
        self,
        opener: Callable[[str], IO[bytes] | None],
        filename: str,
    ) -> None:
        msg = "Use parse_pypi() instead of parse() for PyPIMetadataParser"
        raise NotImplementedError(msg)

    def parse_pypi(self) -> MetadataParser:
        """Extract package metadata from PyPI's JSON API.

        Returns:
            A MetadataParser object populated with metadata from PyPI.

        Raises:
            urllib.error.HTTPError: If the API request fails.
            ValueError: If the API response is invalid or cannot be parsed.
        """

        url = f"https://pypi.org/pypi/{self.package_name}/{self.version_str}/json"

        request = urllib.request.Request(  # noqa: S310
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )

        with urllib.request.urlopen(request) as response:  # noqa: S310
            # Check if response is gzip-encoded
            if response.headers.get("Content-Encoding") == "gzip":
                with gzip.GzipFile(fileobj=response) as gzip_response:
                    data = json.load(gzip_response)
            else:
                data = json.load(response)

        received_version = data["info"].get("version")
        # PyPI returns the version as uploaded (e.g. "0.6c1"), which may differ
        # from the PEP 440 normalized form in version_str (e.g. "0.6rc1")
        if received_version is None or Version(received_version) != Version(
            self.version_str,
        ):
            msg = f"Version mismatch: requested {self.version_str}, got {received_version}"
            raise ValueError(msg)

        # Create and populate the metadata parser
        self.name = canonicalize_name(data["info"]["name"])
        self.version = Version(received_version)

        # Extract Python requirement
        requires_python = data["info"].get("requires_python")
        if requires_python:
            self.python_requirement = SpecifierSet(requires_python)

        provides_extra = data["info"].get("provides_extra")
        if provides_extra:
            self.extras_provided |= set(provides_extra)

        # Parse dependencies
        requires_dist = data["info"].get("requires_dist")
        if requires_dist is not None:
            for requirement_str in requires_dist:
                self._parse_requires_dist(requirement_str)

        return self
