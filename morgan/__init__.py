from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import os.path
import re
import tarfile
import traceback
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from typing import IO, Callable, Iterable

import packaging.requirements
import packaging.specifiers
import packaging.tags
import packaging.utils
import packaging.version

from morgan import configurator, metadata, server
from morgan.__about__ import __version__
from morgan.utils import (
    Cache,
    ListExtendingOrderedDict,
    is_requirement_relevant,
    to_single_dash,
    touch_file,
)

PYPI_ADDRESS = "https://pypi.org/simple/"
PREFERRED_HASH_ALG = "sha256"


class Mirrorer:
    """
    Mirrorer is a class that implements the mirroring capabilities of Morgan.
    A class is used to maintain state, as the mirrorer needs to keep track of
    packages it already processed in the (very common) case that it encounters
    them again as dependencies.
    """

    def __init__(self, args: argparse.Namespace):
        """
        The constructor only needs to path to the package index.
        """

        # load the configuration from the index_path, and parse the environments
        # into representations that are easier for the mirrorer to work with
        self.index_path = args.index_path
        self.index_url = args.index_url
        self.mirror_all_wheels: bool = args.mirror_all_wheels
        self.mirror_all_versions: bool = args.mirror_all_versions
        self.package_type_regex: str = args.package_type_regex
        self.config = configparser.ConfigParser(
            strict=False,
            dict_type=ListExtendingOrderedDict,
        )
        self.config.read(args.config)
        self.envs = {}
        self._supported_pyversions = []
        self._supported_platforms = []
        for key in self.config:
            m = re.fullmatch(r"env\.(.+)", key)
            if m:
                env = self.config[key]
                env["platform_release"] = ""
                env["platform_version"] = ""
                env["implementation_version"] = ""
                env["extra"] = ""
                self.envs[m.group(1)] = dict(env)
                if "python_full_version" in env:
                    self._supported_pyversions.append(env["python_full_version"])
                else:
                    self._supported_pyversions.append(env["python_version"])
                if "platform_tag" in env:
                    self._supported_platforms.append(re.compile(env["platform_tag"]))
                else:
                    self._supported_platforms.append(
                        re.compile(
                            r".*"
                            + env["sys_platform"]
                            + r".*"
                            + env["platform_machine"],
                        ),
                    )

        self._processed_pkgs = Cache()

    def mirror(self, requirement_string: str):
        """
        Mirror a package according to a PEP 508-compliant requirement string.
        """

        requirement = parse_requirement(requirement_string)

        try:
            deps = self._mirror(requirement)
        except urllib.error.HTTPError as err:
            # fail2ban
            # urllib.error.HTTPError: HTTP Error 404: Not Found
            print(f"\tError: {err}")
            deps = None

        if deps is None:
            return

        while len(deps) > 0:
            next_deps = {}
            for dep in deps:
                more_deps = self._mirror(
                    deps[dep]["requirement"],
                    required_by=deps[dep]["required_by"],
                )
                if more_deps:
                    next_deps.update(more_deps)
            deps = next_deps.copy()

    def copy_server(self):
        """
        Copy the server script to the package index. This method will first
        attempt to find the server file directly, and if that fails, it will
        use the inspect module to get the source code.
        """

        print("Copying server script")
        thispath = os.path.realpath(__file__)
        serverpath = os.path.join(os.path.dirname(thispath), "server.py")
        outpath = os.path.join(self.index_path, "server.py")
        if os.path.exists(serverpath):
            with open(serverpath, "rb") as inp, open(outpath, "wb") as out:
                out.write(inp.read())
        else:
            import inspect  # noqa: PLC0415

            with open(outpath, "w") as out:
                out.write(inspect.getsource(server))

    def _mirror(  # noqa: C901, PLR0912
        self,
        requirement: packaging.requirements.Requirement,
        required_by: packaging.requirements.Requirement | None = None,
    ) -> dict | None:
        if self._processed_pkgs.check(requirement):
            return None

        # Display the cause of 'Skipping...'
        extras = None
        if required_by:
            extras = required_by.extras
            print(f"[{required_by}]: {requirement}")
        else:
            print(f"{requirement}")

        # Check if requirement is relevant for any environment
        if not is_requirement_relevant(requirement, self.envs.values(), extras=extras):
            print("\tSkipping, not relevant for any environment")
            self._processed_pkgs.add(requirement)  # Mark as processed
            return None

        data: dict | None = None

        # get information about this package from the Simple API in JSON
        # format as per PEP 691
        request = urllib.request.Request(  # noqa: S310
            f"{self.index_url}{requirement.name}/",
            headers={
                "Accept": "application/vnd.pypi.simple.v1+json",
            },
        )

        response_url = ""
        with urllib.request.urlopen(request) as response:  # noqa: S310
            data = json.load(response)
            response_url = str(response.url)
            if not data:
                msg = f"Failed loading metadata: {response}"
                raise RuntimeError(msg)

        # check metadata version ~1.0
        v_str = data["meta"]["api-version"]
        if not v_str:
            v_str = "1.0"
        v_int = [int(i) for i in v_str.split(".")[:2]]
        if v_int[0] != 1:
            msg = f"Unsupported metadata version {v_str}, only support 1.x"
            raise Exception(msg)  # noqa: TRY002

        files = data["files"]
        if files is None or not isinstance(files, list):
            msg = "Expected response to contain a list of 'files'"
            raise Exception(msg)  # noqa: TRY002

        # filter and enrich files
        files = self._filter_files(requirement, required_by, files)
        if files is None:
            if required_by is None:
                msg = "No files match requirement"
                raise Exception(msg)  # noqa: TRY002
            # this is a dependency, assume the dependency is not relevant
            # for any of our environments and don't return an error
            return None

        if len(files) == 0:
            msg = f"No files match requirement {requirement}"
            raise Exception(msg)  # noqa: TRY002

        # download all files
        depdict = {}
        for file in files:
            file["url"] = urllib.parse.urljoin(response_url, file["url"])
            try:
                file_deps = self._process_file(requirement, file)
                if file_deps:
                    depdict.update(file_deps)
            except Exception:  # noqa: BLE001
                print(
                    "\tFailed processing file {}, skipping it".format(file["filename"]),
                )
                traceback.print_exc()
                continue

        self._processed_pkgs.add(requirement)

        return depdict

    def _filter_files(
        self,
        requirement: packaging.requirements.Requirement,
        required_by: packaging.requirements.Requirement | None,
        files: Iterable[dict],
    ) -> list[dict] | None:
        files = self._parse_and_filter_files_by_extension(files)
        files = self._parse_version_and_tags_in_files(files)
        files = self._filter_files_for_valid_versions(files)

        if not files:
            print(f"Skipping {requirement}, no valid version matches requirement")
            return None

        self._sort_files_by_version(files)
        files = self._filter_by_requirement(files, requirement)

        if not files:
            print(f"Skipping {requirement}, no version matches requirement")
            return None

        files = self._filter_files_by_environment(files)

        if not files:
            print(f"Skipping {requirement}, no file matches environments")
            return None

        return self._filter_files_by_version_strategy(files, required_by)

    def _parse_and_filter_files_by_extension(self, files: Iterable[dict]) -> list[dict]:

        # remove files with unsupported extensions
        pattern: str = rf"\.{self.package_type_regex}$"
        return list(filter(lambda file: re.search(pattern, file["filename"]), files))

    def _parse_version_and_tags_in_files(self, files: list[dict]) -> list[dict]:
        # parse versions and platform tags for each file
        for file in files:
            try:
                if re.search(r"\.whl$", file["filename"]):
                    _, file["version"], ___, file["tags"] = (
                        packaging.utils.parse_wheel_filename(file["filename"])
                    )
                    file["is_wheel"] = True
                elif re.search(r"\.(tar\.gz|zip)$", file["filename"]):
                    _, file["version"] = packaging.utils.parse_sdist_filename(
                        # fix: selenium-2.0-dev-9429.tar.gz -> 9429
                        to_single_dash(file["filename"]),
                    )
                    file["is_wheel"] = False
                    file["tags"] = None
            except (  # noqa: PERF203
                packaging.version.InvalidVersion,
                packaging.utils.InvalidSdistFilename,
                packaging.utils.InvalidWheelFilename,
            ):
                # old versions
                # expandvars-0.6.0-macosx-10.15-x86_64.tar.gz

                # ignore files with invalid version, PyPI no longer allows
                # packages with special versioning schemes, and we assume we
                # can ignore such files
                continue
            except Exception:  # noqa: BLE001
                print("\tSkipping file {}, exception caught".format(file["filename"]))
                traceback.print_exc()
                continue
        return files

    def _filter_files_for_valid_versions(self, files: list[dict]) -> list[dict]:
        # make sure all files have a version field and ignore yanked files
        return list(
            filter(
                lambda file: "version" in file and not file.get("yanked", False),
                files,
            ),
        )

    def _sort_files_by_version(self, files: list[dict]) -> None:
        # sort the files by version
        files.sort(key=lambda file: file["version"], reverse=True)

    def _filter_by_requirement(
        self,
        files: list[dict],
        requirement: packaging.requirements.Requirement,
    ) -> list[dict]:
        if requirement.specifier is None:
            return files

        # keep only files of the version that satisfies the requirement
        return list(
            filter(
                lambda file: requirement.specifier.contains(file["version"]),
                files,
            ),
        )

    def _filter_files_by_environment(self, files: list[dict]) -> list[dict]:
        """Filter files to select the best matching wheels for each supported environment.

        If mirror_all_wheels is enabled, returns all compatible wheels.
        Otherwise, selects the best wheel for each Python version/platform combination.
        """
        if self.mirror_all_wheels:
            return list(
                filter(
                    lambda file: self._matches_environments(
                        file,
                        self._supported_pyversions,
                        self._supported_platforms,
                    ),
                    files,
                ),
            )

        files_by_version = self._group_files_by_version(files)

        selected_files = []
        for version_files in files_by_version.values():
            best_files = self._select_best_files_for_version(version_files)
            selected_files.extend(best_files)

        self._sort_files_by_version(selected_files)
        return selected_files

    def _group_files_by_version(self, files: list[dict]) -> dict:
        """Group files by their version number."""
        files_by_version = defaultdict(list)
        for file in files:
            files_by_version[file["version"]].append(file)
        return files_by_version

    def _select_best_files_for_version(self, version_files: list[dict]) -> list[dict]:
        """Select the best matching files for a single version across all environments."""

        # Separate wheel files from sdists
        wheels = [f for f in version_files if f.get("is_wheel", False)]
        non_wheels = [f for f in version_files if not f.get("is_wheel", False)]

        # Sort wheels by their calculated scores (best match for our environments are first)
        wheels = sorted(wheels, key=self._calculate_scores_for_wheel, reverse=True)

        selected_files = []
        for python_version in self._supported_pyversions:
            for platform_pattern in self._supported_platforms:
                best_non_wheel = self._find_first_matching_file_for_env(
                    non_wheels,
                    python_version,
                    platform_pattern,
                )
                if best_non_wheel and best_non_wheel not in selected_files:
                    selected_files.append(best_non_wheel)

                best_wheel = self._find_first_matching_file_for_env(
                    wheels,
                    python_version,
                    platform_pattern,
                )
                if best_wheel and best_wheel not in selected_files:
                    selected_files.append(best_wheel)

        return selected_files

    def _find_first_matching_file_for_env(
        self,
        files: list[dict],
        python_version: str,
        platform_pattern,
    ) -> dict | None:
        """Find the first file that matches the given environment constraints."""

        for file in files:
            if self._matches_environments(file, [python_version], [platform_pattern]):
                return file

        return None

    def _filter_files_by_version_strategy(
        self,
        files: list[dict],
        required_by: packaging.requirements.Requirement | None,
    ) -> list[dict]:
        # Don't filter files for versions if we need all versions and this packages is a top-level requirement
        if self.mirror_all_versions and required_by is None:
            return files

        # Only keep files from the latest version in case the package is a dependency of another
        latest_version = files[0]["version"]
        return list(filter(lambda file: file["version"] == latest_version, files))

    @staticmethod
    def _matches_environments(  # noqa: C901, PLR0912
        fileinfo: dict,
        supported_pyversions: list,
        supported_platforms: list,
    ) -> bool:
        req = fileinfo.get("requires-python")
        if req:
            # The Python versions in all of our environments must be supported
            # by this file in order to match.
            # Some packages specify their required Python versions with a simple
            # number (e.g. '3') instead of an actual specifier (e.g. '>=3'),
            # which causes the packaging library to raise an expection. Let's
            # change such cases to a proper specifier.
            if req.isdigit():
                req = f"=={req}"
            # packaging.specifiers.SpecifierSet(req): Invalid specifier
            # gssapi: Invalid specifier: '>=3.6.*'
            # pyzmq: Invalid specifier: '!=3.0*'
            req = fileinfo["requires-python"] = re.sub(r"([0-9])\.?\*", r"\1", req)
            try:
                spec_set = packaging.specifiers.SpecifierSet(req)
                for supported_python in supported_pyversions:
                    if not spec_set.contains(supported_python):
                        # file does not support the Python version of one of our
                        # environments, reject it
                        return False
            except packaging.specifiers.InvalidSpecifier as e:
                print(f"\tIgnoring {fileinfo['filename']}: {e}")
                return False

        if fileinfo.get("tags"):
            # At least one of the tags must match ALL of our environments
            for tag in fileinfo["tags"]:
                (intrp_name, intrp_ver) = parse_interpreter(tag.interpreter)
                if intrp_name not in ("py", "cp"):
                    continue

                if not intrp_ver:
                    msg = f"Unexpected interpreter tag {tag.interpreter} in file {fileinfo['filename']}"
                    raise ValueError(msg)

                intrp_set = packaging.specifiers.SpecifierSet(">=" + intrp_ver)
                # As an example, cp38 seems to indicate CPython 3.8+, so we
                # check if the version matches any of the supported Pythons, and
                # only skip it if it does not match any.
                intrp_ver_matched = any(
                    (
                        intrp_set.contains(supported_python)
                        for supported_python in supported_pyversions
                    ),
                )

                if intrp_ver and intrp_ver != "3" and not intrp_ver_matched:
                    continue

                if tag.platform == "any":
                    return True
                for platformre in supported_platforms:
                    if platformre.fullmatch(tag.platform):
                        # tag matched, accept this file
                        return True

            # none of the tags matched, reject this file
            return False

        return True

    def _calculate_scores_for_wheel(self, file: dict) -> tuple[int, int]:
        """Calculate scoring tuple for a file to determine best wheel selection.

        Assigns high scores to non-wheel files (sdists) to ensure they're always included.
        For wheel files, calculates scores based on Python version and manylinux tag
        to enable selection of the most compatible wheel for each platform.

        Args:
            file: Dictionary containing package file information with keys 'is_wheel' and 'tags'

        Returns:
            A tuple of (python_score, platform_score) where:
                - python_score (int): Python version as an integer (e.g., 3.11 = 311).
                Non-wheels get 1e10 to ensure they're always kept.
                - platform_score (int): Numeric representation of manylinux tag.
                Modern formats use glibc version (e.g., manylinux_2_28 = 228).
                Deprecated formats have fixed scores: manylinux2014 = 90,
                manylinux2010 = 80, manylinux1 = 70. Non-wheels get a very high value.

        Note:
            The scoring algorithm prioritizes:
            1. Higher Python version (e.g., cp311 over cp39)
            2. Newer platform tags with higher scores (e.g., manylinux_2_28 [228] over
            manylinux2014 [90] over manylinux1 [70])
            Only CPython (cp) and generic Python (py) interpreters are considered.

        """
        if file.get("is_wheel", False) is False:
            return (int(1e10), int(1e10))

        best_score: tuple[int, int] = (0, 0)

        for tag in file.get("tags", []):
            # Calculate Python score
            interpreter_name, py_version = parse_interpreter(tag.interpreter)
            if interpreter_name not in ("cp", "py") or not py_version:
                continue

            version_obj = packaging.version.Version(py_version)
            py_score = version_obj.major * 100 + version_obj.minor

            # Calculate platform score
            platform = tag.platform
            platform_score = 0
            match = re.search(r"[a-z]+_(\d+)_(\d+)", platform)
            if match:
                # this provides a minimum platform_score of 100 (glibc 1.0 = 100+0 = 100)
                platform_score = int(match.group(1)) * 100 + int(match.group(2))
            elif "manylinux2014" in platform:
                platform_score = 90
            elif "manylinux2010" in platform:
                platform_score = 80
            elif "manylinux1" in platform:
                platform_score = 70

            # Keep the lexicographically maximum tuple (highest py_score, then highest platform_score)
            current_score = (py_score, platform_score)
            best_score = max(best_score, current_score)

        return best_score

    def _process_file(
        self,
        requirement: packaging.requirements.Requirement,
        fileinfo: dict,
    ) -> dict[str, dict[str, packaging.requirements.Requirement]] | None:
        filepath = os.path.join(self.index_path, requirement.name, fileinfo["filename"])
        hashalg = (
            PREFERRED_HASH_ALG
            if PREFERRED_HASH_ALG in fileinfo["hashes"]
            else fileinfo["hashes"].keys()[0]
        )

        self._download_file(fileinfo, filepath, hashalg)

        md = self._extract_metadata(filepath)

        deps = md.dependencies(requirement.extras, self.envs.values())
        if deps is None:
            return None

        depdict = {}
        for dep in deps:
            dep.name = packaging.utils.canonicalize_name(dep.name)
            # keep the index of the dictionary for the full requirement string to pull in potentially
            # duplicate requirements like "mylibrary<2,>=1" and "mylibrary>=2,<3" that may come from different
            # top-level requirements
            dep_index = str(dep)
            depdict[dep_index] = {
                "requirement": dep,
                "required_by": requirement,
            }
        return depdict

    def _download_file(
        self,
        fileinfo: dict,
        target: str,
        hashalg: str,
    ) -> bool:
        exphash = fileinfo["hashes"][hashalg]

        os.makedirs(os.path.dirname(target), exist_ok=True)

        # if target already exists, verify its hash and only download if
        # there's a mismatch
        if os.path.exists(target):
            truehash = self._hash_file(target, hashalg)
            if truehash == exphash:
                touch_file(target, fileinfo)
                return True

        print("\t{}...".format(fileinfo["url"]), end=" ")
        with urllib.request.urlopen(fileinfo["url"]) as inp, open(target, "wb") as out:  # noqa: S310
            out.write(inp.read())
        print("done")

        truehash = self._hash_file(target, hashalg)
        if truehash != exphash:
            os.remove(target)
            msg = "Digest mismatch for {}. Deleting file {}.".format(
                fileinfo["filename"],
                target,
            )
            raise ValueError(msg)

        touch_file(target, fileinfo)
        return True

    def _hash_file(self, filepath: str, hashalg: str) -> str:
        contents = None
        with open(filepath, "rb") as fh:
            # verify downloaded file has same hash
            contents = fh.read()

        truehash = hashlib.new(hashalg)
        truehash.update(contents)

        with open(f"{filepath}.hash", "w") as out:
            out.write(f"{hashalg}={truehash.hexdigest()}")

        return truehash.hexdigest()

    def _extract_metadata(
        self,
        filepath: str,
    ) -> metadata.MetadataParser:
        md = metadata.MetadataParser(filepath)

        archive: tarfile.TarFile | zipfile.ZipFile | None = None
        members = None
        opener: Callable[[str], IO[bytes] | None]

        if re.search(r"\.(whl|zip)$", filepath):
            archive = zipfile.ZipFile(filepath)
            members = [member.filename for member in archive.infolist()]
            opener = archive.open
        elif re.search(r"\.tar.gz$", filepath):
            archive = tarfile.open(filepath)  # noqa: SIM115
            members = [member.name for member in archive.getmembers()]
            opener = archive.extractfile
        else:
            msg = f"Unexpected distribution file {filepath}"
            raise Exception(msg)  # noqa: TRY002

        for member in members:
            try:
                md.parse(opener, member)
            except Exception as e:  # noqa: BLE001, PERF203
                print(f"Failed parsing member {member} of {filepath}: {e}")

        if md.seen_metadata_file():
            md.write_metadata_file(f"{filepath}.metadata")

        archive.close()

        return md


def parse_interpreter(inp: str) -> tuple[str, str | None]:
    """
    Parse interpreter tags in the name of a binary wheel file. Returns a tuple
    of interpreter name and optional version, which will either be <major> or
    <major>.<minor>.
    """

    m = re.fullmatch(r"^([^\d]+)(?:(\d)(?:[._])?(\d+)?)$", inp)
    if m is None:
        return (inp, None)

    intr = m.group(1)
    version = None
    if m.lastindex and m.lastindex > 1:
        version = m.group(2)
        if m.lastindex > 2:  # noqa: PLR2004
            version = f"{version}.{m.group(3)}"

    return (intr, version)


def parse_requirement(req_string: str) -> packaging.requirements.Requirement:
    """
    Parse a requirement string into a packaging.requirements.Requirement object.
    Also canonicalizes (or "normalizes") the name of the package.
    """

    req = packaging.requirements.Requirement(req_string)
    req.name = packaging.utils.canonicalize_name(req.name)
    return req


def mirror(args: argparse.Namespace):
    """
    Run the mirror on the package index in the provided path, and based on the
    morgan.ini configuration file in the index. Copies the server script to the
    index at the end of the process. This function can safely be called multiple
    times on the same index path, files are only downloaded if necessary.
    """

    m = Mirrorer(args)
    for package in m.config["requirements"]:
        reqs = m.config["requirements"][package].splitlines()
        if not reqs:
            # empty requirements
            # morgan =
            m.mirror(f"{package}")
        else:
            # multiline requirements
            # urllib3 =
            #   <1.27
            #   >=2
            #   [brotli]  # noqa: ERA001
            for req in reqs:
                m.mirror(f"{package}{req.strip()}")

    if not args.skip_server_copy:
        m.copy_server()


def main():  # noqa: C901
    """
    Executes the command line interface of Morgan. Use -h for a full list of
    flags, options and arguments.
    """

    def my_url(arg):
        # url -> url/ without params
        # https://stackoverflow.com/a/73719022
        arg = arg.rstrip("/")
        url = urllib.parse.urlparse(arg)
        if all((url.scheme, url.netloc)):
            return f"{url.scheme}://{url.netloc}{url.path}/"
        msg = "Invalid URL"
        raise argparse.ArgumentTypeError(msg)

    parser = argparse.ArgumentParser(
        description="Morgan: PyPI Mirror for Restricted Environments",
    )

    parser.add_argument(
        "-i",
        "--index-path",
        dest="index_path",
        default=os.getcwd(),
        help="Path to the package index",
    )
    parser.add_argument(
        "-I",
        "--index-url",
        dest="index_url",
        default=PYPI_ADDRESS,
        type=my_url,
        help="Base URL of the Python Package Index",
    )
    parser.add_argument(
        "-c",
        "--config",
        dest="config",
        nargs="?",
        help="Config file (default: <INDEX_PATH>/morgan.ini)",
    )
    parser.add_argument(
        "--skip-server-copy",
        dest="skip_server_copy",
        action="store_true",
        help="Skip server copy in mirror command (default: False)",
    )
    parser.add_argument(
        "-a",
        "--mirror-all-versions",
        dest="mirror_all_versions",
        action="store_true",
        help=(
            "For packages listed in the [requirements] section, mirror every release "
            "that matches their version specifiers. "
            "Transitive dependencies still mirror only the latest matching release. "
            "(Default: only the latest matching release)"
        ),
    )
    parser.add_argument(
        "--package-type-regex",
        dest="package_type_regex",
        default=r"(whl|zip|tar\.gz)",
        type=str,
        help="Regular expression to filter which package file types are mirrored",
    )
    parser.add_argument(
        "-W",
        "--mirror-all-wheels",
        dest="mirror_all_wheels",
        action="store_true",
        help=(
            "Download all compatible wheels for each version. "
            "(default: fetch only the wheel for latest compatible Python version)"
        ),
    )

    server.add_arguments(parser)
    configurator.add_arguments(parser)

    parser.add_argument(
        "command",
        choices=[
            "generate_env",
            "generate_reqs",
            "mirror",
            "serve",
            "copy_server",
            "version",
        ],
        help="Command to execute",
    )

    args = parser.parse_args()

    # These commands do not require a configuration file and therefore should
    # be executed prior to sanity checking the configuration
    if args.command == "generate_env":
        configurator.generate_env(args.env)
        return
    if args.command == "generate_reqs":
        configurator.generate_reqs(args.mode)
        return
    if args.command == "serve":
        server.run(args.index_path, args.host, args.port, args.no_metadata)
        return
    if args.command == "version":
        # ruff: noqa: T201
        print(f"Morgan v{__version__}")
        return

    if not args.config:
        args.config = os.path.join(args.index_path, "morgan.ini")
    if not os.path.isfile(args.config):
        # If a file named in filenames cannot be opened, that file will be ignored
        # https://docs.python.org/3.12/library/configparser.html#configparser.ConfigParser.read
        msg = f"Invalid config: {args.config}"
        raise argparse.ArgumentTypeError(msg)

    if args.command == "mirror":
        mirror(args)
    elif args.command == "copy_server":
        Mirrorer(args).copy_server()


if __name__ == "__main__":
    main()
