import tempfile
from itertools import islice
from logging import getLogger

import pytest
from git import Repo

from godoo_cli.git import git_ensure_repo

LOGGER = getLogger(__name__)


@pytest.fixture
def example_repo() -> str:
    return "https://github.com/OpenJKSoftware/gOdoo"


def test_clone_repo(example_repo: str):
    """Test Git cloning and switching to different commit."""
    with tempfile.TemporaryDirectory() as td:
        git_ensure_repo(td, example_repo, branch="main")
        git_repo = Repo(td)
        old_commit = list(islice(git_repo.iter_commits(), 2))[-1]

        git_ensure_repo(td, example_repo, commit=old_commit)

        assert git_repo.head.commit == old_commit

        git_ensure_repo(td, example_repo, commit=old_commit)
