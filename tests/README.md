<!--
SPDX-FileCopyrightText: 2025 ProFACE developers

SPDX-License-Identifier: MIT
-->

# Unit tests for `proface-pre`

## Coverage

### Error conditions

Various error conditions can be tested (on posix systems) with

```bash
uv run coverage erase
git ls-files "tests/e?.*" | xargs -n1 uv run coverage run -a -m proface.preprocessor
```

### Other paths

```bash
uv run coverage run -a -m proface.preprocessor --version
```
