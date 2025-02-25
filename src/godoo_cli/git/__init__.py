"""Git operations for managing Odoo source code.

This package provides functionality for managing Odoo and addon source code using Git:
- Cloning and updating Odoo repositories
- Managing addon repositories
- Handling Git URLs and references
- Supporting archive downloads for Git repositories
"""

from .git_odoo import git_ensure_odoo_repo
from .git_odoo_addons import git_ensure_addon_repos
from .git_repo import git_ensure_repo
from .git_url import GitUrl
