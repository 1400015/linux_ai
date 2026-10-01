#!/usr/bin/env python3
"""
Setup script for Linux AI Assistant.

The canonical metadata (name, version, dependencies) lives in pyproject.toml
and is the single source of truth: com setuptools>=61 e build via PEP 517
(o template XBPS usa python3-module), declarar `install_requires` aqui em
paralelo é rejeitado/avisado no build. Este ficheiro existe só para
`python setup.py` e para o template XBPS.
"""

from setuptools import setup

setup()
