# Claude Usage Mac Menu

[![](https://img.shields.io/pypi/v/claude_usage.svg)](https://pypi.org/pypi/claude_usage/)


<img src="https://raw.githubusercontent.com/ailabexperiments/claude_usage_mac_menu/main/menu_item_example.png" alt="doctordoc logo" width="150" >


A macOS menu bar app that shows your Anthropic API spend and token usage in real time — like a battery indicator for your API budget.

---

## Get started

```bash
$ pip install claude_usage
$ claude-usage
```

Pre-requsite: Having authenticated with Claude Code:

```bash
$ claude-code login
```

---

## How it works

The data is retrieved from Anthropic's Usage API with the OAuth token saved by Claude Code in your macOS Keychain. 

which provides up-to-date information on your organization's API usage and costs. The app polls the API every 60 seconds and updates the menu bar title and dropdown with your current token usage and session / weekly reset window.

