"""Sphinx configuration for the PASTEL documentation."""

from importlib import metadata

project = "PASTEL"
author = "PASTEL contributors"
copyright = "2026, PASTEL contributors"
try:
    release = metadata.version("soc-pastel")
except metadata.PackageNotFoundError:  # building from an uninstalled checkout
    release = "dev"
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_class_signature = "separated"
autodoc_default_options = {"members": True, "show-inheritance": True}
napoleon_google_docstring = True
napoleon_numpy_docstring = False

html_theme = "sphinx_rtd_theme"
html_title = f"PASTEL {release}"
# "Edit on GitHub" links.
html_context = {
    "display_github": True,
    "github_user": "soc-puppets",
    "github_repo": "soc-pastel",
    "github_version": "main",
    "conf_py_path": "/docs/",
}

source_suffix = {".rst": "restructuredtext"}
exclude_patterns = ["_build"]

# Nitpicky mode (-n, used in CI) reports every unresolved cross-reference.
# References into the standard library and third-party packages are not
# resolvable without intersphinx (which needs network access), so ignore
# those and keep the check for PASTEL's own API.
nitpick_ignore_regex = [
    (r"py:.*", r"^(?!soc_pastel\b).*"),
    (r"py:.*", r".*\.T$"),  # type variables
]
