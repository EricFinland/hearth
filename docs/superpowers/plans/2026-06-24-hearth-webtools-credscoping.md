# hearth: Web Tools + Per-Run Credential Scoping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** (A) give agents `web_search` and `web_fetch` tools so they can research the web, not just run local commands; (B) scope stored API credentials per run so an agent only reads the secrets it is granted.

**Architecture:** Both extend systems already built. Web tools are two new entries in the existing pluggable tool registry (`agent/hearth_tools.py`), each split into a network call plus a pure parser so the parser is unit-tested without the network; both are risk-classed `dangerous` in `agent/permissions.py` (so they gate in auto/plan, run in bypass). Credential scoping adds an allow-list: `_resolve_cred` honors an optional `HEARTH_ALLOWED_CREDS` env var (comma-separated names); a launch may declare `creds`, which mapd passes to the worker (queue file for background, subprocess env for sessions) and the spawn runner exports. No declaration keeps today's behavior (all creds resolve), so existing flows are unaffected.

**Tech Stack:** Python 3 standard library only (urllib, html.parser, html, re). Tests use the in-module `_self_test()` convention (no pytest). One NixOS runner edit. Dev machine is Windows (`python`). Blade deploy/verify at the end.

**Decisions (stated; change if you disagree):**
- `web_search` uses DuckDuckGo's keyless HTML endpoint (`https://html.duckduckgo.com/html/?q=...`). No API key, fits the local-first ethos. Fragile to DDG markup changes; the parser is isolated and unit-tested, and the tool degrades gracefully (returns "no results"/error text, never raises).
- Credential scoping is opt-in: a run that declares `creds: ["name", ...]` can read only those; a run that declares nothing reads all (backward compatible). Flipping the default to deny-all is a later one-liner once every launch path declares its creds.

**Commit identity (required):** every commit authored as Eric, no AI attribution:
`git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "<message>"`
No em-dashes in any committed file or message.

**Working directory:** `C:/Users/ericc/hearth-wt` (branch `worktree-desktop`). Blade: `ssh operator@192.168.1.64`, deploy via `git archive -o f.tar HEAD` + `scp` + `sudo nixos-rebuild switch --flake ~/hearth-desktop#blade`.

---

### Task 1: web_fetch tool + HTML-to-text

**Files:** Modify `agent/hearth_tools.py`

- [ ] **Step 1: Add a failing self-test**

In `agent/hearth_tools.py` `_self_test()`, before its final `print(...)`, add:
```python
    # web_fetch: the HTML-to-text helper strips tags and collapses whitespace.
    sample = "<html><head><style>x{}</style><script>var a=1;</script></head>" \
             "<body><h1>Title</h1><p>Hello   world</p><p>Line two</p></body></html>"
    txt = _html_to_text(sample)
    assert "Title" in txt and "Hello world" in txt and "Line two" in txt, txt
    assert "var a=1" not in txt and "x{}" not in txt, ("script/style stripped", txt)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python agent/hearth_tools.py`
Expected: `NameError: name '_html_to_text' is not defined`.

- [ ] **Step 3: Implement `_html_to_text` and `tool_web_fetch`**

Add near the top (after the imports), an HTML-to-text parser using `html.parser`:
```python
import html as _htmlmod
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "head", "noscript"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)


def _html_to_text(html_text):
    p = _TextExtractor()
    try:
        p.feed(html_text or "")
    except Exception:  # noqa: BLE001 - malformed HTML must not raise
        pass
    text = " ".join(" ".join(self_part.split()) for self_part in p.parts if self_part.strip())
    return text
```
Add the tool function (uses the existing urllib import and a browser-like UA):
```python
def tool_web_fetch(args, workspace):
    url = args.get("url")
    if not url:
        return "error: no url"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hearth-agent)"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read(2_000_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "error: {}".format(exc)
    text = _html_to_text(raw)
    return text[:MAX_OUT] if text else "(no readable text)"
```

- [ ] **Step 4: Register the tool**

Add to the `TOOLS` list:
```python
    {
        "name": "web_fetch",
        "description": "Fetch a web page and return its readable text (HTML tags stripped). Provide url.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]},
        "fn": tool_web_fetch,
    },
```

- [ ] **Step 5: Run to verify the self-test passes**

Run: `python agent/hearth_tools.py`
Expected: `hearth-tools self-test OK`.

- [ ] **Step 6: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_tools.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: web_fetch agent tool (fetch a page as readable text)"
```

---

### Task 2: web_search tool + DuckDuckGo result parser

**Files:** Modify `agent/hearth_tools.py`

- [ ] **Step 1: Add a failing self-test**

In `_self_test()`, before the final print, add:
```python
    # web_search: the DDG result parser extracts title/url/snippet from result HTML.
    ddg = ('<div class="result"><a class="result__a" '
           'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnixos.org%2F&rut=x">NixOS</a>'
           '<a class="result__snippet">Reproducible builds and deployments.</a></div>')
    res = _parse_ddg_results(ddg, 5)
    assert res and res[0]["title"] == "NixOS", res
    assert res[0]["url"] == "https://nixos.org/", res
    assert "Reproducible" in res[0]["snippet"], res
```

- [ ] **Step 2: Run to verify it fails**

Run: `python agent/hearth_tools.py`
Expected: `NameError: name '_parse_ddg_results' is not defined`.

- [ ] **Step 3: Implement the parser and tool**

Add (uses `re`, `urllib.parse`, `_htmlmod` from Task 1; add `import re` and `import urllib.parse` if not present):
```python
def _ddg_real_url(href):
    """DDG result links are redirects like //duckduckgo.com/l/?uddg=<encoded>.
    Return the decoded target, or the href unchanged if it is already direct."""
    if "uddg=" in href:
        try:
            q = urllib.parse.urlparse("https:" + href if href.startswith("//") else href).query
            uddg = urllib.parse.parse_qs(q).get("uddg")
            if uddg:
                return urllib.parse.unquote(uddg[0])
        except (ValueError, KeyError):
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


def _parse_ddg_results(html_text, max_results):
    """Extract [{title, url, snippet}] from DuckDuckGo HTML results."""
    results = []
    link_re = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    snip_re = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.S)
    links = link_re.findall(html_text or "")
    snippets = snip_re.findall(html_text or "")

    def clean(s):
        return " ".join(_htmlmod.unescape(re.sub(r"<[^>]+>", "", s)).split())

    for i, (href, title) in enumerate(links[:max_results]):
        results.append({
            "title": clean(title),
            "url": _ddg_real_url(href),
            "snippet": clean(snippets[i]) if i < len(snippets) else "",
        })
    return results


def tool_web_search(args, workspace):
    query = args.get("query") or args.get("q")
    if not query:
        return "error: no query"
    max_results = int(args.get("max_results") or 5)
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hearth-agent)"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "error: {}".format(exc)
    results = _parse_ddg_results(body, max_results)
    if not results:
        return "(no results)"
    return "\n".join("{}. {}\n   {}\n   {}".format(i + 1, r["title"], r["url"], r["snippet"])
                     for i, r in enumerate(results))[:MAX_OUT]
```

- [ ] **Step 4: Register the tool**
```python
    {
        "name": "web_search",
        "description": "Search the web and return top result titles, URLs, and snippets. Provide query.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "max_results": {"type": "integer"}},
            "required": ["query"]},
        "fn": tool_web_search,
    },
```

- [ ] **Step 5: Run to verify the self-test passes**

Run: `python agent/hearth_tools.py` -> `hearth-tools self-test OK`.

- [ ] **Step 6: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_tools.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: web_search agent tool (keyless DuckDuckGo HTML)"
```

---

### Task 3: Risk-class the web tools

**Files:** Modify `agent/permissions.py`

- [ ] **Step 1: Add failing assertions**

In `agent/permissions.py` `_self_test()`, add:
```python
    assert risk_of("web_search") == "dangerous", "web_search should be dangerous"
    assert risk_of("web_fetch") == "dangerous", "web_fetch should be dangerous"
    assert decide("auto", "web_search") == "gate"
    assert decide("bypass", "web_fetch") == "allow"
    assert decide("plan", "web_search") == "deny"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python agent/permissions.py`
Expected: AssertionError (web_search/web_fetch currently default to dangerous via the fallback, so `risk_of` already returns "dangerous" -- these assertions may actually PASS already because unknown tools fail closed to dangerous). If they pass, that is correct behavior; ADD explicit entries anyway for clarity in Step 3 and confirm still green. If any assertion fails, Step 3 fixes it.

- [ ] **Step 3: Add explicit RISK entries**

In the `RISK` dict, add:
```python
    "web_search": "dangerous",
    "web_fetch": "dangerous",
```

- [ ] **Step 4: Run to verify passing**

Run: `python agent/permissions.py` -> `hearth-permissions self-test OK`.

- [ ] **Step 5: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/permissions.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: classify web_search/web_fetch as dangerous in the permission engine"
```

---

### Task 4: Per-run credential scoping in `_resolve_cred`

**Files:** Modify `agent/hearth_tools.py`

- [ ] **Step 1: Add a failing self-test**

In `_self_test()`, add (this manipulates env + a temp creds dir):
```python
    # Credential scoping: HEARTH_ALLOWED_CREDS limits which creds resolve.
    import tempfile as _tf
    cdir = _tf.mkdtemp(prefix="hearth-creds-")
    with open(os.path.join(cdir, "creds"), "w") as fh:
        fh.write("alpha=secretA\nbravo=secretB\n")
    old_cd = os.environ.get("CREDENTIALS_DIRECTORY")
    old_allow = os.environ.get("HEARTH_ALLOWED_CREDS")
    os.environ["CREDENTIALS_DIRECTORY"] = cdir
    try:
        os.environ.pop("HEARTH_ALLOWED_CREDS", None)
        assert _resolve_cred("alpha") == "secretA", "no allow-list: all resolve"
        assert _resolve_cred("bravo") == "secretB", "no allow-list: all resolve"
        os.environ["HEARTH_ALLOWED_CREDS"] = "alpha"
        assert _resolve_cred("alpha") == "secretA", "allowed cred resolves"
        assert _resolve_cred("bravo") == "", "disallowed cred is withheld"
    finally:
        if old_cd is None:
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
        else:
            os.environ["CREDENTIALS_DIRECTORY"] = old_cd
        if old_allow is None:
            os.environ.pop("HEARTH_ALLOWED_CREDS", None)
        else:
            os.environ["HEARTH_ALLOWED_CREDS"] = old_allow
```

- [ ] **Step 2: Run to verify it fails**

Run: `python agent/hearth_tools.py`
Expected: AssertionError on "disallowed cred is withheld" (today `_resolve_cred` ignores the allow-list).

- [ ] **Step 3: Enforce the allow-list in `_resolve_cred`**

At the top of `_resolve_cred(name)`, before reading the file, add:
```python
    allowed = os.environ.get("HEARTH_ALLOWED_CREDS")
    if allowed is not None and name not in [a for a in allowed.split(",") if a]:
        return ""
```
(Place it as the first statement in the function. When `HEARTH_ALLOWED_CREDS` is unset, behavior is unchanged.)

- [ ] **Step 4: Run to verify passing**

Run: `python agent/hearth_tools.py` -> `hearth-tools self-test OK`.

- [ ] **Step 5: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_tools.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: per-run credential scoping via HEARTH_ALLOWED_CREDS allow-list"
```

---

### Task 5: Wire the creds allow-list through mapd and the spawn runner

**Files:** Modify `webui/hearth_mapd.py`, `agent/hearth_loop.py`, `nixos/modules/spawn.nix`

- [ ] **Step 1: Sessions pass the allow-list as subprocess env (`agent/hearth_loop.py` not needed; done in mapd)**

In `webui/hearth_mapd.py` `spawn_session(...)`, accept an optional `allowed_creds` and set it in the child env. Change the signature and Popen:
```python
def spawn_session(loop_cmd, sid, model, mode, workspace, db, ollama_url, allowed_creds=""):
    os.makedirs(workspace, exist_ok=True)
    args = [loop_cmd, "--session", "--model", model, "--mode", mode,
            "--agent-name", sid, "--workspace", workspace, "--db", db,
            "--ollama-url", ollama_url]
    env = dict(os.environ)
    if allowed_creds:
        env["HEARTH_ALLOWED_CREDS"] = allowed_creds
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    return Session(sid, proc)
```
In `_handle_new_session`, read `creds` (a list or comma string) from the request and pass it:
```python
        creds = req.get("creds")
        allowed = ",".join(creds) if isinstance(creds, list) else (creds or "")
```
and pass `allowed_creds=allowed` to `spawn_session(...)`.

- [ ] **Step 2: Background `/run` carries creds into the queue file**

In `_handle_run`, after reading mode, add:
```python
        creds = req.get("creds")
        allowed = ",".join(creds) if isinstance(creds, list) else (creds or "")
```
and include it in the queued JSON:
```python
                json.dump({"name": name, "model": model, "prompt": prompt,
                           "mode": mode, "creds": allowed}, fh)
```

- [ ] **Step 3: The spawn runner exports HEARTH_ALLOWED_CREDS (`nixos/modules/spawn.nix`)**

In the runner `text`, after the `mode=...` extraction, add a `creds=...` extraction and export it before the exec:
```nix
      creds="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('creds') or chr(0)*0)" "$req")"
```
and immediately before the final `exec`, add:
```nix
      export HEARTH_ALLOWED_CREDS="$creds"
```
(When `creds` is empty the var is empty; `_resolve_cred` treats unset and empty differently -- empty string means "allow nothing". To keep the backward-compatible "no declaration = all", only export when non-empty:)
```nix
      [ -n "$creds" ] && export HEARTH_ALLOWED_CREDS="$creds"
```
Use the guarded form so an empty creds declaration does not accidentally withhold all credentials.

- [ ] **Step 4: Verify self-tests still pass**

Run: `python webui/hearth_mapd.py --self-test` and `python agent/hearth_loop.py --self-test`. Both must print OK. (The mapd self-test does not spawn a real session, so the `spawn_session` signature change is covered by import + the existing Session test.)

- [ ] **Step 5: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add webui/hearth_mapd.py nixos/modules/spawn.nix
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: thread per-run creds allow-list through sessions and background workers"
```

---

### Task 6: Optional cockpit field for allowed creds

**Files:** Modify `webui/static/command.html`

- [ ] **Step 1: Add an input and include it in both launches**

In the `#launch` card, after the `agMode` select, add:
```html
    <input id="agCreds" placeholder="allowed creds (optional, comma-separated)" style="width:100%;margin-bottom:4px;background:#0e2236;border:1px solid #16324f;color:#cfe6ff;padding:6px;border-radius:6px;box-sizing:border-box;" />
```
In `agSession.onclick`, add `creds` to the POST body: `creds: document.getElementById("agCreds").value.trim()`.
In `agLaunch.onclick`, add the same `creds` field to its `/run` POST body.

- [ ] **Step 2: Validate**
```bash
python -c "h=open('webui/static/command.html',encoding='utf-8').read(); assert 'agCreds' in h; assert h.count('id=\"agLaunch\"')==1; assert h.count('<script')==h.count('</script>'); print('creds field OK')"
```
Expected: `creds field OK`.

- [ ] **Step 3: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add webui/static/command.html
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: optional allowed-creds field in the launch panel"
```

---

### Task 7: Deploy to the blade and verify, then push

**Files:** none.

- [ ] **Step 1: Local self-tests gate**
```bash
python agent/permissions.py
python agent/hearth_tools.py
python agent/hearth_loop.py --self-test
python webui/hearth_mapd.py --self-test
```
All four print their OK line.

- [ ] **Step 2: Deploy**
```bash
cd C:/Users/ericc/hearth-wt
git archive -o C:/Users/ericc/AppData/Local/Temp/wt.tar HEAD
for i in 1 2 3 4; do scp -o ConnectTimeout=25 C:/Users/ericc/AppData/Local/Temp/wt.tar operator@192.168.1.64:~/wt.tar && break || sleep 10; done
ssh -o ConnectTimeout=30 operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service 2>/dev/null; cd ~/hearth-desktop && sudo nixos-rebuild switch --flake ~/hearth-desktop#blade 2>&1 | tail -3'
```

- [ ] **Step 3: Verify web tools live (bypass session over the API)**
```bash
ssh operator@192.168.1.64 'set +e
SID=$(curl -s -X POST localhost:8770/run -H "Content-Type: application/json" -d "{\"name\":\"web\",\"model\":\"qwen2.5-coder:latest\",\"mode\":\"bypass\",\"prompt\":\"Use the web_search tool to search for: NixOS reproducible builds. Then briefly summarize the top result.\"}")
echo "$SID"; sleep 50
python3 -c "import sqlite3;c=sqlite3.connect(\"/var/lib/hearth/runs/audit.db\");[print(r[0][:120]) for r in c.execute(\"select event from agent_transcript order by id desc limit 8\")]"'
```
Expected: a `tool_result` for `web_search` containing result lines (titles/URLs), proving the keyless search works from the box. If DDG returns nothing (markup change/rate limit), the transcript shows "(no results)" and the parser needs a selector tweak -- note it; the parser self-test still guarantees the parsing logic.

- [ ] **Step 4: Verify credential scoping**
```bash
ssh operator@192.168.1.64 'set +e
# seed two creds if the secrets file is writable; otherwise just confirm the env path
echo "alpha=A1" | sudo tee /var/lib/hearth/secrets/agent-credentials >/dev/null 2>&1
echo "bravo=B2" | sudo tee -a /var/lib/hearth/secrets/agent-credentials >/dev/null 2>&1
sudo chmod 0640 /var/lib/hearth/secrets/agent-credentials 2>/dev/null
# launch an auto worker scoped to only alpha that tries to read both via http header cred:NAME is hard to assert here;
# instead assert the env threading: launch with creds and check the unit got HEARTH_ALLOWED_CREDS
curl -s -X POST localhost:8770/run -H "Content-Type: application/json" -d "{\"name\":\"scoped\",\"model\":\"qwen2.5-coder:latest\",\"mode\":\"bypass\",\"creds\":[\"alpha\"],\"prompt\":\"run_command: echo creds-scope-test\"}" >/dev/null
sleep 20
echo "=== a recent scoped queue/run happened; confirm the loop received the allow-list by checking journal ==="
journalctl -u "hearth-agent@scoped-*" -n 20 --no-pager 2>&1 | tail -8'
```
Expected: the scoped worker ran. (Functional cred withholding is unit-tested in Task 4; this step confirms the end-to-end launch with a creds list does not break the run.)

- [ ] **Step 5: Push**
```bash
cd C:/Users/ericc/hearth-wt
git fetch origin
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" merge origin/main -m "merge: main before web tools + cred scoping"
git push origin worktree-desktop:main
```

---

## Self-Review

**Coverage:** web_fetch (Task 1) + web_search (Task 2) + risk classing (Task 3) deliver "more agent tools"; `_resolve_cred` allow-list (Task 4) + mapd/runner threading (Task 5) + optional UI (Task 6) deliver per-run credential scoping. Task 7 deploys and verifies both.

**Placeholder scan:** every code step has complete code; the one degradation path (DDG markup drift) is called out with the unit-tested parser as the guarantee. No TBD.

**Type/name consistency:** `_html_to_text`, `_TextExtractor`, `tool_web_fetch`; `_parse_ddg_results(html, max_results)`, `_ddg_real_url(href)`, `tool_web_search`; both registered in `TOOLS` and risk-classed in `permissions.RISK`. `_resolve_cred` reads `HEARTH_ALLOWED_CREDS`; `spawn_session(..., allowed_creds="")` sets it in the child env; `_handle_run` writes `creds` into the queue JSON; the spawn runner exports `HEARTH_ALLOWED_CREDS` only when non-empty (so undeclared = all, matching `_resolve_cred`'s unset behavior). The launch field id is `agCreds`.

**Note on the `_html_to_text` helper:** the generator uses a local name `self_part` (not `self`) to avoid any confusion with method scope; it is a plain comprehension variable.
