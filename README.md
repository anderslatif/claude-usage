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

The data is retrieved from Anthropic's Messages API (`/v1/messages`) via the OAuth token saved in the macOS Keychain by Claude Code. 

The API is polled every 60 seconds to provide the session / weekly limits and reset countdown for both. 


