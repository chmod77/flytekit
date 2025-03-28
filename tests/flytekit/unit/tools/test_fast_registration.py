import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from hashlib import md5
from pathlib import Path

import pytest

from flytekit.constants import CopyFileDetection
from flytekit.tools.fast_registration import (
    FAST_FILEENDING,
    FAST_PREFIX,
    FastPackageOptions,
    compute_digest,
    fast_package,
    get_additional_distribution_loc,
)
from flytekit.tools.ignore import DockerIgnore, GitIgnore, Ignore, IgnoreGroup, StandardIgnore
from tests.flytekit.unit.tools.test_ignore import make_tree


@pytest.fixture
def flyte_project(tmp_path):
    tree = {
        "data": {"large.file": "", "more.files": ""},
        "src": {
            "workflows": {
                "__pycache__": {"some.pyc": ""},
                "hello_world.py": "print('Hello World!')",
            },
        },
        "utils": {
            "util.py": "print('Hello from utils!')",
        },
        ".venv": {"lots": "", "of": "", "packages": ""},
        ".env": "supersecret",
        "some.bar": "",
        "some.foo": "",
        "keep.foo": "",
        ".gitignore": "\n".join([".env", ".venv", "# A comment", "data", "*.foo", "!keep.foo"]),
        ".dockerignore": "\n".join(["data", "*.bar", ".git"]),
    }

    make_tree(tmp_path, tree)
    os.symlink(str(tmp_path) + "/utils/util.py", str(tmp_path) + "/src/util")
    subprocess.run(["git", "init", str(tmp_path)])
    return tmp_path


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skip if running on windows since Unix Domain Sockets do not exist in that OS",
)
def test_skip_socket_file():
    tmp_dir = tempfile.mkdtemp()

    tree = {
        "data": {"large.file": "", "more.files": ""},
        "src": {
            "workflows": {
                "hello_world.py": "print('Hello World!')",
            },
        },
    }

    # Add a socket file
    socket_path = tmp_dir + "/test.sock"
    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_socket.bind(socket_path)

    subprocess.run(["git", "init", str(tmp_dir)])

    # Assert that this runs successfully
    compute_digest(str(tmp_dir))


def test_package(flyte_project, tmp_path):
    archive_fname = fast_package(source=flyte_project, output_dir=tmp_path)
    with tarfile.open(archive_fname) as tar:
        assert sorted(tar.getnames()) == [
            ".dockerignore",
            ".gitignore",
            "keep.foo",
            "src",
            "src/util",
            "src/workflows",
            "src/workflows/__pycache__",
            "src/workflows/hello_world.py",
            "utils",
            "utils/util.py",
        ]
        util = tar.getmember("src/util")
        assert util.issym()
    assert str(os.path.basename(archive_fname)).startswith(FAST_PREFIX)
    assert str(archive_fname).endswith(FAST_FILEENDING)


def test_package_with_ignore(flyte_project, tmp_path):
    class TestIgnore(Ignore):
        def _is_ignored(self, path: str) -> bool:
            return path.startswith("utils")

    options = FastPackageOptions(ignores=[TestIgnore])
    archive_fname = fast_package(source=flyte_project, output_dir=tmp_path, deref_symlinks=False, options=options)
    with tarfile.open(archive_fname) as tar:
        assert sorted(tar.getnames()) == [
            ".dockerignore",
            ".gitignore",
            "keep.foo",
            "src",
            "src/util",
            "src/workflows",
            "src/workflows/__pycache__",
            "src/workflows/hello_world.py",
        ]
    assert str(os.path.basename(archive_fname)).startswith(FAST_PREFIX)
    assert str(archive_fname).endswith(FAST_FILEENDING)


def test_package_with_ignore_without_defaults(flyte_project, tmp_path):
    class TestIgnore(Ignore):
        def _is_ignored(self, path: str) -> bool:
            return path.startswith("utils")

    options = FastPackageOptions(ignores=[TestIgnore, GitIgnore, DockerIgnore], keep_default_ignores=False)
    archive_fname = fast_package(source=flyte_project, output_dir=tmp_path, deref_symlinks=False, options=options)
    with tarfile.open(archive_fname) as tar:
        assert sorted(tar.getnames()) == [
            ".dockerignore",
            ".gitignore",
            "keep.foo",
            "src",
            "src/util",
            "src/workflows",
            "src/workflows/__pycache__",
            "src/workflows/__pycache__/some.pyc",
            "src/workflows/hello_world.py",
        ]
    assert str(os.path.basename(archive_fname)).startswith(FAST_PREFIX)
    assert str(archive_fname).endswith(FAST_FILEENDING)


def test_package_with_symlink(flyte_project, tmp_path):
    archive_fname = fast_package(source=flyte_project / "src", output_dir=tmp_path, deref_symlinks=True)
    with tarfile.open(archive_fname, dereference=True) as tar:
        assert sorted(tar.getnames()) == [
            "util",
            "workflows",
            "workflows/__pycache__",
            "workflows/hello_world.py",
        ]
        util = tar.getmember("util")
        assert util.isfile()
    assert str(os.path.basename(archive_fname)).startswith(FAST_PREFIX)
    assert str(archive_fname).endswith(FAST_FILEENDING)


def test_digest_ignore(flyte_project):
    ignore = IgnoreGroup(flyte_project, [GitIgnore, DockerIgnore, StandardIgnore])
    digest1 = compute_digest(flyte_project, ignore.is_ignored)

    change_file = flyte_project / "data" / "large.file"
    assert ignore.is_ignored(change_file)
    change_file.write_text("I don't matter")

    digest2 = compute_digest(flyte_project, ignore.is_ignored)
    assert digest1 == digest2


def test_digest_change(flyte_project):
    ignore = IgnoreGroup(flyte_project, [GitIgnore, DockerIgnore, StandardIgnore])
    digest1 = compute_digest(flyte_project, ignore.is_ignored)

    change_file = flyte_project / "src" / "workflows" / "hello_world.py"
    assert not ignore.is_ignored(change_file)
    change_file.write_text("print('I do matter!')")

    digest2 = compute_digest(flyte_project, ignore.is_ignored)
    assert digest1 != digest2


def test_get_additional_distribution_loc():
    assert get_additional_distribution_loc("s3://my-s3-bucket/dir", "123abc") == "s3://my-s3-bucket/dir/123abc.tar.gz"


def test_skip_invalid_symlink_in_compute_digest(tmp_path):
    tree = {
        "dir1": {"file1": ""},
        "dir2": {},
        "file.txt": "abc",
    }

    make_tree(tmp_path, tree)
    os.symlink(str(tmp_path) + "/file.txt", str(tmp_path) + "/dir2/file.txt")

    # Confirm that you can compute the digest without error
    assert compute_digest(tmp_path) is not None

    # Delete the file backing the symlink
    os.remove(tmp_path / "file.txt")

    # Confirm that you can compute the digest without error
    assert compute_digest(tmp_path) is not None


# Skip test if `pigz` is not installed
@pytest.mark.skipif(
    subprocess.run(["which", "pigz"], stdout=subprocess.PIPE).returncode != 0,
    reason="pigz is not installed",
)
def test_package_with_pigz(flyte_project, tmp_path):
    # Call fast_package twice and compare the md5 of the resulting tarballs

    options = FastPackageOptions(ignores=[], copy_style=CopyFileDetection.ALL)

    Path(tmp_path / "dir1").mkdir()
    archive_fname_1 = fast_package(source=flyte_project, output_dir=tmp_path / "dir1", options=options)
    # Copy the tarball bytes and remove the file to ensure it is not included in the next invocation of fast_package
    archive_1_bytes = Path(archive_fname_1).read_bytes()
    Path(archive_fname_1).unlink()

    # Wait a second to ensure the next tarball has a different timestamp, which consequently tests if there is an impact
    # to the metadata of the resulting tarball
    time.sleep(1)

    Path(tmp_path / "dir2").mkdir()
    archive_fname_2 = fast_package(source=flyte_project, output_dir=tmp_path / "dir2", options=options)

    # Compare the md5sum of the two tarballs
    assert md5(archive_1_bytes).hexdigest() == md5(Path(archive_fname_2).read_bytes()).hexdigest()
