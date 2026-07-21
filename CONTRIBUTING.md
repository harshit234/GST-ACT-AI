# Contributing to GST ACT AI

We're excited that you're interested in contributing to GST ACT AI! This project aims to simplify GST bookkeeping for small Indian businesses.

## How to Contribute

### 1. Report Bugs
If you find a bug, please open an issue with:
- Steps to reproduce
- Expected behavior vs. actual behavior
- Relevant logs (please redact any personal info, API keys, phone numbers, or GSTINs)
- Your OS and Python version

### 2. Suggest Features
We welcome feature suggestions! Open an issue outlining:
- The problem you're trying to solve
- Your proposed solution
- How it benefits the target users (small merchants, accountants)

### 3. Submit Pull Requests
If you want to contribute code:

1. **Fork the repository** on GitHub.
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/GST-ACT-AI.git
   cd GST-ACT-AI
   ```
3. **Create a branch** for your feature or bug fix:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Set up the dev environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
5. **Make your changes**. Please follow the project's code style (PEP 8 for Python).
6. **Add or update tests** if you're adding new logic or changing existing behavior.
7. **Run the test suite** to ensure everything passes:
   ```bash
   python -m pytest tests/ -v
   ```
8. **Commit your changes** with a clear commit message:
   ```bash
   git commit -m "feat: add your feature description"
   ```
9. **Push to your fork** and submit a Pull Request (PR).

## Code Style & Guidelines
- Follow standard Python PEP 8 conventions.
- Include a module-level docstring explaining the file's responsibility.
- Add function docstrings with Input, Process, and Output details for complex logic.
- Keep background tasks lightweight to comply with Twilio's webhook timeouts.

## Setting up API Keys for Local Dev
See the `README.md` and `.env.example` files for details on obtaining the necessary API keys (Google Vision, Gemini, Twilio, Supabase) to test the full pipeline locally.

Thank you for contributing!
