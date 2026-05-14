.PHONY: build clean test install start

build:
	mkdir -p build
	cc -O3 -std=c11 -Wall -Wextra -o build/hx711_sampler native/hx711_sampler.c -lm -ldl

clean:
	rm -rf build .pytest_cache **/__pycache__

test: build
	PYTHONPATH=src python -m pytest -q

install: build
	python -m pip install -r requirements.txt

start: build
	PYTHONPATH=src python -m scaletemp.web.app
