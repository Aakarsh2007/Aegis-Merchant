# RevPilot AI
#
# Thin delegation to tasks.py, which is the single source of truth.
# `make` is not available on Windows, so every target here has an exact
# equivalent: `python tasks.py <target>`. They cannot drift apart.

.DEFAULT_GOAL := help
.PHONY: help install lint fmt types test eval fuzz check api web demo seed warm-cache batch chaos verify-audit tunnel clean

PY ?= python

help:
	@$(PY) tasks.py

install:
	@$(PY) tasks.py install

lint:
	@$(PY) tasks.py lint

fmt:
	@$(PY) tasks.py fmt

types:
	@$(PY) tasks.py types

test:
	@$(PY) tasks.py test

eval:
	@$(PY) tasks.py eval

fuzz:
	@$(PY) tasks.py fuzz

check:
	@$(PY) tasks.py check

api:
	@$(PY) tasks.py api

web:
	@$(PY) tasks.py web

demo:
	@$(PY) tasks.py demo

seed:
	@$(PY) tasks.py seed

warm-cache:
	@$(PY) tasks.py warm-cache

batch:
	@$(PY) tasks.py batch

chaos:
	@$(PY) tasks.py chaos

verify-audit:
	@$(PY) tasks.py verify-audit

tunnel:
	@$(PY) tasks.py tunnel

clean:
	@$(PY) tasks.py clean
