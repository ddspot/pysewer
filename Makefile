-include mk/env.mk
-include Makefile.local

PYTHON ?= $(HOME)/miniforge3/envs/pysewer/bin/python
DOCS_BUILD ?= docs/build/html

.PHONY: docs docs-publish

docs:
	$(PYTHON) -m sphinx -b html docs/source $(DOCS_BUILD)

# Manual fallback for the CI docs-mirror job: build the docs and force-push
# them to the GitHub mirror's gh-pages branch (served by GitHub Pages).
docs-publish: docs
	touch $(DOCS_BUILD)/.nojekyll
	cd $(DOCS_BUILD) && rm -rf .git && git init -q -b gh-pages \
		&& git add -A \
		&& git commit -q -m "docs: publish sphinx build ($$(git -C $(CURDIR) rev-parse --short HEAD))" \
		&& git push --force https://github.com/ddspot/pysewer.git gh-pages \
		&& rm -rf .git
