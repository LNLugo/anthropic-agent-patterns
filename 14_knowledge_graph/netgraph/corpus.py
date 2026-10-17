"""The corpus: network logs, and the runbooks and policy pages they point to.

Synthetic and deterministic (seeded). Four logs and an asset inventory a bank's network services team
would hold (syslog carries the routers and the servers), in the key=value style of the article this module is modelled on:

    vpn.log       2026-10-14 08:01:05 VPN_LOGIN_FAILED user=john.doe ip=203.0.113.45 reason=bad_password
    firewall.log  2026-10-14 08:09:40 FIREWALL_DENY src=203.0.113.45 dst=10.1.1.10 dport=443 policy=GEO_BLOCK_INBOUND
    syslog.log    2026-10-14 02:00:12 CONFIG_COMMIT host=core-rtr-02 change=CHG-4471 by=net.admin1
    auth.log      2026-10-14 03:12:44 SUDO_ESCALATION host=srv-db-01 ip=10.50.2.8 user=svc_backup
    inventory.log 2026-10-01 00:00:00 ASSET host=srv-db-01 ip=10.50.2.8 role=database owner=dba-team

plus Markdown runbooks, policy pages and change records with front matter naming
who may read them and which entities they apply to.

Scenario lines are planted among about 1,200 lines of background traffic and
tagged, so every question in questions.yaml names the exact lines and pages that
answer it. The tags never reach the corpus files; they go only into the questions.

    python -m netgraph.corpus          # rewrite fixtures/ (a test checks it is reproducible)
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[1]
FIXTURES = HERE / "fixtures"
DAY = datetime(2026, 10, 14)

# Who may read each source. Auth logs and the incident playbook are the security team's.
SOURCES = {
    "vpn": ("netops", "security"),
    "firewall": ("netops", "security"),
    "syslog": ("netops", "security"),
    "auth": ("security",),
    "inventory": ("netops", "security"),
}
USERS = {  # the people asking, and their groups
    "netops_analyst": ["netops"],
    "secops_analyst": ["netops", "security"],
    "contractor": [],
}

ENTITY_FIELDS = {"user": "user", "by": "user", "ip": "ip", "src": "ip", "dst": "ip", "neighbor": "ip",
                 "host": "host", "policy": "policy", "change": "change"}
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ([A-Z_]+)((?: [a-z_]+=\S+)*)$")
FIELD = re.compile(r" ([a-z_]+)=(\S+)")


# ---------------------------------------------------------------- the model

@dataclass(frozen=True)
class Item:
    """One retrievable unit: a log line or a section of a page."""

    key: str                       # "vpn:12" or "doc:RB-VPN-LOCKOUT#2"
    kind: str                      # "event" or "doc"
    text: str                      # what is embedded and shown
    readers: frozenset[str]        # groups that may read it
    entities: tuple[str, ...]      # "user:john.doe", "ip:203.0.113.45", ...
    attrs: dict = field(default_factory=dict, compare=False, hash=False)


def parse_line(source: str, line_no: int, line: str) -> Item:
    """A log line to an Item. Fields are parsed by a pattern, not a model: exact and free."""
    m = LINE.match(line.strip())
    if not m:
        raise ValueError(f"{source}:{line_no}: not a log line: {line[:80]!r}")
    ts, event_type, rest = m.groups()
    fields = dict(FIELD.findall(rest))
    entities = tuple(sorted({f"{ENTITY_FIELDS[k]}:{v}" for k, v in fields.items() if k in ENTITY_FIELDS}))
    return Item(f"{source}:{line_no}", "event", f"[{source.upper()}] {line.strip()}",
                frozenset(SOURCES[source]), entities,
                {"source": source, "line": line_no, "ts": ts, "type": event_type, **fields})


FRONT = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


def parse_doc(path: Path) -> list[Item]:
    """A page to one Item per section. Its entities come from its front matter (applies_to),
    written by the page's owner: a page states what it is about."""
    m = FRONT.match(path.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{path.name}: no front matter")
    meta, body = yaml.safe_load(m.group(1)), m.group(2)
    sections = [s.strip() for s in re.split(r"\n(?=## )", body) if s.strip()]
    entities = tuple(sorted(meta.get("applies_to", ())))
    return [Item(f"doc:{meta['id']}#{i}", "doc", f"{meta['title']}\n{s}", frozenset(meta["readers"]), entities,
                 {"doc": meta["id"], "title": meta["title"], "section": i})
            for i, s in enumerate(sections, 1)]


def load(folder: Path = FIXTURES) -> list[Item]:
    items = []
    for source in SOURCES:
        lines = (folder / "logs" / f"{source}.log").read_text(encoding="utf-8").splitlines()
        items += [parse_line(source, n, line) for n, line in enumerate(lines, 1) if line.strip()]
    for path in sorted((folder / "docs").glob("*.md")):
        items += parse_doc(path)
    return items


def load_questions(folder: Path = FIXTURES) -> list[dict]:
    return yaml.safe_load((folder / "questions.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the generator

def _ts(hh: int, mm: int, ss: int = 0, day: int = 0) -> datetime:
    return DAY + timedelta(days=day, hours=hh, minutes=mm, seconds=ss)


def _fmt(t: datetime, event: str, **fields) -> str:
    return f"{t:%Y-%m-%d %H:%M:%S} {event}" + "".join(f" {k}={v}" for k, v in fields.items())


STAFF = ["alice.smith", "bob.jones", "charlie.brown", "dana.white", "eli.ross", "fatima.ali", "george.kim",
         "hana.sato", "ivan.petrov", "julia.costa", "kofi.mensah", "lena.berg", "maria.chen", "raj.patel",
         "john.doe", "nina.lopez", "omar.haddad", "paula.reyes"]
HOSTS = ["srv-web-01", "srv-web-02", "srv-app-01", "srv-app-02", "srv-db-01", "srv-db-02", "srv-files-01"]
HOST_IP = {"srv-web-01": "10.40.1.11", "srv-web-02": "10.40.1.12", "srv-app-01": "10.45.1.21",
           "srv-app-02": "10.45.1.22", "srv-db-01": "10.50.2.8", "srv-db-02": "10.50.2.9",
           "srv-files-01": "10.60.3.5"}
EXTERNAL = ["140.82.112.4", "151.101.1.69", "172.217.14.206", "104.16.132.229", "13.107.42.14"]


def _scenarios() -> list[tuple[str, datetime, str, set[str]]]:
    """(source, time, line, tags). Each tag names a question that this line answers."""
    s = []

    def add(source, t, tags, event, **f):
        s.append((source, t, _fmt(t, event, **f), set(tags)))

    # Q1: one external address tries several staff accounts, gets john.doe locked out, is geo-blocked.
    bad = "203.0.113.45"
    for i, (user, mm) in enumerate([("john.doe", 1), ("john.doe", 3), ("maria.chen", 4), ("john.doe", 6),
                                    ("raj.patel", 7)]):
        add("vpn", _ts(8, mm, 5 + i * 7), ["q1"], "VPN_LOGIN_FAILED", user=user, ip=bad, reason="bad_password")
    add("vpn", _ts(8, 6, 30), ["q1"], "VPN_ACCOUNT_LOCKED", user="john.doe", ip=bad, policy="VPN_LOCKOUT")
    add("firewall", _ts(8, 9, 40), ["q1"], "FIREWALL_DENY", src=bad, dst="10.1.1.10", dport=443,
        policy="GEO_BLOCK_INBOUND")

    # Q2: alice.smith is on VPN; her address is denied by the database segment policy.
    add("vpn", _ts(9, 14, 2), ["q2"], "VPN_LOGIN_SUCCESS", user="alice.smith", ip="10.20.15.21")
    for mm in (16, 18, 23):
        add("firewall", _ts(9, mm, 11), ["q2"], "FIREWALL_DENY", src="10.20.15.21", dst=HOST_IP["srv-db-02"],
            dport=1433, policy="DB_SEGMENT_DENY")

    # Q3: an overnight change on a core router, then its BGP neighbor and an interface flap.
    add("syslog", _ts(2, 0, 12), ["q3"], "CONFIG_COMMIT", host="core-rtr-02", change="CHG-4471", by="net.admin1")
    add("syslog", _ts(2, 3, 41), ["q3"], "BGP_NEIGHBOR_DOWN", host="core-rtr-02", neighbor="10.255.0.2",
        reason="hold_timer_expired")
    add("syslog", _ts(2, 4, 58), ["q3"], "INTERFACE_FLAP", host="core-rtr-02", interface="Ethernet1/3")
    add("syslog", _ts(2, 9, 17), ["q3"], "BGP_NEIGHBOR_DOWN", host="core-rtr-02", neighbor="10.255.0.2",
        reason="hold_timer_expired")
    add("syslog", _ts(2, 9, 30), ["q3"], "INTERFACE_FLAP", host="core-rtr-02", interface="Ethernet1/3")

    # Q4 and Q5: a database server escalates a service account, then calls out to a known-bad address.
    db = HOST_IP["srv-db-01"]
    add("auth", _ts(3, 12, 44), ["q4"], "SUDO_ESCALATION", host="srv-db-01", ip=db, user="svc_backup")
    add("auth", _ts(3, 13, 2), ["q4"], "NEW_SSH_KEY", host="srv-db-01", ip=db, user="svc_backup")
    for mm in (20, 35, 50):
        add("firewall", _ts(3, mm, 7), ["q4", "q5"], "FIREWALL_DENY", src=db, dst="45.33.32.156", dport=4444,
            policy="OUTBOUND_BLOCK")

    # Q6: a disk fills on a web server. Ordinary traffic from the same host sits around it.
    for mm in (5, 40):
        add("auth", _ts(10, mm, 0), [], "SESSION_OPEN", host="srv-web-02", ip=HOST_IP["srv-web-02"], user="deploy")
    add("syslog", _ts(10, 12, 0), ["q6"], "DISK_FULL", host="srv-web-02", partition="/var/log", threshold="95%")
    add("syslog", _ts(10, 47, 0), ["q6"], "DISK_FULL", host="srv-web-02", partition="/var/log", threshold="99%")

    # Q7: bob.jones mistypes his password from home.
    for mm in (31, 32, 34):
        add("vpn", _ts(7, mm, 9), ["q7"], "VPN_LOGIN_FAILED", user="bob.jones", ip="98.114.7.20",
            reason="bad_password")

    # Q8: a port scan from an internal address, caught three times.
    for mm, dst in ((12, "10.1.1.20"), (14, "10.1.1.21"), (15, "10.1.1.22")):
        add("firewall", _ts(13, mm, 0), ["q8"], "FIREWALL_DENY", src="10.70.9.33", dst=dst, dport=22,
            policy="PORT_SCAN_DETECTED")
    return s


def _inventory() -> list[tuple[str, datetime, str, set[str]]]:
    """The asset inventory, which network operations can read: which address is which host.
    Without it, a host named in a question cannot be joined to the firewall lines that name
    only its address, unless the asker can read the auth log."""
    roles = {"web": "web", "app": "application", "db": "database", "files": "file share"}
    out = []
    for i, (h, ip) in enumerate(sorted(HOST_IP.items())):
        t = datetime(2026, 10, 1) + timedelta(minutes=i)
        out.append(("inventory", t, _fmt(t, "ASSET", host=h, ip=ip, role=roles[h.split("-")[1]].replace(" ", "_")),
                    set()))
    return out


def _held_out() -> list[tuple[str, datetime, str, set[str]]]:
    """Scenarios for the held-out questions, written after the retriever's design was fixed
    (retrieve.py at the commit that added them). Nothing in the retriever was changed after."""
    s = []

    def add(source, t, tags, event, **f):
        s.append((source, t, _fmt(t, event, **f), set(tags)))

    # h1: a database server's backup to the file share is denied by a segment policy.
    for mm in (30, 31):
        add("syslog", _ts(1, mm, 0), ["h1"], "BACKUP_FAILED", host="srv-db-02", reason="timeout")
    add("firewall", _ts(1, 30, 40), ["h1"], "FIREWALL_DENY", src=HOST_IP["srv-db-02"], dst=HOST_IP["srv-files-01"],
        dport=445, policy="FILESHARE_SEGMENT_DENY")
    add("firewall", _ts(1, 31, 40), ["h1"], "FIREWALL_DENY", src=HOST_IP["srv-db-02"], dst=HOST_IP["srv-files-01"],
        dport=445, policy="FILESHARE_SEGMENT_DENY")
    # h2: a change on the edge router, then its BGP session drops.
    add("syslog", _ts(4, 0, 30), ["h2", "h5"], "CONFIG_COMMIT", host="edge-rtr-01", change="CHG-4502", by="net.admin2")
    add("syslog", _ts(4, 2, 10), ["h2"], "BGP_NEIGHBOR_DOWN", host="edge-rtr-01", neighbor="192.0.2.1",
        reason="prefix_limit_exceeded")
    add("syslog", _ts(4, 6, 45), ["h2"], "BGP_NEIGHBOR_DOWN", host="edge-rtr-01", neighbor="192.0.2.1",
        reason="prefix_limit_exceeded")
    # h3: an outside address tries two accounts and is geo-blocked.
    for user, mm in (("kofi.mensah", 12), ("lena.berg", 13), ("kofi.mensah", 15)):
        add("vpn", _ts(17, mm, 3), ["h3"], "VPN_LOGIN_FAILED", user=user, ip="198.51.100.77", reason="bad_password")
    add("firewall", _ts(17, 16, 0), ["h3"], "FIREWALL_DENY", src="198.51.100.77", dst="10.1.1.10", dport=443,
        policy="GEO_BLOCK_INBOUND")
    # h4: paula.reyes connects and is stopped at the database segment.
    add("vpn", _ts(15, 2, 0), ["h4"], "VPN_LOGIN_SUCCESS", user="paula.reyes", ip="10.20.9.40")
    for mm in (4, 9):
        add("firewall", _ts(15, mm, 30), ["h4"], "FIREWALL_DENY", src="10.20.9.40", dst=HOST_IP["srv-db-01"],
            dport=1433, policy="DB_SEGMENT_DENY")
    # h6 and h7: a monitoring account escalates on an application server, which then calls out.
    app = HOST_IP["srv-app-02"]
    add("auth", _ts(22, 40, 0), ["h6"], "SUDO_ESCALATION", host="srv-app-02", ip=app, user="svc_monitor")
    for mm in (45, 52):
        add("firewall", _ts(22, mm, 0), ["h6", "h7"], "FIREWALL_DENY", src=app, dst="45.33.32.156", dport=4444,
            policy="OUTBOUND_BLOCK")
    return s


def _background(rng: random.Random) -> list[tuple[str, datetime, str, set[str]]]:
    """Ordinary traffic: logins, allows, routine denies, router health. Each staff member has
    their own home address, so no address but the attacker's fails for several users."""
    out = []
    home = {u: f"98.{rng.randint(10, 250)}.{rng.randint(1, 250)}.{rng.randint(2, 250)}" for u in STAFF}
    office = {u: f"10.20.{rng.randint(1, 14)}.{rng.randint(2, 250)}" for u in STAFF}
    for _ in range(330):
        u, t = rng.choice(STAFF), _ts(rng.randint(6, 19), rng.randint(0, 59), rng.randint(0, 59), rng.randint(-2, 0))
        r = rng.random()
        if r < 0.62:
            out.append(("vpn", t, _fmt(t, "VPN_LOGIN_SUCCESS", user=u, ip=office[u]), set()))
        elif r < 0.85:
            out.append(("vpn", t, _fmt(t, "VPN_DISCONNECT", user=u, ip=office[u]), set()))
        else:
            out.append(("vpn", t, _fmt(t, "VPN_LOGIN_FAILED", user=u, ip=home[u], reason="bad_password"), set()))
    for _ in range(620):
        h = rng.choice(HOSTS)
        t = _ts(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59), rng.randint(-2, 0))
        r = rng.random()
        if r < 0.70:
            out.append(("firewall", t, _fmt(t, "FIREWALL_ALLOW", src=HOST_IP[h], dst=rng.choice(EXTERNAL),
                                            dport=rng.choice([443, 443, 80, 53]),
                                            policy=rng.choice(["WEB_ALLOW", "WEB_ALLOW", "DNS_ALLOW"])), set()))
        elif r < 0.92:
            out.append(("firewall", t, _fmt(t, "FIREWALL_DENY", src=HOST_IP[h],
                                            dst=f"185.{rng.randint(1, 250)}.{rng.randint(1, 250)}.{rng.randint(2, 250)}",
                                            dport=rng.choice([25, 6667, 23]), policy="OUTBOUND_BLOCK"), set()))
        else:
            out.append(("firewall", t, _fmt(t, "FIREWALL_DENY", src=f"192.0.2.{rng.randint(2, 250)}",
                                            dst=HOST_IP[h], dport=443, policy="GEO_BLOCK_INBOUND"), set()))
    for _ in range(140):
        rtr = rng.choice(["core-rtr-01", "core-rtr-02", "edge-rtr-01"])
        t = _ts(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59), rng.randint(-2, 0))
        r = rng.random()
        if r < 0.75:
            out.append(("syslog", t, _fmt(t, "HEALTH_OK", host=rtr), set()))
        elif r < 0.9:
            out.append(("syslog", t, _fmt(t, "BGP_NEIGHBOR_UP", host=rtr, neighbor=f"10.255.0.{rng.randint(1, 9)}"),
                        set()))
        else:
            out.append(("syslog", t, _fmt(t, "CONFIG_COMMIT", host=rtr, change=f"CHG-{rng.randint(4000, 4400)}",
                                          by=rng.choice(["net.admin1", "net.admin2"])), set()))
    for _ in range(110):
        h = rng.choice(HOSTS)
        t = _ts(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59), rng.randint(-2, 0))
        out.append(("auth", t, _fmt(t, "SESSION_OPEN", host=h, ip=HOST_IP[h],
                                    user=rng.choice(["deploy", "svc_backup", "svc_monitor"])), set()))
    return out


DOCS = [
    {"id": "POL-GEO_BLOCK_INBOUND", "title": "Policy: inbound geo block", "readers": ["netops", "security"],
     "applies_to": ["policy:GEO_BLOCK_INBOUND"], "body": [
         "## What it does\nDenies inbound connections from address ranges outside the countries the bank serves. "
         "A deny under this policy means the source address is not expected to reach any internal service.",
         "## When it fires with VPN failures\nIf the same source also fails VPN logins, treat the failures as an "
         "attack on the accounts, not as users mistyping. Follow RB-VPN-LOCKOUT."]},
    {"id": "RB-VPN-LOCKOUT", "title": "Runbook: VPN account lockouts", "readers": ["netops", "security"],
     "applies_to": ["policy:VPN_LOCKOUT"], "body": [
         "## Symptom\nA user cannot connect to VPN; the log shows VPN_ACCOUNT_LOCKED after repeated failures.",
         "## Steps\n1. Check whether the failures come from the user's own address. 2. If one address failed for "
         "several users, escalate to security as credential stuffing and block the address. 3. Unlock only after "
         "the user resets their password through the identity portal."]},
    {"id": "POL-DB_SEGMENT_DENY", "title": "Policy: database segment isolation", "readers": ["netops", "security"],
     "applies_to": ["policy:DB_SEGMENT_DENY"], "body": [
         "## What it does\nOnly the application tier may open connections to the database segment 10.50.2.0/24 "
         "on SQL ports. VPN users are denied by design.",
         "## Getting access\nAccess for a person is granted through a time-boxed request, see RB-DB-ACCESS-REQUEST."]},
    {"id": "RB-DB-ACCESS-REQUEST", "title": "Runbook: requesting database access", "readers": ["netops", "security"],
     "applies_to": ["policy:DB_SEGMENT_DENY"], "body": [
         "## Steps\nRaise an access request naming the database host, the port and a business reason. A second "
         "engineer approves; the firewall exception expires after eight hours."]},
    {"id": "CHG-4471", "title": "Change record CHG-4471: core router software upgrade",
     "readers": ["netops", "security"], "applies_to": ["change:CHG-4471", "host:core-rtr-02"], "body": [
         "## Change\nUpgrade core-rtr-02 to the vendor's maintenance release during the 02:00 window. Approved by "
         "the change advisory board.",
         "## Known risk\nThe release changes the BGP hold timer default from 180 to 90 seconds; neighbors that "
         "keep the old timer may flap until both sides match."]},
    {"id": "RB-BGP-FLAP", "title": "Runbook: BGP neighbor flapping", "readers": ["netops", "security"],
     "applies_to": ["host:core-rtr-01", "host:core-rtr-02"], "body": [
         "## Steps\n1. Check for a configuration commit on the router in the hour before the first flap. "
         "2. Compare hold timers on both neighbors. 3. If a change caused it, roll back under the same change "
         "number or raise an emergency change."]},
    {"id": "POL-OUTBOUND_BLOCK", "title": "Policy: outbound block list", "readers": ["netops", "security"],
     "applies_to": ["policy:OUTBOUND_BLOCK"], "body": [
         "## What it does\nDenies outbound connections to known-bad addresses and to ports used for remote shells "
         "and mail relays. Routine denies on ports 23, 25 and 6667 are expected background noise."]},
    {"id": "SEC-IR-PLAYBOOK", "title": "Security: suspected host compromise", "readers": ["security"],
     "applies_to": ["host:srv-db-01", "policy:OUTBOUND_BLOCK"], "body": [
         "## Indicators\nPrivilege escalation by a service account, a new SSH key, and outbound attempts to a "
         "remote-shell port together indicate compromise.",
         "## Steps\nIsolate the host, preserve its disk, rotate the service account and open a security incident."]},
    {"id": "POL-PORT_SCAN_DETECTED", "title": "Policy: port scan detection", "readers": ["netops", "security"],
     "applies_to": ["policy:PORT_SCAN_DETECTED"], "body": [
         "## What it does\nDenies a source that touches many destinations on management ports within minutes, "
         "and logs each attempt."]},
    {"id": "RB-DISK-FULL", "title": "Runbook: disk full on a server", "readers": ["netops", "security"],
     "applies_to": ["host:srv-web-02", "host:srv-web-01"], "body": [
         "## Steps\nRotate and compress logs under /var/log, then raise the log volume if it fills again within "
         "a week."]},
    {"id": "POL-FILESHARE_SEGMENT_DENY", "title": "Policy: file share segment", "readers": ["netops", "security"],
     "applies_to": ["policy:FILESHARE_SEGMENT_DENY"], "body": [
         "## What it does\nOnly hosts on the backup allow list may reach the file share segment on SMB (445). "
         "A server added after the list was last reviewed is denied until it is added."]},
    {"id": "CHG-4502", "title": "Change record CHG-4502: edge router prefix filter", "readers": ["netops", "security"],
     "applies_to": ["change:CHG-4502", "host:edge-rtr-01"], "body": [
         "## Change\nTighten the maximum-prefix limit on the edge router's upstream session.",
         "## Known risk\nIf the upstream announces more prefixes than the new limit, the session is torn down."]},
    {"id": "POL-WEB_ALLOW", "title": "Policy: outbound web", "readers": ["netops", "security"],
     "applies_to": ["policy:WEB_ALLOW"], "body": ["## What it does\nAllows servers to reach the web on 80 and 443."]},
]

QUESTIONS = [
    ("q1", "netops_analyst", "multi_hop", "Why are john.doe's VPN logins failing this morning, and is anyone else affected?",
     ["POL-GEO_BLOCK_INBOUND", "RB-VPN-LOCKOUT"]),
    ("q2", "netops_analyst", "multi_hop", "Why can't alice.smith reach the database server?",
     ["POL-DB_SEGMENT_DENY", "RB-DB-ACCESS-REQUEST"]),
    ("q3", "netops_analyst", "multi_hop", "What caused the BGP neighbor flaps on core-rtr-02 overnight?",
     ["CHG-4471", "RB-BGP-FLAP"]),
    ("q4", "secops_analyst", "multi_hop", "Is srv-db-01 compromised?", ["SEC-IR-PLAYBOOK", "POL-OUTBOUND_BLOCK"]),
    ("q5", "netops_analyst", "multi_hop", "Is srv-db-01 compromised?", ["POL-OUTBOUND_BLOCK"]),
    ("q6", "netops_analyst", "single_hop", "When did srv-web-02 run out of disk space?", ["RB-DISK-FULL"]),
    ("q7", "netops_analyst", "single_hop", "Show the failed VPN logins for bob.jones", []),
    ("q8", "netops_analyst", "single_hop", "Which address triggered PORT_SCAN_DETECTED?", ["POL-PORT_SCAN_DETECTED"]),
]
HELD_OUT = [
    ("h1", "netops_analyst", "multi_hop", "Why did last night's backup from srv-db-02 fail?", ["POL-FILESHARE_SEGMENT_DENY"]),
    ("h2", "netops_analyst", "multi_hop", "Why does edge-rtr-01 keep losing its upstream BGP session?", ["CHG-4502"]),
    ("h3", "secops_analyst", "multi_hop", "Which accounts did 198.51.100.77 try, and was it stopped?",
     ["POL-GEO_BLOCK_INBOUND"]),
    ("h4", "netops_analyst", "multi_hop", "What happened when paula.reyes tried to use the database?",
     ["POL-DB_SEGMENT_DENY", "RB-DB-ACCESS-REQUEST"]),
    ("h5", "netops_analyst", "single_hop", "Who committed CHG-4502 and when?", []),
    ("h6", "secops_analyst", "multi_hop", "Has svc_monitor done anything suspicious?", ["SEC-IR-PLAYBOOK"]),
    ("h7", "netops_analyst", "multi_hop", "Is srv-app-02 talking to anything it shouldn't?", ["POL-OUTBOUND_BLOCK"]),
]
NOTES_HELD_OUT = {
    "h1": "The backup failure names srv-db-02; the denies name its address (joined by the inventory) and a policy.",
    "h2": "The session drops name the router; the change committed on it two minutes earlier explains them.",
    "h3": "Asked by address: the failures and the deny name it directly, the policy page is one hop on.",
    "h4": "The denies name paula.reyes's address, which only her VPN login states.",
    "h5": "Answered by the line that names the change.",
    "h6": "The escalation names the account and the host; the outbound denies name only the host's address.",
    "h7": "From network operations, who cannot read the auth log: the inventory joins the host to its address.",
}

NOTES = {
    "q1": "The failures for john.doe come from one address that also failed for maria.chen and raj.patel and was "
          "geo-blocked: the link is the address, which no line about john.doe states.",
    "q2": "alice.smith's address appears only on her VPN login; the denies name the address, not her.",
    "q3": "The flaps name the router; the cause is a change committed on it two minutes earlier.",
    "q4": "The firewall names an address; the auth log ties that address to srv-db-01. Security can read both.",
    "q5": "The same question from network operations: the auth lines and the incident playbook are not theirs to "
          "read, so the expected evidence is what they may see, and those must never be returned.",
    "q6": "Answered by lines that name the host directly.",
    "q7": "Answered by lines that name the user directly.",
    "q8": "Answered by lines that name the policy directly.",
}


def generate(folder: Path = FIXTURES, seed: int = 14) -> None:
    rng = random.Random(seed)
    rows = _scenarios() + _held_out() + _held_out_2() + _inventory() + _background(rng)
    by_source: dict[str, list] = {s: [] for s in SOURCES}
    for source, t, line, tags in rows:
        by_source[source].append((t, line, tags))
    evidence: dict[str, list[str]] = {q[0]: [] for q in QUESTIONS + HELD_OUT + HELD_OUT_2}
    (folder / "logs").mkdir(parents=True, exist_ok=True)
    for source, lines in by_source.items():
        lines.sort(key=lambda r: (r[0], r[1]))
        (folder / "logs" / f"{source}.log").write_text("\n".join(line for _, line, _ in lines) + "\n",
                                                        encoding="utf-8")
        for n, (_, _, tags) in enumerate(lines, 1):
            for tag in tags:
                evidence[tag].append(f"{source}:{n}")
    docs = folder / "docs"
    docs.mkdir(exist_ok=True)
    for d in DOCS:
        meta = {k: d[k] for k in ("id", "title", "readers", "applies_to")}
        (docs / f"{d['id']}.md").write_text("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n"
                                            + "\n\n".join(d["body"]) + "\n", encoding="utf-8")
    questions = [{"id": qid, "as": who, "kind": kind, "question": q, "set": "development",
                  "expected": evidence[qid] + [f"doc:{p}" for p in pages], "note": NOTES[qid]}
                 for qid, who, kind, q, pages in QUESTIONS]
    for qid, who, kind, q, pages in HELD_OUT:
        questions.append({"id": qid, "as": who, "kind": kind, "question": q, "set": "held_out_1",
                          "expected": evidence[qid] + [f"doc:{p}" for p in pages], "note": NOTES_HELD_OUT[qid]})
    for qid, who, kind, q, pages in HELD_OUT_2:
        questions.append({"id": qid, "as": who, "kind": kind, "question": q, "set": "held_out_2",
                          "expected": evidence[qid] + [f"doc:{p}" for p in pages], "note": NOTES_HELD_OUT_2[qid]})
    questions[4]["forbidden"] = [k for k in evidence["q4"] if k.startswith("auth:")] + ["doc:SEC-IR-PLAYBOOK"]
    by_id = {q["id"]: q for q in questions}
    by_id["h7"]["forbidden"] = [k for k in evidence["h6"] if k.startswith("auth:")] + ["doc:SEC-IR-PLAYBOOK"]
    (folder / "questions.yaml").write_text(
        "# Generated by netgraph/corpus.py. expected: the log lines (source:line) and pages (doc:ID) that answer\n"
        "# the question, as the asker may see them. A page counts as found when any of its sections is returned.\n"
        + yaml.safe_dump(questions, sort_keys=False, width=110), encoding="utf-8")
    (folder / "README.md").write_text(
        "# Fixtures\n\nSynthetic network logs, pages and questions, written by `python -m netgraph.corpus` "
        "(seed 14). All addresses, hosts, people and changes are invented; 203.0.113.0/24 and 192.0.2.0/24 are "
        "documentation ranges.\n", encoding="utf-8")


if __name__ == "__main__":
    generate()
    print(f"wrote {FIXTURES}")
