"""
Hearth Discord server bootstrap: one community, two products
(Hearth for NixOS and Hearth for Windows).

Builds roles, categories, channels, permissions, Community mode, onboarding,
AutoMod, welcome/rules posts, and a GitHub dev-log webhook per product.
Safe to re-run: existing items are reused or renamed in place, posts are
edited rather than duplicated.

Usage:
    pip install -U "discord.py>=2.4"
    export DISCORD_TOKEN="..."   # bot token - never commit this
    export GUILD_ID="..."        # right-click the server -> Copy Server ID
    python scripts/discord/setup_server.py
"""

import asyncio
import datetime
import os
import sys

import discord
from discord import PermissionOverwrite as PO

# ---- Project links ----------------------------------------------------------
NIX_REPO = "https://github.com/EricFinland/hearth"
WIN_REPO = "https://github.com/EricFinland/hearth-windows"
NIX_DOCS = "https://ericfinland.github.io/hearth/"
WIN_DOWNLOAD = f"{WIN_REPO}/releases/latest"
ACCENT = 0xCC785C      # Hearth for NixOS ember (README badges)
WIN_ACCENT = 0x4A9EFF  # Hearth for Windows blue (README badges)
ICON_PATH = os.environ.get(
    "ICON_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_icon.png"))
SERVER_DESCRIPTION = ("Local LLMs and autonomous agents on your own hardware. "
                      "Home of Hearth for NixOS and Hearth for Windows.")
# -----------------------------------------------------------------------------

REASON = "Hearth server bootstrap"
TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
WEBHOOK_OUT = os.environ.get("WEBHOOK_OUT", "github_webhooks.txt")  # gitignored: holds secrets

REPORT_PRIVATELY = (
    "report it privately through GitHub Security Advisories on the "
    f"[NixOS]({NIX_REPO}/security/advisories/new) or "
    f"[Windows]({WIN_REPO}/security/advisories/new) repo")

ROLES = [
    # name, color, hoist (shown separately), mentionable, base permissions
    ("Maintainer", 0xCC785C, True, False, discord.Permissions(
        manage_guild=True, manage_channels=True, manage_roles=True,
        manage_messages=True, manage_threads=True, manage_webhooks=True,
        manage_nicknames=True, kick_members=True, ban_members=True,
        moderate_members=True, mention_everyone=True, view_audit_log=True)),
    ("Moderator", 0x5B8DEF, True, False, discord.Permissions(
        manage_messages=True, manage_threads=True, manage_nicknames=True,
        kick_members=True, moderate_members=True, view_audit_log=True)),
    ("Contributor", 0x57A773, True, False, discord.Permissions.none()),
    ("NixOS Releases", 0x7EBAE4, False, True, discord.Permissions.none()),
    ("Windows Releases", WIN_ACCENT, False, True, discord.Permissions.none()),
]

# Old name -> new name, for servers set up by the single-product version.
CHANNEL_RENAMES = {"help": "nixos-help", "dev": "nixos-dev",
                   "github-feed": "nixos-devlog", "releases": "nixos-releases"}
ROLE_RENAMES = {"Release Pings": "NixOS Releases"}
OLD_CATEGORIES = ("Text Channels", "Voice Channels", "SUPPORT", "DEVELOPMENT")

NIX_HELP_TAGS = ["Installation", "Configuration", "Models (Ollama)", "Agents & MCP",
                 "Secrets (sops-nix)", "Hardware", "Bug", "Solved"]
WIN_HELP_TAGS = ["Install & Setup", "Models & Model Shop", "Agent", "GPU & Performance",
                 "Updates", "Bug", "Solved"]
IDEA_TAGS = [("Hearth for NixOS", False), ("Hearth for Windows", False),
             ("Proposal", False), ("Accepted", True), ("Planned", True), ("Declined", True)]
HELP_GUIDELINES = (
    "One post per problem. Include your {ver}, hardware, what you did, what you "
    "expected, and relevant logs. Redact secrets and keys. Tag your post, and add "
    "the Solved tag once it's fixed.")


def log(msg):
    print(msg, flush=True)


def get(seq, name):
    return discord.utils.get(seq, name=name)


# ---- ensure_* helpers: create if missing, otherwise bring into line ---------

async def ensure_role(guild, name, color, hoist, mentionable, perms):
    role = get(guild.roles, name)
    if role:
        return role
    log(f"+ role        {name}")
    return await guild.create_role(
        name=name, colour=discord.Colour(color), hoist=hoist,
        mentionable=mentionable, permissions=perms, reason=REASON)


async def ensure_category(guild, name, position, overwrites=None):
    cat = get(guild.categories, name)
    kwargs = {"overwrites": overwrites} if overwrites is not None else {}
    if cat:
        await cat.edit(position=position, reason=REASON, **kwargs)
        return cat
    log(f"+ category    {name}")
    return await guild.create_category(name, position=position, reason=REASON, **kwargs)


async def ensure_text(guild, cat, name, topic, overwrites=None):
    ch = get(guild.text_channels, name)
    if ch:
        kwargs = {"overwrites": overwrites} if overwrites else {"sync_permissions": True}
        return await ch.edit(category=cat, topic=topic, reason=REASON, **kwargs) or ch
    log(f"+ #{name}")
    kwargs = {"overwrites": overwrites} if overwrites else {}
    return await guild.create_text_channel(
        name, category=cat, topic=topic, reason=REASON, **kwargs)


async def ensure_forum(guild, cat, name, guidelines, tags):
    tag_objs = [discord.ForumTag(name=t, moderated=m) for t, m in tags]
    forum = get(guild.forums, name)
    if forum:
        kwargs = {}
        if {t.name for t in forum.available_tags} != {t for t, _ in tags}:
            kwargs["available_tags"] = tag_objs
        return await forum.edit(category=cat, topic=guidelines, sync_permissions=True,
                                reason=REASON, **kwargs) or forum
    log(f"+ #{name} (forum)")
    return await guild.create_forum(name, category=cat, topic=guidelines,
                                    reason=REASON, available_tags=tag_objs)


async def ensure_voice(guild, cat, name, rename_from=None):
    vc = get(guild.voice_channels, name)
    if vc is None and rename_from:
        old = get(guild.voice_channels, rename_from)
        if old:
            log(f"~ voice       {rename_from} -> {name}")
            return await old.edit(name=name, category=cat, sync_permissions=True, reason=REASON)
    if vc:
        await vc.edit(category=cat, sync_permissions=True, reason=REASON)
        return vc
    log(f"+ voice       {name}")
    return await guild.create_voice_channel(name, category=cat, reason=REASON)


async def apply_renames(guild):
    changed = []
    for old, new in CHANNEL_RENAMES.items():
        c = get(guild.channels, old)
        if c and not get(guild.channels, new):
            log(f"~ #{old} -> #{new}")
            await c.edit(name=new, reason=REASON)
            changed.append(("channel", new))
    for old, new in ROLE_RENAMES.items():
        r = get(guild.roles, old)
        if r and not get(guild.roles, new):
            log(f"~ role        {old} -> {new}")
            await r.edit(name=new, reason=REASON)
            changed.append(("role", new))
    # Renames land in the cache via gateway events; wait so we never duplicate.
    for _ in range(20):
        if all(get(guild.channels if kind == "channel" else guild.roles, n)
               for kind, n in changed):
            return
        await asyncio.sleep(0.5)
    sys.exit("Renames didn't show up in time; re-run the script.")


# ---- content ----------------------------------------------------------------

def welcome_embeds(ch, icon_url):
    head = discord.Embed(
        title="Welcome to Hearth",
        description=("Hearth runs local LLMs and autonomous agents on your own hardware: "
                     "every token from your own GPU, at no cost. It comes in two editions, "
                     "and this server is home to both."),
        colour=ACCENT)
    if icon_url:
        head.set_thumbnail(url=icon_url)
    nix = discord.Embed(
        title="Hearth for NixOS", url=NIX_REPO, colour=ACCENT,
        description=("The whole OS from one flake. Agents sandboxed at the OS level, every run "
                     "audited, secrets in sops-nix, atomic rollbacks."))
    nix.add_field(name="Links", value=f"[GitHub]({NIX_REPO}) · [Docs]({NIX_DOCS})")
    nix.add_field(name="Get help", value=ch["nixos-help"].mention)
    win = discord.Embed(
        title="Hearth for Windows", url=WIN_REPO, colour=WIN_ACCENT,
        description=("A Windows 10/11 desktop app that brings its own inference engine and Python, "
                     "with a coding agent whose tools stay inside your workspace."))
    win.add_field(name="Links", value=f"[GitHub]({WIN_REPO}) · [Download]({WIN_DOWNLOAD})")
    win.add_field(name="Get help", value=ch["windows-help"].mention)
    tour = discord.Embed(colour=ACCENT)
    tour.add_field(name="Around the server", inline=False, value=(
        f"{ch['announcements'].mention} for project news\n"
        f"{ch['ideas'].mention} for feature requests (tag the edition)\n"
        f"{ch['showcase'].mention} to share your setup\n"
        f"{ch['nixos-devlog'].mention} and {ch['windows-devlog'].mention} for every push, live"))
    tour.add_field(name="Found a vulnerability?", inline=False,
                   value=f"Please don't post it here; {REPORT_PRIVATELY}.")
    return [head, nix, win, tour]


def rules_embed(ch):
    helps = f"{ch['nixos-help'].mention} and {ch['windows-help'].mention}"
    rules = [
        ("Be respectful.", "Disagree with ideas, not people. No harassment, slurs, or personal attacks."),
        ("Report vulnerabilities privately.", f"Never post exploit details or undisclosed issues publicly; {REPORT_PRIVATELY}."),
        ("Ask answerable questions.", f"In {helps}, include your version, hardware, what you did, what you expected, and relevant logs. Redact secrets and keys."),
        ("Use the right edition's channels.", "NixOS and Windows questions each have their own forum, so answers stay findable."),
        ("Stay on topic.", f"Use {ch['off-topic'].mention} for everything else."),
        ("No spam or self-promotion.", f"No unsolicited ads or DM advertising. Related projects are welcome in {ch['showcase'].mention}."),
        ("No piracy.", "No pirated software, leaked model weights, or help getting around licenses."),
        ("Follow Discord's rules.", "[Terms of Service](https://discord.com/terms) and [Community Guidelines](https://discord.com/guidelines) apply."),
    ]
    e = discord.Embed(title="Server Rules", colour=ACCENT)
    for i, (title, body) in enumerate(rules, 1):
        e.add_field(name=f"{i}. {title}", value=body, inline=False)
    e.set_footer(text="Moderators may act on behavior not listed here.")
    return e


async def upsert_post(channel, embeds, me):
    embeds = embeds if isinstance(embeds, list) else [embeds]
    async for msg in channel.history(limit=10):
        if msg.author.id == me.id:
            await msg.edit(embeds=embeds)
            log(f"~ updated post in #{channel.name}")
            return
    log(f"> posting in #{channel.name}")
    await channel.send(embeds=embeds)


# ---- server-level features (each optional; failures fall back to the UI) ----

async def enable_community(guild, rules, updates):
    if "COMMUNITY" in guild.features:
        return True
    try:
        await guild.edit(
            community=True, rules_channel=rules, public_updates_channel=updates,
            verification_level=discord.VerificationLevel.medium,
            explicit_content_filter=discord.ContentFilter.all_members,
            reason=REASON)
        log("+ Community mode enabled")
        return True
    except (discord.HTTPException, ValueError) as e:
        log(f"! Couldn't enable Community via API ({e}).\n"
            "  Do it in Server Settings -> Enable Community, then re-run this script.")
        return False


async def ensure_automod(guild, alert_channel, exempt):
    try:
        names = {r.name for r in await guild.fetch_automod_rules()}
        block = discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message)
        alert = discord.AutoModRuleAction(channel_id=alert_channel.id)
        rules = [
            ("Mention spam", discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.mention_spam, mention_limit=5),
             [block, alert, discord.AutoModRuleAction(duration=datetime.timedelta(minutes=10))]),
            ("Suspected spam", discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.spam), [block, alert]),
            ("Server invites", discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.keyword,
                regex_patterns=[r"discord(?:\.gg|(?:app)?\.com/invite)/[\w-]+"]),
             [block, alert]),
        ]
        for name, trigger, actions in rules:
            if name in names:
                continue
            log(f"+ automod     {name}")
            await guild.create_automod_rule(
                name=name, event_type=discord.AutoModRuleEventType.message_send,
                trigger=trigger, actions=actions, enabled=True,
                exempt_roles=exempt, reason=REASON)
    except Exception as e:  # noqa: BLE001 - optional feature
        log(f"! AutoMod setup skipped ({e}). Set it up in Server Settings -> AutoMod.")


async def ensure_onboarding(guild, ch, roles):
    try:
        opt, MC = discord.OnboardingPromptOption, discord.OnboardingPromptType.multiple_choice
        prompts = [
            discord.OnboardingPrompt(
                type=MC, title="Which Hearth do you use?",
                single_select=False, required=False, in_onboarding=True, options=[
                    opt(title="Hearth for NixOS", description="The flake-based OS",
                        channels=[ch["nixos-help"], ch["nixos-releases"]]),
                    opt(title="Hearth for Windows", description="The desktop app",
                        channels=[ch["windows-help"], ch["windows-releases"]]),
                ]),
            discord.OnboardingPrompt(
                type=MC, title="Want to help build it?",
                single_select=False, required=False, in_onboarding=True, options=[
                    opt(title="I want to contribute",
                        description="Dev discussion and the live commit logs",
                        channels=[ch["nixos-dev"], ch["windows-dev"],
                                  ch["nixos-devlog"], ch["windows-devlog"], ch["security"]]),
                ]),
            discord.OnboardingPrompt(
                type=MC, title="Get pinged for new releases?",
                single_select=False, required=False, in_onboarding=True, options=[
                    opt(title="Hearth for NixOS releases", roles=[roles["NixOS Releases"]]),
                    opt(title="Hearth for Windows releases", roles=[roles["Windows Releases"]]),
                ]),
        ]
        defaults = [ch[n] for n in ("welcome", "rules", "announcements", "general",
                                    "introductions", "showcase", "off-topic",
                                    "nixos-help", "windows-help", "ideas", "faq")]
        await guild.edit_onboarding(prompts=prompts, default_channels=defaults,
                                    enabled=True, reason=REASON)
        log("+ onboarding configured")
    except Exception as e:  # noqa: BLE001 - optional feature
        log(f"! Onboarding skipped ({e}). Set it up in Server Settings -> Onboarding.")


async def ensure_github_webhook(channel):
    hooks = await channel.webhooks()
    hook = get(hooks, "GitHub")
    if hook is None:
        log(f"+ webhook     GitHub in #{channel.name}")
        hook = await channel.create_webhook(name="GitHub", reason=REASON)
    return hook.url + "/github"


# ---- main build -------------------------------------------------------------

async def build(guild: discord.Guild, me: discord.Member):
    everyone = guild.default_role
    await apply_renames(guild)

    roles = {}
    for spec in ROLES:
        roles[spec[0]] = await ensure_role(guild, *spec)
    maint, mod = roles["Maintainer"], roles["Moderator"]

    read_only = {
        everyone: PO(send_messages=False, create_public_threads=False,
                     create_private_threads=False, add_reactions=True),
        maint: PO(send_messages=True),
        me: PO(send_messages=True),
    }
    staff_only = {
        everyone: PO(view_channel=False),
        maint: PO(view_channel=True),
        mod: PO(view_channel=True),
        me: PO(view_channel=True),
    }

    start = await ensure_category(guild, "START HERE", 0)
    community = await ensure_category(guild, "COMMUNITY", 1)
    nix = await ensure_category(guild, "HEARTH FOR NIXOS", 2)
    win = await ensure_category(guild, "HEARTH FOR WINDOWS", 3)
    project = await ensure_category(guild, "PROJECT", 4)
    voice = await ensure_category(guild, "VOICE", 5)
    staff = await ensure_category(guild, "STAFF", 6, overwrites=staff_only)

    ch = {}
    order = []  # desired top-to-bottom order

    async def text(cat, name, topic, ow=None):
        ch[name] = await ensure_text(guild, cat, name, topic, ow)
        order.append(ch[name])

    async def forum(cat, name, guidelines, tags):
        ch[name] = await ensure_forum(guild, cat, name, guidelines, tags)
        order.append(ch[name])

    await text(start, "welcome", "Start here.", read_only)
    await text(start, "rules", "Server rules.", read_only)
    await text(start, "announcements", "Project news for both editions, from the maintainers.", read_only)

    await text(community, "general", "General Hearth discussion, either edition.")
    await text(community, "introductions", "Say hi: which Hearth do you run, on what hardware, and what are you building?")
    await text(community, "showcase", "Show off your Hearth setups, configs, and projects built on it.")
    await text(community, "off-topic", "Anything else. Keep it friendly.")

    await forum(nix, "nixos-help", HELP_GUIDELINES.format(ver="Hearth commit or flake.lock rev"),
                [(t, False) for t in NIX_HELP_TAGS])
    await text(nix, "nixos-dev", f"Building Hearth for NixOS: architecture, PRs, design. Repo: {NIX_REPO}")
    await text(nix, "nixos-devlog", "Every push to EricFinland/hearth, live from GitHub.", read_only)
    await text(nix, "nixos-releases", "Release notes for Hearth for NixOS. Grab the NixOS Releases role to get pinged.", read_only)

    await forum(win, "windows-help", HELP_GUIDELINES.format(ver="Hearth version, Windows version, GPU"),
                [(t, False) for t in WIN_HELP_TAGS])
    await text(win, "windows-dev", f"Building Hearth for Windows: architecture, PRs, design. Repo: {WIN_REPO}")
    await text(win, "windows-devlog", "Every push to EricFinland/hearth-windows, live from GitHub.", read_only)
    await text(win, "windows-releases", "Release notes for Hearth for Windows. Grab the Windows Releases role to get pinged.", read_only)

    await forum(project, "ideas", "Feature requests and proposals. Tag which edition it's for, "
                "search before posting, one idea per post. Maintainers set the status tags.", IDEA_TAGS)
    await text(project, "faq", "Answers to common questions about both editions.", read_only)
    await text(project, "security", "Threat models and hardening for both editions. Do NOT post undisclosed "
               "vulnerabilities here; report them privately via GitHub Security Advisories on the relevant repo.")

    await ensure_voice(guild, voice, "Lounge", rename_from="General")
    await ensure_voice(guild, voice, "Office Hours")

    await text(staff, "mod-chat", "Staff discussion.")
    await text(staff, "mod-log", "AutoMod alerts and moderation log.")
    await text(staff, "discord-updates", "Notices from Discord about Community servers.")

    # Put channels in the intended order within each category.
    for i, c in enumerate(order):
        if c.position != i:
            await c.edit(position=i, reason=REASON)

    # Remove leftover categories from the default/old layout, only if empty.
    chans = await guild.fetch_channels()
    for cat in chans:
        if isinstance(cat, discord.CategoryChannel) and cat.name in OLD_CATEGORIES:
            if not any(getattr(c, "category_id", None) == cat.id for c in chans):
                log(f"- category    {cat.name} (empty)")
                await cat.delete(reason=REASON)

    if os.path.exists(ICON_PATH) and (guild.icon is None or os.environ.get("FORCE_ICON")):
        log("~ server icon set")
        with open(ICON_PATH, "rb") as f:
            await guild.edit(icon=f.read(), reason=REASON)
    icon_url = guild.icon.url if guild.icon else None

    await upsert_post(ch["welcome"], welcome_embeds(ch, icon_url), me)
    await upsert_post(ch["rules"], rules_embed(ch), me)

    if await enable_community(guild, ch["rules"], ch["discord-updates"]):
        for name in ("announcements", "nixos-releases", "windows-releases"):
            if not ch[name].is_news():
                log(f"~ #{name} -> announcement channel (followable)")
                await ch[name].edit(type=discord.ChannelType.news, reason=REASON)
        await ensure_onboarding(guild, ch, roles)
        if guild.description != SERVER_DESCRIPTION:
            log("~ server description set")
            await guild.edit(description=SERVER_DESCRIPTION, reason=REASON)
    try:
        await guild.widget()
    except discord.Forbidden:  # widget off; it powers the README badge's online count
        log("~ server widget enabled")
        await guild.edit(widget_enabled=True, widget_channel=ch["welcome"], reason=REASON)

    await ensure_automod(guild, ch["mod-log"], [maint, mod])

    owner = await guild.fetch_member(guild.owner_id)
    if maint not in owner.roles:
        log(f"+ gave {owner} the Maintainer role")
        await owner.add_roles(maint, reason=REASON)

    invites = [i for i in await guild.invites()
               if i.inviter and i.inviter.id == me.id and i.max_age == 0]
    invite = invites[0] if invites else await ch["welcome"].create_invite(
        max_age=0, max_uses=0, unique=False, reason=REASON)
    log(f"Permanent invite: {invite.url}")

    with open(WEBHOOK_OUT, "w") as f:
        for repo, name in ((NIX_REPO, "nixos-devlog"), (WIN_REPO, "windows-devlog")):
            url = await ensure_github_webhook(ch[name])
            f.write(f"{repo}  ->  #{name}\n  {url}\n")
    log(f"\nDone. GitHub webhook URLs written to {WEBHOOK_OUT} (treat them like passwords).")


class SetupClient(discord.Client):
    failed = False

    async def on_ready(self):
        try:
            guild = self.get_guild(GUILD_ID)
            if guild is None:
                sys.exit(f"Bot isn't in a server with ID {GUILD_ID}. Check GUILD_ID and the invite.")
            log(f"Setting up '{guild.name}' as {self.user}\n")
            await build(guild, guild.me)
        except Exception:
            import traceback
            traceback.print_exc()
            self.failed = True
        finally:
            await self.close()


if __name__ == "__main__":
    if not TOKEN or not GUILD_ID:
        sys.exit("Set DISCORD_TOKEN and GUILD_ID environment variables first.")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    client = SetupClient(intents=discord.Intents.default(), proxy=proxy)
    client.run(TOKEN, log_handler=None)
    sys.exit(1 if client.failed else 0)
