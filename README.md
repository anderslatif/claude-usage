# Claude Usage Mac Menu

[![](https://img.shields.io/pypi/v/claude_usage.svg)](https://pypi.org/pypi/claude_usage/)


<img src="https://raw.githubusercontent.com/anderslatif/claude-usage/main/assets/menu_item_example.png" alt="doctordoc logo" width="350" >


A macOS menu bar app that shows your Anthropic API spend and token usage in real time - like a battery indicator for your API budget.

---

## Get started

```bash
$ pip install claude-usage
$ claude-usage
```

**Pre-requsite**: Having authenticated with Claude Code:

```bash
$ claude
```

---

## How it works

The data is retrieved by sending a request to Anthropic's Messages API and reading the rate-limit headers from the response. The **input** token is `hi` and the **output** is capped to `1` token. This is achieved with the OAuth token that Claude Code stores in the macOS Keychain. By default the app polls every `60 seconds` but it can be configured in the menu bar. 

Recommended usage is to run the app when needing to closely monitor usage in real-time and not as a permanent fixture.



