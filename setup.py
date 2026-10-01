#!/usr/bin/env python3
"""
Setup script para Linux AI Assistant.

A metadata canónica vive em pyproject.toml; este ficheiro é mantido apenas
para `python setup.py` e para o recipe XBPS, que o referenciam.
"""

from setuptools import setup

# requirements.txt contém comentários e linhas em branco, que não são
# requisitos válidos e fariam setup() falhar.
with open('requirements.txt', encoding='utf-8') as f:
    requirements = [
        line.strip()
        for line in f
        if line.strip() and not line.strip().startswith('#')
    ]

setup(install_requires=requirements)
