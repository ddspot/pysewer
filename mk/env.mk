# managed by hpc-env v0.1.0 — edit in hpc-env/templates/, not here
ENV_MK_INCLUDED := 1

ENV_NAME    ?= pysewer
ENV_FILE    ?= environment.yml
LOCK_FILE   ?= conda-lock.yml
HOST_OS     := $(shell uname -s)

HPC_MODULE_INIT ?= source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null || true
HPC_MODULE_LOAD ?= module purge && module load Miniforge3/25.3.1-0 && module load Mamba/25.3.1-0

ifeq ($(HOST_OS),Linux)
ifneq ($(wildcard conda-linux-64.lock),)
LOCK_FILE := conda-linux-64.lock
endif
endif

# Auto-detect local vs HPC: if /work exists, use HPC prefix; otherwise local miniforge.
ifeq ($(wildcard /work/$(USER)),)
ENV_PREFIX ?= $(HOME)/miniforge3/envs/$(ENV_NAME)
CONDA_CMD  := mamba
IS_HPC     := 0
else
ENV_PREFIX ?= /work/$(USER)/conda_envs/$(ENV_NAME)
CONDA_CMD  := mamba
IS_HPC     := 1
endif

.PHONY: env env-rebuild env-recreate env-update env-local env-local-rebuild \
	lock validate validate-yml check doctor clean-env clean-lock

env:
ifeq ($(IS_HPC),1)
	bash -lc '$(HPC_MODULE_INIT); $(HPC_MODULE_LOAD); source bin/bootstrap_env.sh "$(ENV_NAME)" "$(ENV_PREFIX)" "$(ENV_FILE)" "$(LOCK_FILE)"'
else
	@if [ -d "$(ENV_PREFIX)" ]; then \
		echo "[env] existing environment detected at $(ENV_PREFIX)"; \
	elif [ "$(HOST_OS)" != "Linux" ] && [ "$(LOCK_FILE)" = "conda-lock.yml" ]; then \
		echo "[env] local platform $(HOST_OS) -> using $(ENV_FILE) instead of Linux-oriented $(LOCK_FILE)"; \
		$(CONDA_CMD) env create -y -p "$(ENV_PREFIX)" -f "$(ENV_FILE)"; \
	elif [ -f "$(LOCK_FILE)" ] && case "$(LOCK_FILE)" in *.yml|*.yaml) true ;; *) false ;; esac; then \
		echo "[env] creating local environment from $(LOCK_FILE) via conda-lock install"; \
		conda-lock install --conda "$(CONDA_CMD)" -p "$(ENV_PREFIX)" "$(LOCK_FILE)"; \
	elif [ -f "$(LOCK_FILE)" ]; then \
		echo "[env] creating local environment from $(LOCK_FILE)"; \
		$(CONDA_CMD) create -y -p "$(ENV_PREFIX)" --file "$(LOCK_FILE)"; \
	else \
		echo "[env] creating local environment from $(ENV_FILE)"; \
		$(CONDA_CMD) env create -y -p "$(ENV_PREFIX)" -f "$(ENV_FILE)"; \
	fi
	@if [ -f pyproject.toml ] && [ -x "$(ENV_PREFIX)/bin/uv" ]; then \
		echo "[env] uv pip install -e .[dev]"; \
		"$(ENV_PREFIX)/bin/uv" pip install --python "$(ENV_PREFIX)/bin/python" -e ".[dev]" \
		  || "$(ENV_PREFIX)/bin/uv" pip install --python "$(ENV_PREFIX)/bin/python" -e .; \
	fi
endif

env-rebuild:
ifeq ($(IS_HPC),1)
	bash -lc '$(HPC_MODULE_INIT); $(HPC_MODULE_LOAD); FORCE_REBUILD=1 source bin/bootstrap_env.sh "$(ENV_NAME)" "$(ENV_PREFIX)" "$(ENV_FILE)" "$(LOCK_FILE)"'
else
	@rm -rf "$(ENV_PREFIX)"
	@$(MAKE) -f Makefile env ENV_NAME="$(ENV_NAME)" ENV_FILE="$(ENV_FILE)" LOCK_FILE="$(LOCK_FILE)" ENV_PREFIX="$(ENV_PREFIX)"
endif

env-recreate: lock env-rebuild validate

env-update:
	$(CONDA_CMD) env update -p "$(ENV_PREFIX)" -f "$(ENV_FILE)" --prune
	@if [ -f pyproject.toml ] && [ -x "$(ENV_PREFIX)/bin/uv" ]; then \
		"$(ENV_PREFIX)/bin/uv" pip install --python "$(ENV_PREFIX)/bin/python" -e ".[dev]" \
		  || "$(ENV_PREFIX)/bin/uv" pip install --python "$(ENV_PREFIX)/bin/python" -e .; \
	fi

env-local:
	@if [ -d "$(ENV_PREFIX)" ]; then \
		echo "[env-local] existing environment detected at $(ENV_PREFIX)"; \
	elif [ "$(HOST_OS)" != "Linux" ] && [ "$(LOCK_FILE)" = "conda-lock.yml" ]; then \
		echo "[env-local] local platform $(HOST_OS) -> using $(ENV_FILE) instead of Linux-oriented $(LOCK_FILE)"; \
		$(CONDA_CMD) env create -y -p "$(ENV_PREFIX)" -f "$(ENV_FILE)"; \
	elif [ -f "$(LOCK_FILE)" ] && case "$(LOCK_FILE)" in *.yml|*.yaml) true ;; *) false ;; esac; then \
		echo "[env-local] creating local environment from $(LOCK_FILE) via conda-lock install"; \
		conda-lock install --conda "$(CONDA_CMD)" -p "$(ENV_PREFIX)" "$(LOCK_FILE)"; \
	elif [ -f "$(LOCK_FILE)" ]; then \
		echo "[env-local] creating local environment from $(LOCK_FILE)"; \
		$(CONDA_CMD) create -y -p "$(ENV_PREFIX)" --file "$(LOCK_FILE)"; \
	else \
		echo "[env-local] creating local environment from $(ENV_FILE)"; \
		$(CONDA_CMD) env create -y -p "$(ENV_PREFIX)" -f "$(ENV_FILE)"; \
	fi
	@if [ -f pyproject.toml ] && [ -x "$(ENV_PREFIX)/bin/uv" ]; then \
		echo "[env-local] uv pip install -e .[dev]"; \
		"$(ENV_PREFIX)/bin/uv" pip install --python "$(ENV_PREFIX)/bin/python" -e ".[dev]" \
		  || "$(ENV_PREFIX)/bin/uv" pip install --python "$(ENV_PREFIX)/bin/python" -e .; \
	fi

env-local-rebuild:
	@rm -rf "$(ENV_PREFIX)"
	@$(MAKE) -f Makefile env-local ENV_NAME="$(ENV_NAME)" ENV_FILE="$(ENV_FILE)" LOCK_FILE="$(LOCK_FILE)" ENV_PREFIX="$(ENV_PREFIX)"

lock:
	bash bin/lock_env.sh "$(ENV_FILE)"

validate:
ifeq ($(IS_HPC),1)
	bash -lc '$(HPC_MODULE_INIT); $(HPC_MODULE_LOAD); source bin/bootstrap_env.sh "$(ENV_NAME)" "$(ENV_PREFIX)" "$(ENV_FILE)" "$(LOCK_FILE)" && bash bin/env_doctor.sh "$(ENV_PREFIX)"'
else
	@$(MAKE) -f Makefile env ENV_NAME="$(ENV_NAME)" ENV_FILE="$(ENV_FILE)" LOCK_FILE="$(LOCK_FILE)" ENV_PREFIX="$(ENV_PREFIX)"
	@bash bin/env_doctor.sh "$(ENV_PREFIX)"
endif

validate-yml:
	bash bin/validate_env.sh "$(ENV_FILE)" "$(ENV_NAME)"

doctor:
	@bash bin/env_doctor.sh "$(ENV_PREFIX)"

check:
	@"$(ENV_PREFIX)/bin/python" -s -c "import sys; print('Python:', sys.executable); import numpy, pandas, pyarrow, geopandas, rasterio, fiona, pyproj, shapely, networkx, xarray, rioxarray; print('All core imports OK')" \
	|| (echo "Import check failed - run 'make env-rebuild'" && exit 1)

clean-env:
	rm -rf "$(ENV_PREFIX)"

clean-lock:
	rm -f conda-lock.yml conda-linux-64.lock
