.PHONY: install lint typecheck test test-all test-engine test-engine-postgres test-knowledge test-serve lint-serve regen-openapi docs-build docs-serve docs-clean release publish version-bump

install:
	uv sync --group dev --group docs

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

typecheck:
	uv run pyright

test:
	uv run pytest -m unit

test-all:
	uv run pytest

test-engine:
	uv run pytest tests/unit tests/integration tests/property tests/replay tests/migration \
		--ignore=tests/integration/test_postgres_checkpointer.py \
		--ignore=tests/integration/test_mlnode_sklearn.py \
		--ignore=tests/integration/test_mlnode_xgboost.py \
		--ignore=tests/integration/test_ml_pickle_safety.py \
		--ignore=tests/integration/test_training_subgraph_example.py

test-engine-postgres:
	uv run pytest tests/integration/test_postgres_checkpointer.py

test-knowledge:
	uv run pytest tests/unit tests/integration tests/property \
		--ignore=tests/integration/test_postgres_checkpointer.py \
		--ignore=tests/integration/test_mlnode_sklearn.py \
		--ignore=tests/integration/test_mlnode_xgboost.py \
		-m "knowledge or unit"

test-serve:
	uv run pytest -q -m serve --tb=short

lint-serve:
	uv run ruff check \
		src/stargraph/serve \
		src/stargraph/bosun \
		src/stargraph/nodes/nautilus \
		src/stargraph/nodes/interrupt \
		src/stargraph/nodes/artifacts \
		src/stargraph/tools/nautilus \
		src/stargraph/artifacts \
		src/stargraph/triggers

regen-openapi:
	uv run python scripts/regen_openapi.py

docs-build:
	uv run mkdocs build --strict

docs-serve:
	uv run mkdocs serve

docs-clean:
	rm -rf site/

release:
	@./scripts/release.sh

publish:
	uv build
	uv run twine upload dist/*

version-bump:
	@./scripts/version_bump.sh
