# Contributing

Thank you for helping improve Google Analytics Advisor.

## Before you start

Use a GitHub issue to describe a bug or proposed improvement. Keep every public example synthetic.
Never include customer data, OAuth files, tokens, passwords, private keys, Authorization headers,
Analytics exports, or private website source.

Published documentation, source comments, issue templates, and pull-request text should be in
English. The plugin itself must answer users in the language they use.

## Make a change

1. Fork the repository and create a focused branch.
2. Keep credentials and generated runtime data outside the repository.
3. Update the README and changelog when user-visible behavior changes.
4. Run the local validation:

   ```powershell
   pwsh -NoProfile -File .\tests\validate.ps1
   ```

5. Open a pull request explaining the user benefit, safety impact, and verification performed.

Contributions are accepted under the repository's MIT License. Opening a pull request does not
guarantee that it will be merged or released.
