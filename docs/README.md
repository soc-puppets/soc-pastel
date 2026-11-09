# Building the documentation

    pip install -e ".[docs]"
    sphinx-build -n -W --keep-going -b html docs docs/_build/html

The pages are reStructuredText. Open `docs/_build/html/index.html`. CI builds with the same flags: `-W` makes
warnings errors, and `-n` reports any unresolved reference to PASTEL's own
API. The published site is built by Read the Docs from `.readthedocs.yaml`.
