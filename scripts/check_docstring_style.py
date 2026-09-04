"""Reject NumPy-style sections in Python docstrings."""

import ast
import logging
import re
from pathlib import Path

LOGGER = logging.getLogger(__name__)

IGNORED_DIRECTORIES = {
    ".git",
    ".godoo",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
}
NUMPY_SECTION = re.compile(
    r"(?m)^[ \t]*(Parameters|Returns|Yields|Raises):?[ \t]*\n[ \t]*-{3,}[ \t]*$",
)


def _python_files(root: Path):
    """Yield project Python files while skipping generated and tool-managed directories."""
    for path in root.rglob("*.py"):
        if not IGNORED_DIRECTORIES.intersection(path.relative_to(root).parts):
            yield path


def _numpy_sections(path: Path):
    """Yield line numbers and names for NumPy-style docstring sections."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        docstring = ast.get_docstring(node, clean=False)
        if not docstring:
            continue
        first_statement = node.body[0]
        for match in NUMPY_SECTION.finditer(docstring):
            yield first_statement.lineno + docstring[: match.start()].count("\n"), match.group(1)


def main() -> int:
    """Report NumPy-style docstring sections and return a process status."""
    root = Path(__file__).resolve().parents[1]
    failures = []
    for path in sorted(_python_files(root)):
        for line_number, section_name in _numpy_sections(path):
            failures.append((path, line_number, section_name))

    for path, line_number, section_name in failures:
        relative_path = path.relative_to(root)
        LOGGER.error(
            "%s:%d: NumPy-style '%s' section; use the repository's Google-style docstrings.",
            relative_path,
            line_number,
            section_name,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
