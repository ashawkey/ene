Ene is an experimental agent harness for personal usage. 

## Features

- **Skill-first Design**: Memory is skill. Hierarhical skill loading makes the core small, but capacity large.
- **Optimized Bundled Skills**: plan, review, clean up, create skills, background processes, pdf-reading, ...
- **Terminal-Native & Web UI**: Native terminal experience, while also attached to a modern web UI.

## Quick Start

```bash
pip install ene-agent
```

Configure a model in `~/.ene.yaml`, then start chatting:

```yaml
openai: # openai-compatible
  deepseek: # model alias
    model: deepseek-v4-pro # model name
    base_url: https://api.deepseek.com # base URL
    api_key: ... # your API key
```

```bash
ene # start the terminal UI
```