.PHONY: setup pipeline test lint dashboard clean

RAW := data/raw/DonneesOuvertes (2).csv

setup:  ## Install runtime + dev dependencies
	pip install -r requirements-dev.txt

pipeline:  ## Clean + aggregate the raw export into data/processed/
	python src/build_dataset.py --raw "$(RAW)"

test:  ## Run the test suite
	pytest tests/ -v

lint:  ## Static-check the pipeline and tests
	ruff check src/ tests/

dashboard:  ## Open the live dashboard locally, no server needed
	python -c "import webbrowser; webbrowser.open('docs/index.html')"

clean:  ## Remove local caches and build artifacts
	rm -rf .pytest_cache .ruff_cache src/__pycache__ tests/__pycache__
