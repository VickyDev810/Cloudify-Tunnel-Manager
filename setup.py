from setuptools import setup, find_packages
import os

setup(
    name="cloudify-cli",
    version="1.0.0",
    description="A CLI tool for managing Cloudflare tunnels",
    packages=find_packages(),
    package_data={
        'api': ['static/**/*'],  # Include all frontend files in cli package
        'cli': ['frontend/**/*'],
    },
    include_package_data=True,
    # Remove data_files completely
    install_requires=[
        "click>=8.0.0",
        "fastapi>=0.68.0",
        "uvicorn>=0.15.0",
        "requests>=2.25.0",
        "pydantic>=1.8.0",
        "pyyaml",
        "passlib>=1.7.4",
        "python-multipart",
        "bcrypt==4.3.0"
    ],
    entry_points={
        "console_scripts": [
            "cloudify=cli.cli:cli",
        ],
    },
    python_requires=">=3.7",
)
