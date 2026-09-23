

<p align="center">
    <picture>
    <img alt="logo" src="docs/public/favicon.svg" width="10%">
    </picture>
    </br>
    <b>Ene</b>
    </br>
    <code>pip install ene-agent</code>
    &nbsp;&nbsp;&bull;&nbsp;&nbsp;
    <a href="https://ene.kiui.moe/">Documentation</a>
</p>

An experimental, Python-first coding agent with transparent context, tools, and skills.

## Features

- **Transparent context:** inspect the system prompt and conversation; no hidden memory.
- **Skills on demand:** planning, reviews, monitoring, PDFs, and reusable lessons.
- **Personal library:** synchronize skills and personas through Git.
- **Terminal and Web UI:** work locally or attach from a browser.

## Quick Start

```bash
pip install ene-agent

# Or install from GitHub
pip install git+https://github.com/ashawkey/ene.git
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
ene [NAME]  # start a session; name is optional
ene resume  # resume a saved conversation
ene attach  # attach to a live session
```

- [Model setup and ChatGPT subscription login](https://ene.kiui.moe/#configure-a-model)
- [CLI commands](https://ene.kiui.moe/commands/) · [Web UI](https://ene.kiui.moe/web-ui/) · [Skill library](https://ene.kiui.moe/library/)

**Tools run automatically with your shell permissions, without a safety boundary.** Use an OS sandbox or container to constrain them.
