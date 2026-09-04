.PHONY: setup seed calibrate test demo clean

setup:      ## install dependencies (anthropic is optional; core is stdlib)
	pip install -r requirements.txt pytest

seed:       ## generate held-out and calibration datasets
	python3 -m recon.cli generate

calibrate:  ## sweep the acceptance threshold on the calibration split only
	python3 -m recon.cli calibrate

test:       ## run the adversarial test suite
	python3 -m pytest tests/ -q

demo:       ## seed, calibrate, reconcile, and print the report
	python3 -m recon.cli demo

clean:
	rm -rf out data __pycache__ */__pycache__ .pytest_cache
