#!/usr/bin/env python3
"""Fetch the Time To Pet company calendar feed and write today.json.

Reads the feed URL from the TTP_CAL_URL env var (a repo secret).
Never prints the URL.
"""
import os, re, sys, json, datetime, urllib.request
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
URL = os.environ.get("TTP_CAL_URL", "").strip()
if not URL:
    sys.exit("TTP_CAL_URL is not set")

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "petcare-brief-today/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", "replace")

def unfold(raw):
    return re.sub(r"\r?\n[ \t]", "", raw)

def unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i+1]
            out.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(n, n))
            i += 2
        else:
            out.append(c); i += 1
    return "".join(out)

def events(raw):
    for block in re.findall(r"BEGIN:VEVENT\r?\n(.*?)END:VEVENT", unfold(raw), re.S):
        ev = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            ev.setdefault(key.split(";")[0].strip().upper(), val.strip())
        yield ev

def start(ev):
    v = ev.get("DTSTART", "")
    try:
        if re.fullmatch(r"\d{8}T\d{6}Z", v):
            return (datetime.datetime.strptime(v, "%Y%m%dT%H%M%SZ")
                    .replace(tzinfo=datetime.timezone.utc).astimezone(ET))
        if re.fullmatch(r"\d{8}T\d{6}", v):
            return datetime.datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=ET)
        if re.fullmatch(r"\d{8}", v):
            return datetime.datetime.strptime(v, "%Y%m%d").replace(tzinfo=ET)
    except ValueError:
        return None
    return None

def field(desc, label):
    m = re.search(rf"^{label}:[ \t]*(.*)$", desc, re.M)
    return m.group(1).strip() if m else ""

def build(raw, today=None):
    today = today or datetime.datetime.now(ET).date()
    by_client, skipped = {}, 0
    for ev in events(raw):
        d = start(ev)
        if d is None:
            skipped += 1; continue
        if d.date() != today:
            continue
        desc = unescape(ev.get("DESCRIPTION", ""))
        name = field(desc, "Client")
        if not name:
            # fall back to the first comma-separated part of SUMMARY
            name = unescape(ev.get("SUMMARY", "")).split(",")[0].strip()
        if not name:
            skipped += 1; continue
        rec = by_client.setdefault(name, {"name": name, "visits": []})
        rec["visits"].append({
            "time":    d.strftime("%-I:%M %p"),
            "sort":    d.strftime("%H%M"),
            "staff":   field(desc, "Staff"),
            "service": field(desc, "Service"),
            "pets":    field(desc, "Pets"),
        })
    for rec in by_client.values():
        rec["visits"].sort(key=lambda v: v["sort"])
    clients = sorted(by_client.values(), key=lambda c: c["name"].lower())
    return {
        "generated": datetime.datetime.now(ET).replace(microsecond=0).isoformat(),
        "date":      today.isoformat(),
        "label":     today.strftime("%b %-d, %Y"),
        "clients":   clients,
        "visitCount": sum(len(c["visits"]) for c in clients),
    }, skipped

def previous():
    try:
        with open("today.json") as f:
            return json.load(f)
    except Exception:
        return None

if __name__ == "__main__":
    raw = fetch(URL)
    if "BEGIN:VEVENT" not in raw:
        sys.exit("feed did not look like iCalendar - refusing to overwrite today.json")
    data, skipped = build(raw)
    prev = previous()

    # Guard: never blank a day that previously had visits. A feed outage or an
    # auth change can return a valid-but-empty calendar, and silently emptying
    # the roster would hide every client from the sitters' page.
    if not data["clients"] and prev and prev.get("date") == data["date"] and prev.get("clients"):
        sys.exit(f"feed returned 0 visits for {data['date']} but the last run found "
                 f"{len(prev['clients'])} clients - keeping the previous file")

    with open("today.json", "w") as f:
        json.dump(data, f, indent=1)
    print(f"{data['date']}: {len(data['clients'])} clients, "
          f"{data['visitCount']} visits, {skipped} unparsed")
