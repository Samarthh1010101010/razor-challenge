.PHONY: setup seed calibrate baseline bench test demo dashboard clean

setup:      ## install dependencies (anthropic is optional; core is stdlib)
	pip install -r requirements.txt pytest

seed:       ## generate held-out and calibration datasets
	python3 -m recon.cli generate

calibrate:  ## sweep the acceptance threshold on the calibration split only
	python3 -m recon.cli calibrate

baseline:   ## rules-only baseline on the held-out split
	python3 -m evaluation.baseline data

bench:      ## throughput, median over repeats on a scaled-up batch
	python3 -m evaluation.bench

test:       ## run the adversarial test suite
	python3 -m pytest tests/ -q

demo:       ## seed, calibrate, reconcile, print the report, render the dashboard
	python3 -m recon.cli demo

dashboard:  ## re-render out/dashboard.html from the last run
	python3 -c "from pathlib import Path; from recon import dashboard; \
	print(dashboard.write(Path('out')))"

clean:
	rm -rf out data __pycache__ */__pycache__ .pytest_cache
