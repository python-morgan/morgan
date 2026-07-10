# ruff: noqa: FBT001, FBT002, PERF401, PLR2004, PLW0602, PLW0603, S104
# FBT: allow boolean as positional argument
# PERF401: allow manual list comprehensions
# PLR2004: allow magic values in comparisons
# PLW0602: allow unassigned globals
# PLW0603: allow usage of globals
# S104: allow binding to all network interfaces even if it is insecure
from __future__ import annotations

import argparse
import html
import http.server
import json
import os
import pathlib
import re
import urllib.parse
from typing import Any

PYPI_JSON_TYPE_V1 = "application/vnd.pypi.simple.v1+json"
PYPI_JSON_TYPE_LT = "application/vnd.pypi.simple.latest+json"
PYPI_HTML_TYPE_V1 = "application/vnd.pypi.simple.v1+html"
GENL_HTML_TYPE = "text/html"

project_re = re.compile(r"/([^/]+)/")
file_re = re.compile(r"/([^/]+)/([^/]+)")
index_path = os.getcwd()
no_metadata = False


class RequestHandler(http.server.BaseHTTPRequestHandler):
    """
    The request handler class for the server. Extends
    http.server.BaseHTTPRequestHandler. Implements the Simple Repository API
    (PEP 503) with HTML output, or JSON output as per PEP 691. Also serves
    metadata files for packages as per PEP 658. Only GET requests are currently
    supported.
    """

    def do_GET(self):
        url = urllib.parse.urlsplit(urllib.parse.unquote(self.path))

        ct = parse_accept_header(self.headers.get("Accept"))
        if ct is None:
            self.send_response(406)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"The server cannot generate a response "
                b"in any of the requested MIME types",
            )
            return

        if url[2] in ["", "/"]:
            self._serve_project_listing(ct)
            return

        m = project_re.fullmatch(url[2])
        if m and m.group(1) != "":
            self._serve_project(ct, m.group(1))
            return

        m = file_re.fullmatch(url[2])
        if m and m.group(1) != "" and m.group(2) != "":
            self._serve_file(m.group(1), m.group(2))
            return

        self._serve_notfound()

    def _serve_notfound(self, msg: str | None = None):
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        if msg:
            self.wfile.write(msg.encode("utf-8"))
        else:
            self.wfile.write(b"Page not found")

    def _serve_project_listing(self, ct):
        projects = []
        with os.scandir(index_path) as it:
            for entry in it:
                if entry.is_dir():
                    projects.append({"name": entry.name})
        projects.sort(key=lambda proj: proj["name"])

        if ct in [PYPI_JSON_TYPE_V1, PYPI_JSON_TYPE_LT]:
            self.send_response(200)
            self.send_header("Content-Type", PYPI_JSON_TYPE_V1)
            self.end_headers()
            body = json.dumps({"meta": {"api-version": "1.0"}, "projects": projects})
            self.wfile.write(body.encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.end_headers()
            self.wfile.write(b"<!DOCTYPE html>\n<html>\n  <body>\n")
            for i, project in enumerate(projects):
                newline = "\n" if i < len(projects) - 1 else ""
                self.wfile.write(
                    '    <a href="/{}/">{}</a>{}'.format(
                        html.escape(project["name"]),
                        html.escape(project["name"]),
                        newline,
                    ).encode("utf-8"),
                )
            self.wfile.write(b"\n  </body>\n</html>")

    def _serve_project(self, ct, project):
        global no_metadata
        project = normalize(project)

        path = pathlib.Path(index_path, project)
        if not path.exists() or not path.is_dir():
            self._serve_notfound(f"No such project {project}")
            return

        files = []
        with os.scandir(path) as it:
            for entry in it:
                if re.search(r"\.(whl|zip|tar\.gz)$", entry.name):
                    file: dict[str, Any] = {
                        "filename": entry.name,
                        "url": f"/{project}/{entry.name}",
                        "hashes": {},
                    }

                    # read file hash
                    hashfile = path.joinpath(f"{entry.name}.hash")
                    if hashfile.exists():
                        with open(hashfile) as hf:
                            data = hf.read().strip().split("=")
                            file["hashes"][data[0]] = data[1]

                    # do we have a metadata file?
                    file["dist-info-metadata"] = (
                        False
                        if no_metadata
                        else path.joinpath(f"{entry.name}.metadata").exists()
                    )

                    files.append(file)
        files.sort(key=lambda file: file["filename"])

        if ct in [PYPI_JSON_TYPE_V1, PYPI_JSON_TYPE_LT]:
            self.send_response(200)
            self.send_header("Content-Type", PYPI_JSON_TYPE_V1)
            self.end_headers()
            body = json.dumps(
                {"name": project, "meta": {"api-version": "1.0"}, "files": files},
            )
            self.wfile.write(body.encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.end_headers()
            self.wfile.write(b"<!DOCTYPE html>\n<html>\n  <body>\n")
            for i, file in enumerate(files):
                newline = "\n" if i < len(files) - 1 else ""
                hashval = ""
                if "sha256" in file["hashes"]:
                    hashval = "#{}={}".format("sha256", file["hashes"]["sha256"])
                if not no_metadata and file.get("dist-info-metadata", False):
                    self.wfile.write(
                        '    <a href="{}{}" data-dist-info-metadata="true">{}</a>{}'.format(
                            html.escape(file["url"]),
                            html.escape(hashval),
                            html.escape(file["filename"]),
                            newline,
                        ).encode("utf-8"),
                    )
                else:
                    self.wfile.write(
                        '    <a href="{}{}">{}</a>{}'.format(
                            html.escape(file["url"]),
                            html.escape(hashval),
                            html.escape(file["filename"]),
                            newline,
                        ).encode("utf-8"),
                    )
            self.wfile.write(b"\n  </body>\n</html>")

    def _serve_file(self, project, filename):
        global no_metadata
        project = normalize(project)

        path = pathlib.Path(index_path, project, filename)
        if not path.exists() or not path.is_file():
            self._serve_notfound(f"No such project {project}")
            return

        if no_metadata and re.search(r"\.metadata$", filename):
            self._serve_notfound(f"No such file {filename}")
            return

        ct = "text/plain"
        if re.search(r"\.(whl|zip)$", filename):
            ct = "application/octet-stream"
        elif re.search(r"\.tar\.gz$", filename):
            ct = "application/x-tar"

        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.end_headers()
        self.wfile.write(path.read_bytes())


def run(
    ipath: str = os.getcwd(),
    host: str = "0.0.0.0",
    port: int = 8080,
    nomd: bool = False,
):
    """
    Run the server on the provided index path, listening on the provided host
    and port. All arguments are options. By default, index path is the current
    working directory, and the server will listen on all interfaces at port
    8080.
    """

    global index_path
    global no_metadata
    index_path = ipath
    no_metadata = nomd
    http.server.ThreadingHTTPServer(
        (host, port),
        RequestHandler,
    ).serve_forever()


def parse_accept_header(header_val: str | None) -> str | None:
    """
    Parses an Accept HTTP header and returns a selected MIME type for the server
    to answer with, honoring priorities defined in the header value. If the
    header value is empty, or the topmost priority is */*, HTML will be returned
    for backwards compatibility with PEP 503 clients.
    """

    if not header_val:
        return PYPI_HTML_TYPE_V1

    accepts_all = False

    options = [parse_accept_option(option) for option in header_val.split(",")]
    options.sort(key=lambda option: option["priority"], reverse=True)

    for option in options:
        if option["mime"] == "*/*":
            accepts_all = True

        if option["mime"] in [
            PYPI_JSON_TYPE_V1,
            PYPI_JSON_TYPE_LT,
            PYPI_HTML_TYPE_V1,
            GENL_HTML_TYPE,
        ]:
            return option["mime"]

    if accepts_all:
        return PYPI_HTML_TYPE_V1

    return None


opt_re = re.compile(r"([^;]+)\s*(?:;.*q=(\d(?:\.\d+)?))")


def parse_accept_option(option: str) -> dict:
    """
    Parses one option from a multi-option Accept header, returning a dictionary
    with the MIME type (key "mime") and priority/quality (key "priority", a
    float). If option does not specifically list a priority, it will be zero.
    """

    m = opt_re.match(option)
    if m is None:
        return {"mime": option.strip(), "priority": 0}

    return {
        "mime": m.group(1).strip(),
        "priority": float(m.group(2)) if m.lastindex == 2 else 0,
    }


def normalize(name):
    """
    Normalize the name of a package as per PEP 503.
    """

    return re.sub(r"[-_.]+", "-", name).lower()


def add_arguments(parser: argparse.ArgumentParser):
    """
    Used to add server-specific command line options.
    """

    parser.add_argument(
        "-H",
        "--host",
        dest="host",
        default="0.0.0.0",
        help="Host to listen on",
    )
    parser.add_argument(
        "-p",
        "--port",
        dest="port",
        default=8080,
        type=int,
        help="Port to listen on",
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        dest="no_metadata",
        default=False,
        help="Do not serve metadata files",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Morgan PyPI Server")
    parser.add_argument(
        "-i",
        "--index-path",
        dest="index_path",
        default=os.getcwd(),
        help="Path to the package index",
    )
    add_arguments(parser)
    args = parser.parse_args()

    run(args.index_path, args.host, args.port, args.no_metadata)
