# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

import datetime
import importlib.metadata

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
import os
import shutil
import sys



sys.path.insert(0, os.path.abspath("../../pysewer"))


# sys.path.insert(0, os.path.abspath("../.."))


# -- Project information -----------------------------------------------------

project = "pysewer"
copyright = f"2022 - {datetime.datetime.now().year}, the pysewer developers from UFZ"
author = "WASP Team, UFZ"

# Version comes from the installed package (pyproject.toml), never hardcoded
release = importlib.metadata.version("pysewer")
version = release


# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "myst_parser",
    "sphinx_design",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.doctest",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "matplotlib.sphinxext.plot_directive",
    "ablog",
    "sphinx.ext.mathjax",
    "sphinx.ext.extlinks",
    "sphinx_favicon",  # to add custom favicons https://pypi.org/project/sphinx-favicon/
    "sphinx_codeautolink",  # automatic links from code to documentation # https://sphinx-codeautolink.readthedocs.io/en/latest/index.html
    "sphinx_copybutton",
]

# autosummaries from source files
autosummary_generate = True
# don't show __init__ docstring in class documentation
autoclass_content = "class"
# sort members by source order
autodoc_member_order = "bysource"


# Notes in boxes
napoleon_use_admonition_for_notes = True
# Attributes like parameters
napoleon_use_ivar = True
# keep "Other Parameters" section
# https://github.com/sphinx-doc/sphinx/issues/10330
napoleon_use_param = False

# this is a nice class-doc layout
numpydoc_show_class_members = True
# class members have no separate file, so they are not in a toctree
numpydoc_class_members_toctree = False
# maybe switch off with:    :no-inherited-members:
numpydoc_show_inherited_class_members = True
# add refs to types also in parameter lists
numpydoc_xref_param_type = True

# Whether to show a link to the source in HTML (default: True).
plot_html_show_source_link = False
# Whether to show links to the files in HTML (default: True).
plot_html_show_formats = False
# File formats to generate.
plot_formats = ["svg"]

myst_enable_extensions = [
    "colon_fence",
]
# napoleon_google_docstring = False


# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []



# -- Sphinx-copybutton options ---------------------------------------------
# Exclude copy button from appearing over notebook cell numbers by using :not()
# The default copybutton selector is `div.highlight pre`
# https://github.com/executablebooks/sphinx-copybutton/blob/master/sphinx_copybutton/__init__.py#L82
copybutton_selector = ":not(.prompt) > div.highlight pre"


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
# html_theme = "sphinx_rtd_theme"
html_theme = "pydata_sphinx_theme"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

html_theme_options = {
    "external_links": [
        {
            "url": "https://www.ufz.de/index.php?en=51198",
            "name": "UFZ WASP",
        },
        # {
        #     "url": "https://numfocus.org/",
        #     "name": "NumFocus",
        # },
        # {
        #     "url": "https://numfocus.org/donate",
        #     "name": "Donate to NumFocus",
        # },
    ],
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/ddspot/pysewer",
            "icon": "fa-brands fa-github",
        },
        # {
        #     "name": "PyPI",
        #     "url": "https://pypi.org/project/pydata-sphinx-theme",
        #     "icon": "fa-custom fa-pypi",
        # },
        # {
        #     "name": "PyData",
        #     "url": "https://pydata.org",
        #     "icon": "fa-custom fa-pydata",
        # },
    ],
}


# Example configuration for intersphinx: refer to the Python standard library.
intersphinx_mapping = {
    "Python": ("https://docs.python.org/3/", None),
    "NumPy": ("https://numpy.org/doc/stable/", None),
    "SciPy": ("https://docs.scipy.org/doc/scipy/", None),
    "pytest": ("https://docs.pytest.org/en/7.1.x/", None),
    "pyproj": ("https://pyproj4.github.io/pyproj/stable/", None),
    "dateutil": ("https://dateutil.readthedocs.io/en/stable/", None),
    "networkx": (
        "https://networkx.org/documentation/stable/",
        "https://networkx.org/documentation/stable/objects.inv",
    ),
    "matplotlib": ("https://matplotlib.org/stable/", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
}

# def setup(app):
#     app.connect('builder-inited', copy_notebooks)

# def copy_notebooks(app):
#     notebooks_src = sys.path.insert(0, os.path.abspath("../../notebooks"))
#     notebooks_dest = os.path.join(app.srcdir, 'examples')
#     if not os.path.exists(notebooks_dest):
#         os.makedirs(notebooks_dest)
#     for notebook in os.listdir(notebooks_src):
#         if notebook.endswith('.ipynb'):
#             shutil.copyfile(
#                 os.path.join(notebooks_src, notebook),
#                 os.path.join(notebooks_dest, notebook)
#             )
