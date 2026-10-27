# Contributing to TermChat

Thanks for taking the time to contribute! TermChat is an educational project, and improvements of all sizes are welcome.

## Getting started

1. Fork the repository and clone your fork.
2. Create a topic branch from `main`:

   ```bash
   git checkout -b feat/short-description
   ```

3. Make your change and add or update tests where it makes sense.
4. Run the test suite before opening a pull request:

   ```bash
   python3 -m unittest discover -s tests -v
   ```

5. Push your branch and open a pull request against `main`.

## Pull request guidelines

- Keep each pull request focused on one change.
- Write a clear title and describe the motivation and approach.
- Reference related issues (for example, `Closes #12`).
- Make sure the CI matrix passes on every supported Python version.
- Update the README when behaviour or commands change.

## Code style

- Follow PEP 8 and keep the existing module layout.
- Prefer small, testable functions and explicit names.
- Keep network code compatible with Python 3.10 and newer.

## Reporting bugs

Open an issue with the steps to reproduce, the expected behaviour, and the actual behaviour. Include your Python version and operating system.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
