# hearth integration (control API + agent credentials) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let you drive hearth from other devices through a token-authed HTTP API, and let agents use your stored API keys for outbound calls without baking secrets into the repo or exposing the whole secret store.

**Architecture:** Add token auth to `hearth-mapd` (localhost stays open so the local cockpit works; non-localhost requests need a bearer token read from a secret file) plus a `/runs` JSON endpoint for results. Give launched agents their configured API keys through systemd's credential mechanism (`LoadCredential` from a sops-managed file), so the sandboxed DynamicUser reads them from `$CREDENTIALS_DIRECTORY`, not from the world.

**Tech Stack:** Python 3 stdlib (hearth-mapd), NixOS systemd (EnvironmentFile, LoadCredential), the existing sops secrets dir.

**Repo conventions (read first):**
- Work in the worktree `C:\Users\ericc\hearth-wt` (branch `worktree-desktop`, now even with `main`). Commit as Eric. No AI attribution. No em dashes in committed files.
- Python tested locally with `py` (3.14). Nix eval/build on the blade `ssh operator@192.168.1.64`; deploy by `git archive -o file.tar HEAD` then `scp` the tar and extract into `~/hearth-desktop` (do not use a long `git archive | ssh tar` pipe). WiFi intermittent; retry.
- `hearth-mapd` (webui/hearth_mapd.py) serves `/`, `/state`, `/events`, `/stats`, `/models`, `/command`, `/chat`, `/run`, `/healthz`. Its handler class has `do_GET`, `do_POST`, `_send`, `_read_json_body`, `make_server(host, port, db, static_dir)`. The service is defined in `nixos/modules/mapui.nix` (runs as user `hearth`). Launched agents run via `nixos/modules/spawn.nix` (`hearth-agent@` templated unit using `config.hearth.sandbox.profile`); the runner execs `hearth-loop`. The http tool lives in `agent/hearth_tools.py`.

---

## File Structure

- Modify `webui/hearth_mapd.py` - add `request_allowed()` auth helper, enforce it in `do_GET`/`do_POST`, add a `/runs` endpoint.
- Modify `nixos/modules/mapui.nix` - pass the API token to the service from a secret file (EnvironmentFile), document it.
- Modify `nixos/modules/spawn.nix` - `LoadCredential` the agent credentials file into `hearth-agent@`, and export its path to the loop.
- Modify `agent/hearth_tools.py` - the http tool can reference a stored credential by name (resolved from `$CREDENTIALS_DIRECTORY`), so the model supplies a cred name rather than a raw key.

---

## Task 1: API auth helper + enforcement

**Files:** Modify `webui/hearth_mapd.py`.

- [ ] **Step 1: Add the pure auth helper + self-test cases**

```python
LOCAL_IPS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
API_TOKEN = os.environ.get("HEARTH_API_TOKEN", "")


def request_allowed(client_ip, auth_header, token):
    """Localhost is always allowed (the local cockpit). Remote requests need a
    bearer token matching the configured one. If no token is configured, remote
    access is denied (localhost-only)."""
    if client_ip in LOCAL_IPS:
        return True
    if not token:
        return False
    expected = "Bearer " + token
    return bool(auth_header) and auth_header == expected
```
Add to `_self_test()`:
```python
    assert request_allowed("127.0.0.1", None, "") is True, "localhost open"
    assert request_allowed("192.168.1.9", None, "secret") is False, "remote no token"
    assert request_allowed("192.168.1.9", "Bearer secret", "secret") is True, "remote good token"
    assert request_allowed("192.168.1.9", "Bearer wrong", "secret") is False, "remote bad token"
    assert request_allowed("192.168.1.9", None, "") is False, "no token configured -> remote denied"
```

- [ ] **Step 2: Run the self-test**

Run: `cd "C:/Users/ericc/hearth-wt" && py webui/hearth_mapd.py --self-test`
Expected: `hearth-mapd self-test OK`

- [ ] **Step 3: Enforce in the handler**

At the very start of both `do_GET` and `do_POST` (before routing), add a guard that lets `/healthz` through always and otherwise checks auth:
```python
        if self.path.split("?", 1)[0] != "/healthz" and not request_allowed(
                self.client_address[0], self.headers.get("Authorization"), API_TOKEN):
            return self._send(403, "forbidden")
```
Put that as the first lines inside `do_GET` and inside `do_POST`.

- [ ] **Step 4: Verify locally (localhost still works)**

Run the existing serve check (it connects from 127.0.0.1, so it must still get the page):
```
cd "C:/Users/ericc/hearth-wt" && py -c "import sys,threading,time,urllib.request,tempfile,os;sys.path.insert(0,'webui');import hearth_mapd as m;d=tempfile.mkdtemp();srv=m.make_server('127.0.0.1',8799,os.path.join(d,'a.db'),'webui/static');threading.Thread(target=srv.serve_forever,daemon=True).start();time.sleep(0.3);print('localhost ok' if urllib.request.urlopen('http://127.0.0.1:8799/command',timeout=5).status==200 else 'FAIL');srv.shutdown()"
```
Expected: `localhost ok`

- [ ] **Step 5: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: hearth-mapd token auth (localhost open, remote needs a bearer token)"
```

---

## Task 2: /runs results endpoint

**Files:** Modify `webui/hearth_mapd.py`.

- [ ] **Step 1: Add a reader + route**

```python
def read_runs(db, limit=20):
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(SCHEMA)
        cur = con.execute(
            "SELECT started_at, agent_name, model, tokens_in, tokens_out, "
            "latency_ms, cost_usd, error FROM agent_runs ORDER BY started_at DESC LIMIT ?",
            (limit,))
        cols = ["started_at", "agent_name", "model", "tokens_in", "tokens_out",
                "latency_ms", "cost_usd", "error"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
        return rows
    except sqlite3.Error:
        return []
```
In `do_GET`, add:
```python
        if path == "/runs":
            return self._send(200, json.dumps({"runs": read_runs(self.db)}), "application/json")
```

- [ ] **Step 2: Verify locally**

```
cd "C:/Users/ericc/hearth-wt" && py -c "import sys,threading,time,urllib.request,tempfile,os,json;sys.path.insert(0,'webui');import hearth_mapd as m;d=tempfile.mkdtemp();db=os.path.join(d,'a.db');m._record_chat_run(db,'t','x',1,2,3,None);srv=m.make_server('127.0.0.1',8801,db,'webui/static');threading.Thread(target=srv.serve_forever,daemon=True).start();time.sleep(0.3);print(urllib.request.urlopen('http://127.0.0.1:8801/runs',timeout=5).read().decode());srv.shutdown()"
```
Expected: JSON with a `runs` array containing one run (agent_name `t`).

- [ ] **Step 3: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: hearth-mapd /runs results endpoint"
```

---

## Task 3: supply the API token to the service (Nix)

**Files:** Modify `nixos/modules/mapui.nix`.

- [ ] **Step 1: Read the token from a secret file via EnvironmentFile**

In `nixos/modules/mapui.nix`, in the `hearth-mapd` service `serviceConfig`, add:
```nix
        # The API token for remote access is read from a secret file if present
        # (HEARTH_API_TOKEN). Create /var/lib/hearth/secrets/mapd.env with a line
        # HEARTH_API_TOKEN=<your token> to enable remote API access; without it,
        # the API is localhost-only. See docs.
        EnvironmentFile = [ "-/var/lib/hearth/secrets/mapd.env" ];
```
(The leading `-` makes the file optional, so the service starts even before you create the token file.)

- [ ] **Step 2: Commit and eval on the blade**

```bash
cd "C:/Users/ericc/hearth-wt"
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: hearth-mapd reads its API token from an optional secret env file"
git archive -o "C:/Users/ericc/AppData/Local/Temp/wt.tar" HEAD
scp "C:/Users/ericc/AppData/Local/Temp/wt.tar" operator@192.168.1.64:~/wt.tar
ssh operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && cd ~/hearth-desktop && nix flake check --no-build 2>&1 | tail -4'
```
Expected: `all checks passed!`

---

## Task 4: agent credentials via systemd LoadCredential

**Files:** Modify `nixos/modules/spawn.nix`, `agent/hearth_tools.py`.

- [ ] **Step 1: Load the credentials file into the sandboxed agent unit**

In `nixos/modules/spawn.nix`, in the `hearth-agent@` `serviceConfig` (which merges `config.hearth.sandbox.profile`), add (alongside the existing `ReadWritePaths` override):
```nix
      # Make the user's stored API credentials available to the agent through
      # systemd's credential channel (readable at $CREDENTIALS_DIRECTORY/creds,
      # not world-readable). Optional: if the file is absent, the unit still runs.
      LoadCredential = [ "creds:/var/lib/hearth/secrets/agent-credentials" ];
```

- [ ] **Step 2: Let the http tool resolve a named credential**

In `agent/hearth_tools.py`, update `tool_http_request` so a header value of the form `"cred:NAME"` is replaced with the stored credential NAME read from the credentials directory. Add a helper and use it:
```python
def _resolve_cred(name):
    """Read a stored credential by name from the systemd credentials directory.
    The credentials file is a simple `NAME=VALUE` per line. Returns "" if not
    available, so an agent can never read the raw store directly."""
    creds_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if not creds_dir:
        return ""
    path = os.path.join(creds_dir, "creds")
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        return ""
    return ""
```
In `tool_http_request`, before building the request, resolve any `cred:NAME` header values:
```python
    headers = {k: (_resolve_cred(v[5:]) if isinstance(v, str) and v.startswith("cred:") else v)
               for k, v in (args.get("headers") or {}).items()}
```
(Replace the existing `headers = args.get("headers") or {}` line with the above.)

- [ ] **Step 3: Test the credential resolution locally**

```
cd "C:/Users/ericc/hearth-wt" && py -c "
import os,sys,tempfile
d=tempfile.mkdtemp(); open(os.path.join(d,'creds'),'w').write('github=ghp_secret123\n')
os.environ['CREDENTIALS_DIRECTORY']=d
sys.path.insert(0,'agent'); import hearth_tools as t
assert t._resolve_cred('github')=='ghp_secret123', t._resolve_cred('github')
assert t._resolve_cred('missing')=='', 'missing'
print('cred resolve OK')"
```
Expected: `cred resolve OK`. Also re-run `py agent/hearth_tools.py` -> `hearth-tools self-test OK`.

- [ ] **Step 4: Commit and eval on the blade**

```bash
cd "C:/Users/ericc/hearth-wt"
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: agents resolve named API credentials via systemd LoadCredential (cred:NAME headers)"
git archive -o "C:/Users/ericc/AppData/Local/Temp/wt.tar" HEAD
scp "C:/Users/ericc/AppData/Local/Temp/wt.tar" operator@192.168.1.64:~/wt.tar
ssh operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && cd ~/hearth-desktop && nix flake check --no-build 2>&1 | tail -4'
```
Expected: `all checks passed!`

---

## Task 5: deploy and verify

**Files:** none (deploy + verification).

- [ ] **Step 1: Deploy and switch**

```
cd "C:/Users/ericc/hearth-wt" && git archive -o "C:/Users/ericc/AppData/Local/Temp/wt.tar" HEAD
scp "C:/Users/ericc/AppData/Local/Temp/wt.tar" operator@192.168.1.64:~/wt.tar
ssh operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service 2>/dev/null; cd ~/hearth-desktop && sudo nixos-rebuild switch --flake ~/hearth-desktop#blade 2>&1 | tail -3'
```

- [ ] **Step 2: Verify localhost still works and the /runs endpoint**

```
ssh operator@192.168.1.64 'curl -s localhost:8770/runs | head -c 200; echo; echo "(localhost should work with no token)"'
```
Expected: a `{"runs": [...]}` JSON.

- [ ] **Step 3: Set a token and verify remote auth**

```
ssh operator@192.168.1.64 'echo "HEARTH_API_TOKEN=testtoken123" | sudo tee /var/lib/hearth/secrets/mapd.env >/dev/null && sudo chmod 600 /var/lib/hearth/secrets/mapd.env && sudo chown hearth:hearth /var/lib/hearth/secrets/mapd.env && sudo systemctl restart hearth-mapd && sleep 2; ip=$(ip -4 addr show wlo1 | grep -oE "192[.0-9]+" | head -1); echo "no-token (expect 403):"; curl -s -o /dev/null -w "%{http_code}\n" http://$ip:8770/runs; echo "with-token (expect 200):"; curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer testtoken123" http://$ip:8770/runs'
```
Expected: `403` without the token, `200` with it. (This proves remote control works only with the token.)

- [ ] **Step 4: Verify agent credentials channel**

```
ssh operator@192.168.1.64 'echo "demo=hello-cred" | sudo tee /var/lib/hearth/secrets/agent-credentials >/dev/null && sudo chmod 600 /var/lib/hearth/secrets/agent-credentials && sudo chown hearth:hearth /var/lib/hearth/secrets/agent-credentials; curl -s -X POST localhost:8770/run -H "Content-Type: application/json" -d "{\"name\":\"credtest\",\"model\":\"qwen2.5-coder\",\"prompt\":\"Use the http_request tool to GET https://example.com with a header named X-Demo set to cred:demo, then report the status code, then stop.\"}"; echo; sleep 60; journalctl -u "hearth-agent@credtest-*" --no-pager | grep -iE "status=|tool|Deactivated" | tail -8'
```
Expected: the run executes; the agent uses the `cred:demo` header (resolved server-side to `hello-cred`) without ever seeing the raw secret store. (The example.com call returns a status; the point is the cred channel works and the agent only got the `demo` cred.)

- [ ] **Step 5: Document remote use (ask the user / note)**

Tell the user how to call the API remotely: from any device on the LAN or Tailscale, set the token and hit it, e.g.
`curl -H "Authorization: Bearer <token>" http://<blade-ip>:8770/runs`
and POST to `/run` or `/chat` the same way.

---

## Self-Review

- **Spec coverage:** hearth control API with auth (Task 1) + results endpoint (Task 2) + token supplied securely (Task 3); agents use stored API keys safely via systemd credentials + `cred:NAME` indirection so the model never handles raw secrets (Task 4); verified remotely and on a real agent run (Task 5). The outbound http tool itself shipped in the agent-engine plan.
- **Placeholder scan:** no stubs; every endpoint/helper has complete code; tests are concrete.
- **Type consistency:** `request_allowed(client_ip, auth_header, token)` is defined and called identically in both handlers. `/runs` returns `{runs: [...]}`. `_resolve_cred(name)` reads `$CREDENTIALS_DIRECTORY/creds` written as `NAME=VALUE` lines, matching the `LoadCredential = "creds:..."` mount (the file mounts at `$CREDENTIALS_DIRECTORY/creds`). The `cred:NAME` header convention in the http tool matches `_resolve_cred`.

## Notes / honest limitations
- v1 gives every launched agent access to all configured credentials (via the single credentials file). Per-run scoping (authorizing specific creds per launch) is a follow-up; the `cred:NAME` indirection already keeps raw secrets out of the model's text and out of the repo.
- The API token is a single shared bearer token (fine for a personal homelab over Tailscale). Per-client tokens/scopes are a later refinement.
- An OpenAI-compatible endpoint (so third-party tools speak to hearth) is a separate, optional follow-up.
