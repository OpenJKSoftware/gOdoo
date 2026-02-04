"""Git operations for managing Odoo source code.

This package provides functionality for managing Odoo and addon source code using Git:
- Cloning and updating Odoo repositories
- Managing addon repositories
- Handling Git URLs and references
- Supporting archive downloads for Git repositories
"""

from .git_repo import git_ensure_repo
from .git_url import GitUrl
