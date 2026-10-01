#!/usr/bin/env python3
"""
Setup script for Linux AI Assistant.

The canonical metadata lives in pyproject.toml; this file is kept only for
`python setup.py` and for the XBPS recipe, which references it.
"""

from setuptools import setup

# requirements.txt contains comments and blank lines, which are not valid
# requirements and would make setup() fail.
with open('requirements.txt', encoding='utf-8') as f:
    requirements = [
        line.strip()
        for line in f
        if line.strip() and not line.strip().startswith('#')
    ]

setup(install_requires=requirements)
