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

An experimental agent harness for personal use.  
It's as powerful as other modern agents, but also pythonic and educational for you to understand what happens behind each tool/skill.

## Features

- **Nothing is Unknown**: Explicit context and system prompt, no unexpected memory: You know the agent.
- **Skill-first Design**: Hierarhical skill loading makes the core small, but capacity large. Memory is also skill.
- **Optimized Bundled Skills**: Plan, review, clean up, create skills, background processes, pdf-reading, ... 
- **Personal Skill Library**: Use a github repository to easily synchronize your skills.
- **Terminal-Native & Web UI**: Native terminal experience, while also attached to a modern web UI.

## Quick Start

```bash
pip install ene-agent

# or from github source
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

It also supports codex subscription login, please check the documentation for details.

```bash
ene # start the terminal UI to chat
ene --resume # resume a previous session

ene models # list available models
ene list # list live sessions (aliases: ls, l)
ene attach # attach to a live session (alias: a)
ene kill NAME_OR_ID # terminate a live session (alias: k)
ene status # check the status of the .ene folder
ene clean # remove disposable data such as tool results and scratch files
ene clean --history # also remove saved conversation sessions
ene hub # start web UI hub (need to run in background)
ene update # update to the latest source code from github
ene lib # skill library management
```

WARNING: it has the same permission as the shell user and NO safety guard, use at your own risk.