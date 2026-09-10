# Renaming MusicBench to ComposerBench

The distribution and Python package are now `composerbench`. Import the client with:

```python
from composerbench.client import ComposerBench
```

The CLI commands are `composerbench` and `cbench`. MCP configuration uses `COMPOSERBENCH_ADAPTER`, `COMPOSERBENCH_CLI`, `COMPOSERBENCH_PROJECT`, and `COMPOSERBENCH_SERVE`.

Update old `musicbench` imports, `MusicBench` class references, and `MUSICBENCH_*` environment variables in scripts. This release does not install aliases for the old Python package or commands.

Existing `.musicbench` sidecar directories, user library directories, `mb:` stable handles, and `music_*` MCP tool names are intentionally retained. These are persisted data and protocol identifiers, so changing them would invalidate existing projects and integrations.

The original workstation source was preserved when preparing this independently installable repository. Use a separate virtual environment to run ComposerBench.
