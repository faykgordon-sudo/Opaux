from setuptools import find_packages, setup

setup(
    name="opaux",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "anthropic>=0.40.0",
        "click>=8.1.0",
        "rich>=13.0.0",
        "rich-click>=1.7.0",
        "pydantic>=2.0.0",
        "pyyaml>=6.0",
        "python-dotenv>=1.0.0",
        "python-docx>=1.1.0",
        "playwright>=1.40.0",
        "python-jobspy>=1.1.0",
    ],
    extras_require={
        "dev": [
            "pytest>=8.0.0",
            "pytest-asyncio>=0.23.0",
            "ruff>=0.4.0",
            "mypy>=1.10.0",
        ],
    },
    entry_points={
        "console_scripts": ["opaux=main:cli"],
    },
    python_requires=">=3.9",
    description="Modular Python CLI tool for automated job applications",
    author="Gordon Akaminko",
)
