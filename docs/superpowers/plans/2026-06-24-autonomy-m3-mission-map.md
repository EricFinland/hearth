# Autonomy Milestone 1, Plan 3: Mission-Control Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Turn the cockpit's flat agent list into mission control: render each swarm as a live tree (manager → specialists) from the `agent_meta` lineage, with per-agent state, and add a one-click "launch mission" (swarm) control.

**Architecture:** A new `mapd` reader `read_tree(db)` joins `agent_meta` (lineage) with `agent_state` (current state) and serves it at `GET /tree`. `command.html` polls `/tree`, groups nodes by their top-level manager, and draws nested trees with state icons; the launch panel gets a "launch mission" button that posts `swarm: true` to `/run`. All build + unit-test offline; deploy/verify on the blade is deferred (the box is intermittently offline).

**Tech Stack:** Python 3 stdlib (mapd) + vanilla JS (command.html). Tests via `python webui/hearth_mapd.py --self-test`. Dev Windows (`python`). Plan 3 of 4 in Autonomy Milestone 1.

**Commit identity:** `git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "<msg>"`. No AI attribution. No em-dashes.

**Working dir:** `C:/Users/ericc/hearth-wt` (branch worktree-desktop).

---

### Task MM-1: `read_tree` + `/tree` endpoint (`webui/hearth_mapd.py`)

- [ ] **Step 1: failing self-test.** In `_self_test()`, before the final `print(...)`, add:
```python
    import tempfile as _tft
    tdb = os.path.join(_tft.mkdtemp(prefix="hearth-tree-"), "t.db")
    con = sqlite3.connect(tdb)
    con.executescript(SCHEMA)
    con.execute("CREATE TABLE IF NOT EXISTS agent_meta (agent_id TEXT PRIMARY KEY, parent_id TEXT, kind TEXT, goal TEXT, created_at TEXT)")
    con.execute("INSERT INTO agent_meta VALUES (?,?,?,?,?)", ("mgr", None, "manager", "do it", _now_iso()))
    con.execute("INSERT INTO agent_meta VALUES (?,?,?,?,?)", ("mgr-s1", "mgr", "specialist", "part one", _now_iso()))
    con.execute("INSERT INTO agent_state (agent_id, state, detail, updated_at) VALUES (?,?,?,?)", ("mgr", "WAITING_IO", "2 running", _now_iso()))
    con.commit(); con.close()
    nodes = {n["agent_id"]: n for n in read_tree(tdb)}
    assert nodes["mgr"]["kind"] == "manager" and nodes["mgr"]["state"] == "WAITING_IO", nodes
    assert nodes["mgr-s1"]["parent_id"] == "mgr" and nodes["mgr-s1"]["state"] is None, nodes
```

- [ ] **Step 2: run `python webui/hearth_mapd.py --self-test`** -> NameError on `read_tree`.

- [ ] **Step 3: add `read_tree`** (near `read_snapshot`):
```python
def read_tree(db):
    """Lineage nodes (agent_meta) joined with current state, for the mission map."""
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(SCHEMA)
        con.execute("CREATE TABLE IF NOT EXISTS agent_meta ("
                    "agent_id TEXT PRIMARY KEY, parent_id TEXT, kind TEXT, goal TEXT, created_at TEXT)")
        cur = con.execute(
            "SELECT m.agent_id, m.parent_id, m.kind, m.goal, m.created_at, s.state, s.detail "
            "FROM agent_meta m LEFT JOIN agent_state s ON s.agent_id = m.agent_id "
            "ORDER BY m.created_at")
        cols = ["agent_id", "parent_id", "kind", "goal", "created_at", "state", "detail"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
        return rows
    except sqlite3.Error:
        return []
```

- [ ] **Step 4: add the route.** In `do_GET`, before the final 404:
```python
        if path == "/tree":
            return self._send(200, json.dumps({"nodes": read_tree(self.db)}), "application/json")
```

- [ ] **Step 5: run `python webui/hearth_mapd.py --self-test`** -> `hearth-mapd self-test OK`.

- [ ] **Step 6: commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add webui/hearth_mapd.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: mapd /tree endpoint (swarm lineage joined with live state)"
```

---

### Task MM-2: mission tree + launch in the cockpit (`webui/static/command.html`)

- [ ] **Step 1: add a missions card.** Inside `#grid`, after the `#map` card, add:
```html
  <div class="card" id="missions" style="display:none;"><div class="title">missions</div><div id="missionsBody"></div></div>
```
(If the grid layout needs a row, append an `auto` track to `#grid`'s `grid-template-rows` as done for the approvals card; otherwise the card flows under the map.)

- [ ] **Step 2: add a launch-mission button.** In the `#launch` card's button row (next to "open session" / "run in background"), add:
```html
      <button id="agMission" style="flex:1;background:#5fd0ff;color:#04150c;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;">launch mission</button>
```

- [ ] **Step 3: add the JS** at the end of the `<script>` block:
```javascript
// ---- mission control: render the swarm tree from /tree ----
const STATE_DOT={SPAWNING:"*",IDLE:"z",THINKING:"?",TOOL_CALL:"+",WAITING_IO:"~",WAITING_APPROVAL:"!?",ERRORED:"!",DONE:"#"};
async function refreshMissions(){
  try{
    const j=await(await fetch("/tree")).json();
    const nodes=j.nodes||[];
    const card=document.getElementById("missions");
    card.style.display=nodes.length?"block":"none";
    const byParent={};
    nodes.forEach(n=>{(byParent[n.parent_id||""]=byParent[n.parent_id||""]||[]).push(n);});
    const line=n=>`<div style="margin:2px 0;"><b style="color:#5fd0ff">${STATE_DOT[n.state]||"?"}</b> ${esc(n.agent_id)} <small style="color:#8a93a0">${esc(n.kind||"")} ${esc((n.state||"").toLowerCase())}</small>`+
      `${esc(n.goal? " - "+n.goal.slice(0,60):"")}</div>`;
    const roots=nodes.filter(n=>!n.parent_id);
    document.getElementById("missionsBody").innerHTML=roots.map(r=>
      `<div style="border-left:3px solid #16324f;padding-left:8px;margin:6px 0;">${line(r)}`+
      `<div style="margin-left:14px;">${(byParent[r.agent_id]||[]).map(line).join("")}</div></div>`).join("")||"(no missions yet)";
  }catch(e){}
}
setInterval(refreshMissions,3000); refreshMissions();
document.getElementById("agMission").onclick=async()=>{
  const name=document.getElementById("agName").value.trim()||"mission";
  const model=document.getElementById("agModel").value;
  const prompt=document.getElementById("agTask").value.trim();
  const msg=document.getElementById("agMsg");
  if(!prompt){msg.textContent="enter a goal";return;}
  msg.textContent="launching mission...";
  try{const r=await fetch("/run",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name,model,prompt,mode:"bypass",swarm:true})});
    const j=await r.json();
    if(j.queued){msg.textContent="mission launched: "+j.queued+" (watch missions)";document.getElementById("agTask").value="";}
    else msg.textContent="error: "+(j.error||"unknown");
  }catch(e){msg.textContent="error: "+e;}};
```
(`esc` already exists in the file from the session console. Reuse it.)

- [ ] **Step 4: validate.**
```bash
python -c "h=open('webui/static/command.html',encoding='utf-8').read(); assert 'refreshMissions' in h and 'id=\"missions\"' in h and 'agMission' in h and '/tree' in h; assert h.count('<script')==h.count('</script>'); assert h.count('id=\"agLaunch\"')==1; print('mission map OK')"
```
Expected: `mission map OK`.

- [ ] **Step 5: commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add webui/static/command.html
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: cockpit mission tree (live swarm lineage) + launch-mission button"
```

---

### Task MM-3: deploy + verify (DEFERRED until the blade is reachable)

- [ ] **Step 1:** local gate: `python webui/hearth_mapd.py --self-test` and the other module self-tests pass.
- [ ] **Step 2:** deploy (`git archive` + scp + `nixos-rebuild switch`).
- [ ] **Step 3:** open the cockpit, launch a mission, confirm the missions card shows the manager with its specialists nested and their states update live; `curl localhost:8770/tree` returns the nodes.
- [ ] **Step 4:** push (already pushed incrementally; ensure main is current).

---

## Self-Review
- Coverage: `/tree` reader + endpoint (MM-1); cockpit tree render + mission launch (MM-2); blade verify (MM-3, deferred).
- Placeholders: none in MM-1/MM-2 (complete code); MM-3 is an explicit deferred verification gated on connectivity.
- Consistency: `read_tree` joins `agent_meta` (from SW-1) + `agent_state`; `/tree` returns `{nodes}`; the UI groups by `parent_id` and reuses the existing `esc`; the launch-mission button posts `swarm:true` (consumed by SW-3's `/run`). State dots match the closed state set incl. WAITING_APPROVAL.
