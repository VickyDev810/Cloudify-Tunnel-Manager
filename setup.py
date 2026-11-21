from setuptools import find_packages, setup

with open("cloudify/__init__.py") as f:
    version = next(line.split('"')[1] for line in f if line.startswith("__version__"))

setup(
    name="cloudify-cli",
    version=version,
    description="Manage Cloudflare tunnels from a CLI or a web UI",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    packages=find_packages(include=["cloudify", "cloudify.*"]),
    package_data={"cloudify": ["web/*.html", "web/css/*.css", "web/js/*.js", "web/js/views/*.js",
                               "web/fonts/*.woff2", "web/fonts/*.txt"]},
    include_package_data=True,
    install_requires=[
        "click>=8.0.0",
        "fastapi>=0.115.0",
        'starlette>=0.49.1; python_version >= "3.9"',
        'starlette>=0.40.0; python_version < "3.9"',
        "uvicorn>=0.23.0",
        "requests>=2.32.4",
        "pydantic>=1.10.0",
        "pyyaml>=6.0",
        "python-multipart>=0.0.18",
        "bcrypt>=4.1.0",
        "cryptography>=44.0.1",
        "asyncssh>=2.14.2",
    ],
    extras_require={"test": ["pytest>=7", "httpx>=0.24"]},
    entry_points={"console_scripts": ["cloudify=cloudify.cli:cli"]},
    python_requires=">=3.8",
)
