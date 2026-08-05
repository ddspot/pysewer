import subprocess
import sys

import pytest

from pysewer.config.manager import reset_config


def test_imports():
    # Test if all modules are imported correctly
    import pysewer
    import pysewer.__init__



    # Test if all modules are defined
    assert hasattr(pysewer, 'set_custom_config')


def test_import_without_matplotlib():
    """'import pysewer' must not pull in matplotlib (lazy plotting module)."""
    code = (
        "import sys; import pysewer; "
        "assert 'matplotlib' not in sys.modules, 'matplotlib imported eagerly'; "
        "assert 'pysewer.plotting' not in sys.modules, 'plotting imported eagerly'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_plotting_available_lazily():
    import pysewer

    assert callable(pysewer.plot_model_domain)
    assert callable(pysewer.plotting.plot_sewer_attributes)


@pytest.fixture(autouse=True)
def restore_config():
    """
    Ensure each test leaves the global config in its default state.
    """
    reset_config()
    yield
    reset_config()


# def test_set_custom_config():
#     # Test if set_custom_config function is defined
#     assert callable(set_custom_config)

#     # Test if set_custom_config function accepts custom_path argument
#     set_custom_config(custom_path="/path/to/custom/config")

#     # Test if set_custom_config function accepts custom_settings_dict argument
#     set_custom_config(custom_settings_dict={"key": "value"})
