# hearth desktop (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Razer Blade into an icy-minimal but usable KDE Plasma desktop with a hotkey-toggled hearth "command center" (live activity, GPU/CPU/RAM stats, agent map).

**Architecture:** A new gated NixOS module brings up KDE Plasma 6 on X11 with SDDM auto-login. `plasma-manager` (home-manager) declares the icy theme, wallpaper, panel, Konsole transparency, and the Meta+A shortcut. The existing `hearth-mapd` web app gains a `/stats` endpoint and a full-screen `/command` page shown by a frameless kiosk browser the shortcut toggles.

**Tech Stack:** NixOS modules, home-manager + plasma-manager, KDE Plasma 6 (X11), SDDM, Python 3 stdlib (hearth-mapd), HTML5 canvas/JS, conky.

**Conventions for this repo (read first):**
- Commits use Eric's identity, no AI attribution: `git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit ...`
- No em dashes in any committed file.
- Nix is validated with `nix flake check --no-build` (CI mirrors this). Full system builds and on-hardware checks happen on the blade: `ssh operator@192.168.1.64`, deploy via `git archive HEAD | ssh ... tar -x` into `~/hearth`, then `sudo nixos-rebuild switch --flake /home/operator/hearth#blade`.
- The blade's WiFi is intermittent; retry SSH on timeout. If a rebuild is interrupted, run `sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service` before the next switch.
- Python web/logic is unit-tested locally on Windows with `py` (Python 3.14), same pattern as existing `--self-test` paths.

---

## File Structure

- Create `nixos/modules/desktop.nix` - KDE/X11/SDDM, apps, conky; gated `hearth.desktop.enable`.
- Modify `nixos/configuration.nix` - import `./modules/desktop.nix`.
- Modify `flake.nix` - add `plasma-manager` input; pass to home-manager.
- Create `nixos/home/operator.nix` - home-manager config for operator: plasma-manager theme, wallpaper, panel, Konsole, Meta+A shortcut, conky.
- Modify `nixos/hosts/blade.nix` - `hearth.desktop.enable = true;` and wire operator home-manager.
- Modify `webui/hearth_mapd.py` - add stats parsers, `/stats` endpoint, `/command` route.
- Create `webui/static/command.html` - the full-screen cockpit page.
- Create `webui/static/assets/` - bundled wallpaper (and any static assets).
- Create `nixos/modules/desktop-assets/hearth-command-toggle.sh` - kiosk show/hide helper (referenced by the shortcut).

---

## Task 1: Add the plasma-manager flake input

**Files:**
- Modify: `flake.nix`

- [ ] **Step 1: Add the input**

In `flake.nix` `inputs`, add:

```nix
    plasma-manager = {
      url = "github:nix-community/plasma-manager";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
    };
```

- [ ] **Step 2: Thread it into outputs**

Change the `outputs` function signature to include it:

```nix
  outputs = { self, nixpkgs, nixos-generators, sops-nix, home-manager, plasma-manager, ... }:
```

- [ ] **Step 3: Make it available to home-manager**

This input is consumed in `nixos/home/operator.nix` (Task 4) via a module import. No flake-level wiring beyond passing it through `specialArgs`. In the `blade` nixosConfiguration (Task 5) add `specialArgs = { inherit plasma-manager; };`.

- [ ] **Step 4: Validate eval (on the blade or any nix host)**

Run: `nix flake check --no-build --show-trace`
Expected: `all checks passed!` (CI will also run this on push).

- [ ] **Step 5: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" \
  commit -am "feat: add plasma-manager flake input"
```

---

## Task 2: Desktop module (KDE Plasma 6, X11, SDDM auto-login, apps)

**Files:**
- Create: `nixos/modules/desktop.nix`
- Modify: `nixos/configuration.nix`

- [ ] **Step 1: Write the module**

Create `nixos/modules/desktop.nix`:

```nix
# desktop.nix: KDE Plasma 6 on X11 with auto-login, for hosts with a screen.
# Gated behind hearth.desktop.enable. The icy theme, wallpaper, panel, and
# shortcuts are declared per-user via plasma-manager (see nixos/home/operator.nix).
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.desktop;
in
{
  options.hearth.desktop = {
    enable = lib.mkEnableOption "the hearth KDE Plasma desktop";
    autoLoginUser = lib.mkOption {
      type = lib.types.str;
      default = "operator";
      description = "User auto-logged into the Plasma session at boot.";
    };
  };

  config = lib.mkIf cfg.enable {
    # X11 + KDE Plasma 6. X11 is the stable path on this Optimus laptop.
    services.xserver.enable = true;
    services.displayManager.sddm.enable = true;
    services.desktopManager.plasma6.enable = true;

    # Boot straight into the desktop, no login prompt (personal laptop).
    services.displayManager.autoLogin = {
      enable = true;
      user = cfg.autoLoginUser;
    };
    services.displayManager.defaultSession = "plasmax11";

    # The display is driven by the Intel iGPU; the NVIDIA card stays for compute
    # (PRIME offload is configured in modules/gpu-nvidia.nix).

    # Daily-use applications and the desktop readout tool.
    environment.systemPackages = with pkgs; [
      firefox
      kdePackages.konsole
      kdePackages.dolphin
      kdePackages.kate
      conky
    ];
  };
}
```

- [ ] **Step 2: Import the module**

In `nixos/configuration.nix`, add `./modules/desktop.nix` to the `imports` list (after `./modules/mapui.nix`).

- [ ] **Step 3: Validate eval**

Run: `nix flake check --no-build --show-trace`
Expected: `all checks passed!` (the module is gated off by default, so this only checks it evaluates).

- [ ] **Step 4: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" \
  commit -am "feat: hearth.desktop module (KDE Plasma 6, X11, SDDM auto-login)"
```

---

## Task 3: Command-center stats parsers (TDD, local)

**Files:**
- Modify: `webui/hearth_mapd.py`
- Test: local `py` invocation (no pytest in repo; use a `--self-test` style block)

- [ ] **Step 1: Add pure parser functions**

In `webui/hearth_mapd.py`, add near the other read helpers:

```python
import shutil
import subprocess


def parse_gpu(csv_text):
    """Parse one line of: nvidia-smi --query-gpu=name,utilization.gpu,
    memory.used,memory.total --format=csv,noheader,nounits"""
    line = (csv_text or "").strip().splitlines()
    if not line:
        return None
    parts = [p.strip() for p in line[0].split(",")]
    if len(parts) < 4:
        return None
    try:
        return {
            "name": parts[0],
            "util_pct": int(float(parts[1])),
            "mem_used_mb": int(float(parts[2])),
            "mem_total_mb": int(float(parts[3])),
        }
    except ValueError:
        return None


def parse_meminfo(text):
    """Parse /proc/meminfo into used/total MB."""
    vals = {}
    for ln in (text or "").splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            vals[k.strip()] = v.strip()
    def kb(key):
        try:
            return int(vals.get(key, "0").split()[0])
        except (ValueError, IndexError):
            return 0
    total = kb("MemTotal")
    available = kb("MemAvailable")
    used = max(total - available, 0)
    return {"used_mb": used // 1024, "total_mb": total // 1024}
```

- [ ] **Step 2: Add a self-test for the parsers**

In `webui/hearth_mapd.py`, extend the CLI: add a `--self-test` flag to `main()` that runs:

```python
def _self_test():
    g = parse_gpu("NVIDIA GeForce RTX 2060, 13, 2538, 6144\n")
    assert g == {"name": "NVIDIA GeForce RTX 2060", "util_pct": 13,
                 "mem_used_mb": 2538, "mem_total_mb": 6144}, g
    m = parse_meminfo("MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\n")
    assert m == {"used_mb": 7812, "total_mb": 16000}, m
    print("hearth-mapd self-test OK")
    return 0
```

Wire it in `main()`: `if args.self_test: return _self_test()` (add the argparse flag `--self-test`).

- [ ] **Step 3: Run the self-test (local, Windows)**

Run: `py webui/hearth_mapd.py --self-test`
Expected: `hearth-mapd self-test OK`

- [ ] **Step 4: Add the live gather + /stats endpoint**

Add a gather function and a route:

```python
def read_stats():
    gpu = None
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout
            gpu = parse_gpu(out)
        except (OSError, subprocess.SubprocessError):
            gpu = None
    mem = None
    try:
        with open("/proc/meminfo") as fh:
            mem = parse_meminfo(fh.read())
    except OSError:
        mem = None
    return {"gpu": gpu, "mem": mem}
```

In the `do_GET` dispatch, add:

```python
        if path == "/stats":
            return self._send(200, json.dumps(read_stats()), "application/json")
        if path == "/command":
            return self._serve_static("command.html", "text/html; charset=utf-8")
```

- [ ] **Step 5: Verify the endpoints serve (local, Windows)**

Run this one-off check (no Ollama/nvidia needed; degrades to nulls):

```bash
py -c "import sys; sys.path.insert(0,'webui'); import hearth_mapd as m; print(m.read_stats())"
```
Expected: prints `{'gpu': None, 'mem': None}` on Windows (no nvidia-smi, no /proc) without raising.

- [ ] **Step 6: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" \
  add webui/hearth_mapd.py && \
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" \
  commit -m "feat: hearth-mapd /stats endpoint and /command route"
```

---

## Task 4: Command-center page (`/command`)

**Files:**
- Create: `webui/static/command.html`
- Create: `webui/static/assets/wallpaper.jpg` (a moody dark-blue still; the animated background is a canvas in the page)

- [ ] **Step 1: Write the page**

Create `webui/static/command.html`. It reuses the icy palette and the agent rendering approach from `index.html`, and adds an animated canvas background plus STATS and ACTIVITY panels. Full file:

```html
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>hearth command</title>
<style>
  :root { color-scheme: dark; }
  html,body { margin:0; height:100%; background:#05080f; color:#cfe6ff;
    font-family: ui-monospace, Menlo, Consolas, monospace; overflow:hidden; }
  #bg { position:fixed; inset:0; z-index:0; }
  #grid { position:fixed; inset:0; z-index:1; display:grid;
    grid-template-columns: 320px 1fr; grid-template-rows: 1fr 180px; gap:14px; padding:14px; }
  .card { background:rgba(8,14,24,0.62); border:1px solid #16324f; border-radius:10px;
    padding:10px 12px; backdrop-filter: blur(3px); overflow:auto; }
  .title { color:#5fd0ff; text-transform:uppercase; letter-spacing:.08em; font-size:12px; margin-bottom:8px; }
  #map { grid-row: 1 / 2; }
  #stats { grid-column: 1 / 2; grid-row: 1 / 2; }
  #activity { grid-column: 1 / 3; grid-row: 2 / 3; }
  .agent { display:inline-block; margin:4px 8px; }
  .bar { height:8px; background:#0e2236; border-radius:4px; overflow:hidden; margin:4px 0 10px; }
  .bar > i { display:block; height:100%; background:linear-gradient(90deg,#1c6,#5fd0ff); }
  .row { font-size:12px; opacity:.9; white-space:nowrap; }
</style></head>
<body>
<canvas id="bg"></canvas>
<div id="grid">
  <div class="card" id="stats"><div class="title">system</div><div id="statsBody">loading...</div></div>
  <div class="card" id="map"><div class="title">agents</div><div id="mapBody"></div></div>
  <div class="card" id="activity"><div class="title">activity</div><div id="actBody"></div></div>
</div>
<script>
const STATE_ICONS={SPAWNING:"*",IDLE:"z",THINKING:"?",TOOL_CALL:"+",WAITING_IO:"~",ERRORED:"!",DONE:"#"};
// animated icy background: drifting particles
const bg=document.getElementById("bg"),bx=bg.getContext("2d");
let parts=[];
function sizeBg(){bg.width=innerWidth;bg.height=innerHeight;
  parts=Array.from({length:90},()=>({x:Math.random()*bg.width,y:Math.random()*bg.height,
    v:0.2+Math.random()*0.6,r:0.5+Math.random()*1.8}));}
addEventListener("resize",sizeBg);sizeBg();
function drawBg(){bx.fillStyle="#05080f";bx.fillRect(0,0,bg.width,bg.height);
  for(const p of parts){p.y-=p.v;if(p.y<0){p.y=bg.height;p.x=Math.random()*bg.width;}
    bx.fillStyle="rgba(95,208,255,0.5)";bx.beginPath();bx.arc(p.x,p.y,p.r,0,7);bx.fill();}
  requestAnimationFrame(drawBg);}
requestAnimationFrame(drawBg);
// data
const agents=new Map();
function renderAgents(){document.getElementById("mapBody").innerHTML=
  [...agents.values()].sort((a,b)=>a.agent_id<b.agent_id?-1:1)
  .map(a=>`<span class="agent">${STATE_ICONS[a.state]||"?"} ${a.agent_id}<br><small>${(a.state||"").toLowerCase()}</small></span>`).join("")||"(no agents)";}
async function refreshStats(){try{const s=await(await fetch("/stats")).json();
  const g=s.gpu,m=s.mem;let h="";
  if(g){h+=`GPU ${g.name}<div class="bar"><i style="width:${g.util_pct}%"></i></div>`+
    `VRAM ${g.mem_used_mb}/${g.mem_total_mb} MB<div class="bar"><i style="width:${100*g.mem_used_mb/g.mem_total_mb}%"></i></div>`;}
  else h+="GPU unavailable<br>";
  if(m)h+=`RAM ${m.used_mb}/${m.total_mb} MB<div class="bar"><i style="width:${100*m.used_mb/m.total_mb}%"></i></div>`;
  document.getElementById("statsBody").innerHTML=h;}catch(e){}}
setInterval(refreshStats,2000);refreshStats();
function logLine(t){const b=document.getElementById("actBody");
  b.innerHTML=`<div class="row">${t}</div>`+b.innerHTML.split("</div>").slice(0,40).join("</div>");}
const es=new EventSource("/events");
es.onmessage=m=>{const d=JSON.parse(m.data);
  if(d.type==="snapshot"){agents.clear();(d.agents||[]).forEach(a=>agents.set(a.agent_id,a));}
  else if(d.type==="event"){agents.set(d.agent_id,{agent_id:d.agent_id,state:d.state});
    logLine(`${(d.ts||"").slice(11,19)}  ${d.agent_id}  ${d.state.toLowerCase()}  ${d.detail||""}`);}
  renderAgents();};
fetch("/state").then(r=>r.json()).then(j=>{(j.agents||[]).forEach(a=>agents.set(a.agent_id,a));renderAgents();});
</script></body></html>
```

- [ ] **Step 2: Add a placeholder wallpaper asset**

Create `webui/static/assets/` and add a moody dark-blue image `wallpaper.jpg` (used by the KDE wallpaper in Task 5; the command page uses the canvas animation, not this file). If no image is on hand, commit a 1x1 dark-blue placeholder and replace during on-hardware polish.

- [ ] **Step 3: Verify the page serves with the existing server test harness (local)**

Reuse the existing in-process server test approach: start `hearth_mapd.make_server` on a temp DB, fetch `/command`, assert it returns 200 and contains `hearth command`.

Run a one-off:

```bash
py -c "import sys,threading,time,urllib.request,tempfile,os; sys.path.insert(0,'webui'); import hearth_mapd as m; \
d=tempfile.mkdtemp(); srv=m.make_server('127.0.0.1',8795,os.path.join(d,'a.db'),'webui/static'); \
threading.Thread(target=srv.serve_forever,daemon=True).start(); time.sleep(0.3); \
print('command ok' if 'hearth command' in urllib.request.urlopen('http://127.0.0.1:8795/command',timeout=5).read().decode() else 'FAIL'); srv.shutdown()"
```
Expected: `command ok`

- [ ] **Step 4: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" \
  add webui/static/command.html webui/static/assets && \
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" \
  commit -m "feat: command-center page with animated bg, stats, activity, agent map"
```

---

## Task 5: Operator home-manager config (plasma-manager theme + shortcut) and kiosk toggle

**Files:**
- Create: `nixos/home/operator.nix`
- Create: `nixos/modules/desktop-assets/hearth-command-toggle.sh`
- Modify: `nixos/hosts/blade.nix`
- Modify: `flake.nix` (blade specialArgs, already added in Task 1 Step 3)

- [ ] **Step 1: Write the kiosk toggle script**

Create `nixos/modules/desktop-assets/hearth-command-toggle.sh`:

```bash
#!/usr/bin/env bash
# Toggle the hearth command center: a frameless Firefox kiosk window pointed at
# the local command page. If it is open, close it; otherwise open it.
set -euo pipefail
URL="http://localhost:8770/command"
if pgrep -f "hearth-command-kiosk" >/dev/null; then
  pkill -f "hearth-command-kiosk" || true
else
  firefox --kiosk --new-instance --class hearth-command-kiosk "$URL" \
    --profile "$HOME/.hearth-command-profile" >/dev/null 2>&1 &
fi
```

- [ ] **Step 2: Write the home-manager config**

Create `nixos/home/operator.nix`:

```nix
# operator.nix: home-manager config for the operator desktop user. Declares the
# icy KDE theme, moody wallpaper, slim panel with sensors, transparent Konsole,
# conky desktop readout, and the Meta+A command-center toggle.
{ config, lib, pkgs, plasma-manager, ... }:
let
  toggle = pkgs.writeShellApplication {
    name = "hearth-command-toggle";
    runtimeInputs = [ pkgs.firefox pkgs.procps ];
    text = builtins.readFile ../modules/desktop-assets/hearth-command-toggle.sh;
  };
in
{
  imports = [ plasma-manager.homeManagerModules.plasma-manager ];

  home.stateVersion = "24.11";
  home.packages = [ toggle ];

  programs.plasma = {
    enable = true;
    workspace = {
      colorScheme = "BreezeDark";
      theme = "breeze-dark";
      wallpaper = "${../../webui/static/assets/wallpaper.jpg}";
    };
    # Slim top panel with sensors and a clock.
    panels = [{
      location = "top";
      height = 28;
      widgets = [
        "org.kde.plasma.kickoff"
        "org.kde.plasma.pager"
        "org.kde.plasma.panelspacer"
        { systemMonitor = { title = "CPU"; }; }
        "org.kde.plasma.systemtray"
        "org.kde.plasma.digitalclock"
      ];
    }];
    shortcuts = {
      "hearth"."toggle-command" = "Meta+A";
    };
    # Transparent, blurred Konsole with the icy palette.
    configFile."konsolerc"."Desktop Entry"."DefaultProfile" = "hearth.profile";
  };

  # Bind Meta+A to the toggle via a custom shortcut (kglobalaccel/khotkeys).
  programs.plasma.hotkeys.commands."hearth-command" = {
    name = "Toggle hearth command center";
    key = "Meta+A";
    command = "${toggle}/bin/hearth-command-toggle";
  };

  # conky desktop readout, autostarted.
  xdg.configFile."conky/hearth.conf".text = ''
    conky.config = {
      own_window = true, own_window_type = 'desktop', own_window_transparent = true,
      alignment = 'bottom_left', gap_x = 30, gap_y = 40, update_interval = 2,
      default_color = 'A0C8FF', font = 'monospace:size=10',
    };
    conky.text = [[
    hearth  ''${nodename}
    OS      NixOS  ''${kernel}
    Up      ''${uptime}
    CPU     ''${cpu}%   RAM ''${mem}/''${memmax}
    GPU     ''${exec nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null}
    Disk    ''${fs_used /} / ''${fs_size /}
    ]];
  '';
  systemd.user.services.hearth-conky = {
    Unit.Description = "hearth conky desktop readout";
    Install.WantedBy = [ "graphical-session.target" ];
    Service.ExecStart = "${pkgs.conky}/bin/conky -c %h/.config/conky/hearth.conf";
  };
}
```

Note: plasma-manager option names (panel widget configs, hotkeys) can shift between releases. If `nix flake check` reports an unknown option, check the plasma-manager version's docs (`programs.plasma`) and adjust the offending attribute. The Konsole transparency profile (`hearth.profile`) is created as a profile file via `programs.konsole.profiles` if available in the pinned plasma-manager; otherwise drop a profile file with `home.file`.

- [ ] **Step 3: Wire operator home-manager into the blade host**

In `nixos/hosts/blade.nix` add:

```nix
  hearth.desktop.enable = true;

  home-manager.useGlobalPkgs = true;
  home-manager.useUserPackages = true;
  home-manager.users.operator = import ../home/operator.nix;
  home-manager.extraSpecialArgs = { inherit plasma-manager; };
```

And ensure `blade.nix` takes `plasma-manager` as an arg: change its header to `{ plasma-manager, ... }:`.

- [ ] **Step 4: Pass plasma-manager to the blade config**

In `flake.nix`, the `blade` nixosConfiguration: add `specialArgs = { inherit plasma-manager; };` so `blade.nix` receives it.

- [ ] **Step 5: Validate eval**

Run: `nix flake check --no-build --show-trace`
Expected: `all checks passed!` If a plasma-manager option errors, fix per the note in Step 2, then re-run.

- [ ] **Step 6: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" \
  add nixos/home/operator.nix nixos/modules/desktop-assets/hearth-command-toggle.sh nixos/hosts/blade.nix flake.nix && \
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" \
  commit -m "feat: operator KDE theme, conky readout, and Meta+A command toggle"
```

---

## Task 6: Deploy to the blade and verify on hardware

**Files:** none (deployment + verification)

- [ ] **Step 1: Copy the repo to the blade**

```bash
git archive --format=tar HEAD | ssh operator@192.168.1.64 'rm -rf ~/hearth && mkdir -p ~/hearth && tar -x -C ~/hearth && echo COPIED'
```
Expected: `COPIED`

- [ ] **Step 2: Build the new config first (catch errors before activating)**

```bash
ssh operator@192.168.1.64 'cd ~/hearth && nix build .#nixosConfigurations.blade.config.system.build.toplevel --no-link 2>&1 | tail -n 5'
```
Expected: completes with no error (KDE/SDDM pulled from cache; first build may take several minutes).

- [ ] **Step 3: Activate for next boot**

```bash
ssh operator@192.168.1.64 'sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service 2>/dev/null; sudo nixos-rebuild boot --flake /home/operator/hearth#blade 2>&1 | tail -n 3'
```
Expected: `Done. The new configuration is /nix/store/...`

- [ ] **Step 4: Reboot and reconnect**

```bash
ssh operator@192.168.1.64 'sudo reboot' || true
# wait ~60-90s, then:
ssh operator@192.168.1.64 'systemctl is-system-running; systemctl is-active display-manager'
```
Expected: `running` (or `degraded` is acceptable if a non-critical unit failed) and `active` for the display manager.

- [ ] **Step 5: Verify the graphical session and the command center (on the laptop screen)**

Ask the user to look at the laptop screen and confirm:
- KDE desktop appears (auto-logged in), mouse works.
- Wallpaper is the moody blue; conky readout shows in the corner; the top panel shows sensors + clock.
- Pressing Meta+A opens the full-screen command center (animated bg, agents, GPU/CPU/RAM stats, activity); Meta+A again closes it.

From SSH, confirm the backing services:

```bash
ssh operator@192.168.1.64 'for u in display-manager hearth-mapd ollama; do printf "%-16s %s\n" "$u" "$(systemctl is-active $u)"; done; curl -s -m5 http://localhost:8770/stats'
```
Expected: services `active`; `/stats` returns real GPU + RAM numbers.

- [ ] **Step 6: If first graphical boot fails (fallback)**

If KDE/X does not start, the system still reaches SSH. Roll back:

```bash
ssh operator@192.168.1.64 'sudo nixos-rebuild switch --rollback'
```
Then read `journalctl -b -u display-manager` and the X log to diagnose the NVIDIA/X issue before retrying.

- [ ] **Step 7: Commit any fixes found during on-hardware verification**

Commit with Eric's identity, then re-deploy (Steps 1-5) until the screen shows the desktop and the command center works.

---

## Self-Review

- **Spec coverage:** KDE/X11/SDDM auto-login (Task 2), icy theme + wallpaper + panel + Konsole + conky readout (Task 5), animated wallpaper (canvas in command page Task 4; KDE wallpaper Task 5 with static fallback per spec risk note), command center page + stats + map (Tasks 3, 4), Meta+A toggle (Task 5), GPU/CPU/RAM stats (Task 3), safety/rollback (Task 6 Step 6), local + on-hardware testing (Tasks 3-6). All spec sections map to a task.
- **Placeholder scan:** wallpaper asset is a real (possibly placeholder) committed file with a stated replacement step, not a requirement gap. No "TBD"/"handle errors" hand-waving; parsers and endpoints have complete code.
- **Type consistency:** `parse_gpu` returns `{name, util_pct, mem_used_mb, mem_total_mb}` used verbatim in `command.html` (`g.name`, `g.util_pct`, `g.mem_used_mb`, `g.mem_total_mb`); `parse_meminfo` returns `{used_mb, total_mb}` used as `m.used_mb`, `m.total_mb`; `read_stats` returns `{gpu, mem}` consumed as `s.gpu`, `s.mem`. STATE_ICONS keys match `agent/hearth_state.py`.

## Notes / known risks

- plasma-manager option names are the most likely source of eval errors; Task 5 Step 2 documents how to adjust. Catch them with `nix flake check` before deploying.
- Animated KDE wallpaper is the fiddliest visual; the canvas animation lives in the command center regardless, and the desktop wallpaper falls back to a static moody-blue image (spec risk note).
- First graphical boot on the Optimus GPU is the main on-hardware risk; Task 6 Step 6 is the rollback path.
