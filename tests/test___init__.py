import pytest

from pysewer.config.manager import reset_config


def test_imports():
    # Test if all modules are imported correctly
    import pysewer
    import pysewer.__init__



    # Test if all modules are defined
    assert hasattr(pysewer, 'set_custom_config')


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
