Configuration Setting
================================

.. important::

   Apply custom configuration **before** creating the
   :class:`~pysewer.preprocessing.ModelDomain`::

      pysewer.set_custom_config(custom_path="my_settings.yaml")  # 1st
      md = pysewer.ModelDomain(dem, roads, buildings)            # 2nd

   The ``preprocessing`` parameters ``clustering`` and
   ``connect_buildings`` are consumed while the connection graph is built
   inside ``ModelDomain(...)``; config overrides applied afterwards cannot
   affect them. Most other parameters (including ``pump_penalty`` and the
   ``optimization`` values) are read from the live config at the pipeline
   stage that uses them.

.. currentmodule:: pysewer.config

.. autosummary::
   :toctree: _autosummary/


.. automodule:: pysewer.config.settings
   :members: Config, load_config, config_to_dataframe, view_default_settings
   :undoc-members:
   :show-inheritance:

Preprocessing settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: pysewer.config.settings
   :members: Preprocessing
   :undoc-members:
   :show-inheritance:


Optimization settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: pysewer.config.settings
   :members: Optimization
   :undoc-members:
   :show-inheritance:


Visualisation settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: pysewer.config.settings
   :members: Plotting
   :undoc-members:
   :show-inheritance:


Export settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: pysewer.config.settings
   :members: Export
   :undoc-members:
   :show-inheritance: