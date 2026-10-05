"""Single source of the application version.

Kept in its own module (no side effects) so that `pyproject.toml` can read
the literal via setuptools' `attr:` directive without importing the package.
"""

__version__ = "1.4.0"
