# Discord server setup

`setup_server.py` builds the [Hearth Discord](https://discord.gg/8bathX4UQY) from code:
roles, the channel layout for both editions (NixOS and Windows), permissions,
Community mode, onboarding, AutoMod, the welcome and rules posts, the server
icon, and a GitHub webhook per repo for the live dev logs.

It is idempotent. Edit the layout or post text here, then re-run it to apply
the change; existing channels and posts are updated in place, never duplicated.

```bash
pip install -U "discord.py>=2.4"
export DISCORD_TOKEN="..."   # bot token: never commit it
export GUILD_ID="1535552732889612348"
python scripts/discord/setup_server.py
```

The bot needs Administrator for the run. Remove it (or reset its token)
afterwards. The webhook URLs it writes to `github_webhooks.txt` are secrets;
that file is gitignored.
