# Contributing to tt-local-generator

Thank you for your interest in contributing to tt-local-generator! We welcome contributions from the community.

## How to Contribute

### Reporting Bugs

If you find a bug, please report it using [GitHub Issues](https://github.com/tenstorrent/tt-local-generator/issues). When reporting a bug, please include:

- A clear and descriptive title
- Steps to reproduce the issue
- Expected behavior vs. actual behavior
- Your environment (OS, Python version, GTK4 version, hardware if relevant)
- Any relevant logs or error messages

### Suggesting Features

We welcome feature suggestions! Please open a [GitHub Issue](https://github.com/tenstorrent/tt-local-generator/issues) with:

- A clear description of the feature
- The use case or problem it solves
- Any implementation ideas you may have

### Submitting Pull Requests

1. **Fork the repository** and create your branch from `main`
2. **Make your changes** following the project's coding standards
3. **Test your changes** - ensure `pytest` passes: `python3 -m pytest tests/`
4. **Update documentation** if you're adding new features or changing behavior
5. **Commit your changes** with clear, descriptive commit messages
6. **Add SPDX headers** to any new source files (see below)
7. **Push to your fork** and submit a pull request to the `main` branch

### Pull Request Review Process

- Pull requests are typically reviewed **weekly**
- Maintainers will provide feedback on your submission
- Once approved, your PR will be merged by a maintainer

## Development Setup

### Prerequisites

- Python 3.10 or later
- GTK4 and GObject bindings (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-4.0`)
- GStreamer for video playback
- FFmpeg for video processing
- Docker for running inference servers

### Ubuntu 24.04 Setup

```bash
# Clone the repository
git clone https://github.com/tenstorrent/tt-local-generator.git ~/code/tt-local-generator
cd ~/code/tt-local-generator

# Run the setup script (installs dependencies)
./setup_ubuntu.sh

# Install Python dependencies
pip install -r requirements.txt
```

### Running Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_worker.py -v
```

### Running the Application

```bash
# Start the GUI
./tt-gen

# Start the CLI
./tt-ctl status
./tt-ctl history
```

## Code Style

- Follow standard Python conventions (PEP 8)
- Use meaningful variable and function names
- Add docstrings to functions and classes
- Keep functions focused and concise
- Add SPDX headers to all new source files:
  ```python
  # SPDX-License-Identifier: Apache-2.0
  # SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
  ```

### GTK Threading Discipline

**CRITICAL**: GTK is strictly single-threaded. Never call GTK methods from background threads.

- All UI updates from worker threads must use `GLib.idle_add(callback, *args)`
- See `CLAUDE.md` for detailed GTK threading guidelines

## Project Structure

- `app/` - All Python source files
- `bin/` - Shell scripts for server management
- `tests/` - Test suite (pytest)
- `patches/` - Hotfix patches for vendored tt-inference-server
- `debian/` - Debian packaging files

## Testing Guidelines

- Write unit tests for new functionality
- Mock all subprocess and network calls
- Ensure tests pass: `python3 -m pytest tests/`
- Test with mock backend: `./tt-gen --mock --mock-devices 4`

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to ospo@tenstorrent.com.

## Questions?

If you have questions about contributing, feel free to:

- Open a [GitHub Issue](https://github.com/tenstorrent/tt-local-generator/issues)
- Contact the maintainers at ospo@tenstorrent.com

## License

By contributing to tt-local-generator, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
