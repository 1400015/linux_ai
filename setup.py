#!/usr/bin/env python3
"""
Setup script para Linux AI Assistant
"""

from setuptools import setup, find_packages
from glob import glob

# Ler o ficheiro requirements.txt
with open('requirements.txt') as f:
    requirements = f.read().splitlines()

# Ler descrição do README
with open('README.md', 'r', encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='linux-ai-assistant',
    version='1.0.0',
    description='Assistente de IA permanente para Linux com interface flutuante',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Linux AI Assistant Team',
    author_email='',
    url='https://github.com/1400015/linux_ai',
    packages=find_packages(),
    data_files=[('share/linux-ai-assistant/themes', glob('themes/*.json'))],
    package_dir={'': '.'},
    python_requires='>=3.8',
    install_requires=requirements,
    entry_points={
        'console_scripts': [
            'linux-ai-assistant=src.app:main',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Operating System :: POSIX :: Linux',
        'Environment :: X11 Applications :: GTK',
        'Topic :: Desktop Environment',
        'Topic :: Utilities',
    ],
    project_urls={
        'Bug Reports': 'https://github.com/1400015/linux_ai/issues',
        'Source': 'https://github.com/1400015/linux_ai',
    },
)
