from setuptools import setup, find_packages

setup(
    name="launchpad-ctrl",
    version="1.0.0",
    description="A modular MIDI controller for Novation Launchpad Mini Mk2",
    author="LaunchPad Controller",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "PyQt5>=5.15",
        "mido>=1.3",
        "python-rtmidi>=1.5",
        "sounddevice>=0.4",
        "soundfile>=0.12",
        "pydub>=0.25",
        "numpy>=1.21",
    ],
    entry_points={
        "console_scripts": [
            "launchpad-ctrl=launchpad_ctrl.__main__:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Multimedia :: Sound/Audio :: MIDI",
        "Programming Language :: Python :: 3",
        "Operating System :: POSIX :: Linux",
    ],
)
