#!/usr/bin/env python3
"""tdr: reference tool for traceable design records.

Python 3.10+, standard library only, no network.
Usage: python tools/tdr.py <check|status|get|refs|brief|tier|mcp> [--root DIR]
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- YAML subset


class YErr(Exception):
    """A refusal by the YAML subset reader."""

    def __init__(self, line, msg):
        super().__init__(msg)
        self.line, self.msg = line, msg


class Map(dict):
    """A mapping with the line of the mapping and of each key."""

    def __init__(self, line=0):
        super().__init__()
        self.line, self.klines = line, {}

    def at(self, key):
        return self.klines.get(key, self.line)


KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:[ ]+(.*))?$")
INT = re.compile(r"^-?\d+$")
FLOAT = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
LITERAL = re.compile(r"^\|-?$")


def plain(t, ln):
    """Convert a plain scalar (comment already cut)."""
    if ": " in t or t.endswith(":") or " #" in t:
        raise YErr(ln, "plain scalar contains ': ' or ' #' (quote it)")
    if t == "null":
        return None
    if t in ("true", "false"):
        return t == "true"
    if INT.match(t):
        return int(t)
    if FLOAT.match(t):
        return float(t)
    return t


def quoted(t, ln):
    """Read a quoted scalar followed by an optional comment."""
    q, out, i = t[0], [], 1
    while i < len(t):
        c = t[i]
        if q == '"' and c == "\\":
            nxt = t[i + 1:i + 2]
            if nxt not in ('"', "\\", "n"):
                raise YErr(ln, "unsupported escape in double quotes")
            out.append({"n": "\n"}.get(nxt, nxt))
            i += 2
            continue
        if c == q:
            if q == "'" and t[i + 1:i + 2] == "'":
                out.append("'")
                i += 2
                continue
            if t[i + 1:].strip():
                raise YErr(ln, "text after closing quote (comments need their own line)")
            return "".join(out)
        out.append(c)
        i += 1
    raise YErr(ln, "unterminated quote")


def scalar(text, ln):
    """Parse one inline value."""
    t = text.strip()
    c = t[0]
    if c in "\"'":
        return quoted(t, ln)
    if c in "{[":
        raise YErr(ln, "flow style is not allowed")
    if c in "&*!":
        raise YErr(ln, "anchors, aliases, and tags are not allowed")
    if c in "|>%@`#":
        raise YErr(ln, "value starts with a special character (quote it)")
    return plain(t, ln)


class Reader:
    """Recursive reader over raw lines."""

    def __init__(self, text):
        self.raw = text.replace("\r\n", "\n").split("\n")
        self.pos, self.over = 0, {}

    def peek(self):
        """Next significant line as (index, indent, text), or None."""
        while self.pos < len(self.raw):
            i = self.pos
            if i in self.over:
                return (i, *self.over[i])
            r = self.raw[i]
            s = r.strip()
            if not s or s.startswith("#"):
                self.pos += 1
                continue
            lead = r[:len(r) - len(r.lstrip())]
            if "\t" in lead:
                raise YErr(i + 1, "tab in indentation")
            if s in ("---", "...") or s.startswith("--- "):
                raise YErr(i + 1, "document markers are not allowed")
            return i, len(lead), r.strip()
        return None

    def block(self, indent):
        i, ind, t = self.peek()
        if ind != indent:
            raise YErr(i + 1, "bad indentation")
        if t == "-" or t.startswith("- "):
            return self.seq(indent)
        return self.map(indent)

    def literal(self, indent, strip_nl):
        """Read a literal block whose lines are indented deeper than indent."""
        lines, j = [], self.pos
        while j < len(self.raw):
            r = self.raw[j]
            if r.strip() and len(r) - len(r.lstrip(" ")) <= indent:
                break
            lines.append(r)
            j += 1
        while lines and not lines[-1].strip():
            lines.pop()
        self.pos = j
        first = next((l for l in lines if l.strip()), None)
        if first is None:
            return ""
        base = len(first) - len(first.lstrip(" "))
        text = "\n".join(l[base:] if l.strip() else "" for l in lines)
        return text if strip_nl else text + "\n"

    def child(self, indent):
        """Value of a key or item with nothing inline."""
        q = self.peek()
        if q and q[1] > indent:
            return self.block(q[1])
        return None

    def map(self, indent):
        i0 = self.peek()[0]
        m = Map(i0 + 1)
        while True:
            p = self.peek()
            if not p or p[1] < indent:
                break
            i, ind, t = p
            if ind > indent:
                raise YErr(i + 1, "bad indentation")
            mt = KEY.match(t)
            if not mt:
                raise YErr(i + 1, "expected 'key: value'")
            key, rest = mt.group(1), (mt.group(2) or "").strip()
            if key in m:
                raise YErr(i + 1, f"duplicate key '{key}'")
            m.klines[key] = i + 1
            self.pos = i + 1
            if not rest:
                q = self.peek()
                if q and q[1] == indent and (q[2] == "-" or q[2].startswith("- ")):
                    m[key] = self.seq(indent)
                else:
                    m[key] = self.child(indent)
            elif LITERAL.match(rest):
                m[key] = self.literal(indent, rest.startswith("|-"))
            else:
                m[key] = scalar(rest, i + 1)
        return m

    def seq(self, indent):
        s = []
        while True:
            p = self.peek()
            if not p or p[1] < indent:
                break
            i, ind, t = p
            if ind > indent:
                raise YErr(i + 1, "bad indentation")
            if not (t == "-" or t.startswith("- ")):
                break
            body = t[1:].lstrip(" ")
            off = len(t) - len(body)
            self.pos = i + 1
            if not body:
                s.append(self.child(indent))
            elif KEY.match(body):
                self.over[i] = (indent + off, body)
                self.pos = i
                s.append(self.map(indent + off))
            else:
                s.append(scalar(body, i + 1))
        return s


def load_yaml(text):
    """Parse text in the subset; raise YErr on anything else."""
    r = Reader(text)
    p = r.peek()
    if p is None:
        raise YErr(1, "empty file")
    if p[1] != 0:
        raise YErr(p[0] + 1, "bad indentation")
    data = r.block(0)
    p = r.peek()
    if p:
        raise YErr(p[0] + 1, "unexpected content")
    return data


# ---------------------------------------------------------------- schema

GATES = ("design_build", "build_operate", "remove")
KINDFILES = ("requirement", "value", "source", "decision", "open", "hazard", "part",
             "interface", "check", "citation", "baseline", "run", "procedure")
TIERS = ("T0", "T1", "T2", "T3")
D = r"\d{4}-\d{2}-\d{2}"
SRC_RX = re.compile(r"[PSMF]\d+[a-z]?")
GLOBAL_RX = re.compile(r"[\w.-]+/[\w.-]+#[\w.-]+@[\w.-]+")
DOCS_RX = re.compile(r"docs/[^#\s]+\.md(#[\w.-]+)?")
PHASES = ("design", "build", "operate", "change", "remove")

SPECS = {
    "requirement": dict(req="id text origin design_point verify basis status",
                        opt="expected parent parts", lists="basis parts",
                        enum=dict(origin=("owner", "derived"),
                                  verify=("inspection", "analysis", "demonstration", "test"),
                                  status=("awaiting", "confirmed", "withdrawn")),
                        rx=dict(id=r"R-\d+[a-z]?")),
    "value": dict(req="key v status basis", opt="unit", lists="basis",
                  enum=dict(status=("decided", "sourced", "calculated", "measured", "assumed")),
                  rx=dict(key=r"[a-z0-9_]+(\.[a-z0-9_]+)+")),
    "source": dict(req="id class title license read", opt="url retrieved note",
                   enum=dict(**{"class": tuple("PSMF")},
                             read=("full", "partial", "abstract", "excerpt", "archived", "not_read")),
                   rx=dict(id=SRC_RX.pattern), dates="retrieved"),
    "decision": dict(req="id date by what touches", opt="options supersedes",
                     lists="options touches", enum=dict(by=("owner", "operator")),
                     rx=dict(id=r"D-\d{3}"), dates="date"),
    "open": dict(req="id kind text waits_for opened",
                 opt="affects raised_by phase run blocks closed_by closed", lists="affects",
                 enum=dict(kind=("question", "finding", "notice"), phase=PHASES),
                 rx=dict(id=r"Q-\d{3}"), dates="opened closed"),
    "hazard": dict(req="id text phases at_risk controls", lists="phases controls",
                   enum=dict(phases=PHASES), rx=dict(id=r"H-\d{2}")),
    "part": dict(req="id name", opt="parent", rx=dict(id=r"[a-z][a-z0-9_]*")),
    "interface": dict(req="id between carries", opt="values", lists="between values",
                      enum=dict(carries=("load", "heat", "gas", "liquid", "power", "signal",
                                         "smoke", "other")),
                      rx=dict(id=r"I-\d{2}")),
    "check": dict(req="id target tier method result by date", opt="detail", any="target",
                  enum=dict(tier=TIERS, result=("pass", "fail", "partial"),
                            method=("script", "refute", "independent_model", "hand_calc",
                                    "inspection", "analysis", "demonstration", "test",
                                    "measurement")),
                  rx=dict(id=r"K-\d{3}"), dates="date"),
    "baseline": dict(req="id kind date decision", opt="deviations", lists="deviations",
                     enum=dict(kind=("as_designed", "as_built", "as_operated")),
                     rx=dict(id=r"B-\d{2}"), dates="date"),
    "run": dict(req="id date procedure", opt="conditions measured deviations findings",
                lists="measured deviations findings", rx=dict(id=r"RN-\d{3}"), dates="date"),
    "procedure": dict(req="id title steps", lists="steps", rx=dict(id=r"OP-\d{2}")),
    "citation": dict(req="ref used_for", opt="responses", lists="used_for responses"),
}
NESTED = {
    ("baseline", "deviations"): dict(req="key design built decision", any="design built"),
    ("run", "measured"): dict(req="key v", opt="unit how"),
    ("procedure", "steps"): dict(req="id action", opt="values controls limits",
                                 lists="values controls limits", rx=dict(id=r"OP-\d{2}\.\d{2}")),
    ("citation", "responses"): dict(req="notice answer", opt="by_id",
                                    enum=dict(answer=("adopted", "not_applicable", "open"))),
}
OVR = {("requirement", "design_point"): "E07", ("requirement", "verify"): "E08",
       ("value", "status"): "E04", ("value", "basis"): "E04", ("hazard", "controls"): "E09",
       ("baseline.deviations", "decision"): "E19"}

# (kind, field) or (kind, listfield, subfield): fields whose entries are references.
REF_FIELDS = [
    ("requirement", "basis"), ("requirement", "parent"), ("requirement", "parts"),
    ("value", "basis"), ("decision", "touches"), ("decision", "supersedes"),
    ("open", "affects"), ("open", "closed_by"), ("open", "run"),
    ("hazard", "controls"), ("part", "parent"), ("interface", "between"),
    ("interface", "values"), ("check", "target"), ("baseline", "decision"),
    ("run", "procedure"), ("run", "findings"),
    ("baseline", "deviations", "key"), ("baseline", "deviations", "decision"),
    ("run", "measured", "key"), ("procedure", "steps", "values"),
    ("procedure", "steps", "limits"), ("procedure", "steps", "controls"),
    ("citation", "responses", "notice"), ("citation", "responses", "by_id"),
]
REF_CODE = {("baseline", "deviations", "decision"): "E19",
            ("procedure", "steps", "values"): "E17", ("procedure", "steps", "limits"): "E17"}


def blank(v):
    return v is None or v == "" or v == []


def aslist(v):
    """A null or missing list is empty; a scalar is a one-item list."""
    return [] if v is None else v if isinstance(v, list) else [v]


def est_tokens(text):
    ascii_n = sum(1 for c in text if ord(c) < 128)
    return int(ascii_n / 4 + (len(text) - ascii_n) / 1.5 + 0.999)


# ---------------------------------------------------------------- record


class Item:
    def __init__(self, kind, d, path):
        self.kind, self.d, self.path = kind, d, path

    @property
    def id(self):
        d = self.d
        return str(d.get("id") or d.get("key") or d.get("ref") or "")


class Record:
    """All files of record/ parsed and validated for form (E01, E02, E21, E22)."""

    def __init__(self, root):
        self.root = Path(root)
        self.problems = []
        self.items = {k: [] for k in KINDFILES}
        self.index = {}
        base = self.root / "record"
        files = sorted(base.rglob("*.yaml")) if base.is_dir() else []
        for f in files:
            self.load_file(f)

    def add(self, code, path, line, msg):
        self.problems.append((code, path, line, msg))

    def load_file(self, f):
        rel = f.relative_to(self.root).as_posix()
        try:
            data = load_yaml(f.read_text(encoding="utf-8"))
        except YErr as e:
            return self.add("E01", rel, e.line, e.msg)
        except (OSError, UnicodeDecodeError) as e:
            return self.add("E01", rel, 0, f"cannot read: {e}")
        if not isinstance(data, Map):
            return self.add("E01", rel, 1, "top level must be a mapping")
        for k in data:
            if k not in ("kind", "schema", "items"):
                self.add("E21", rel, data.at(k), f"unknown top-level key '{k}'")
        kind = data.get("kind")
        if kind not in KINDFILES:
            return self.add("E01", rel, data.at("kind"), f"missing or unknown kind {kind!r}")
        if data.get("schema") != 1 or isinstance(data.get("schema"), bool):
            self.add("E01", rel, data.at("schema"), "schema must be the integer 1")
        items = data.get("items")
        if "items" not in data or not (items is None or isinstance(items, list)):
            return self.add("E01", rel, data.at("items"), "items must be a list")
        for d in items or []:
            if not isinstance(d, Map):
                self.add("E01", rel, data.at("items"), "each item must be a mapping")
                continue
            it = Item(kind, d, rel)
            self.items[kind].append(it)
            self.validate(kind, SPECS[kind], d, rel, kind)
            self.register(it)

    def register(self, it):
        for x in [it] + [Item("step", s, it.path) for s in aslist(it.d.get("steps"))
                         if isinstance(s, Map)]:
            i = x.id
            if not i:
                continue
            if i in self.index:
                o = self.index[i]
                self.add("E02", x.path, x.d.line, f"duplicate id {i} (also {o.path}:{o.d.line})")
            else:
                self.index[i] = x

    def validate(self, kind, sp, d, path, label):
        """Generic field checks; nested lists are descended into."""
        req, opt = sp["req"].split(), sp.get("opt", "").split()
        lists, anyf = sp.get("lists", "").split(), sp.get("any", "").split()
        for k in d:
            if k not in req + opt:
                self.add("E21", path, d.at(k), f"unknown key '{k}' in {label}")
        for f in req:
            if blank(d.get(f)):
                self.add(OVR.get((label, f), "E22"), path, d.line, f"{label}: missing {f}")
        for f in req + opt:
            v = d.get(f)
            if v is None:
                continue
            line = d.at(f)
            if f in lists:
                if not isinstance(v, list):
                    self.add("E22", path, line, f"{f} must be a list")
                    continue
            elif f not in anyf and isinstance(v, (list, Map)):
                self.add("E22", path, line, f"{f} must be a single value")
                continue
            for x in aslist(v) if not isinstance(v, Map) else []:
                if isinstance(x, Map) and (label, f) in NESTED:
                    continue
                if f in sp.get("enum", {}) and str(x) not in sp["enum"][f]:
                    self.add(OVR.get((label, f), "E22"), path, line, f"{f}: '{x}' is not allowed")
            if f in sp.get("rx", {}) and not re.fullmatch(sp["rx"][f], str(v)):
                self.add("E22", path, line, f"{f}: '{v}' has a bad form")
            if f in sp.get("dates", "").split() and not re.fullmatch(D, str(v)):
                self.add("E22", path, line, f"{f}: '{v}' is not YYYY-MM-DD")
        if kind == label:
            self.special(kind, d, path)
            for (k, lf), nsp in NESTED.items():
                if k != kind:
                    continue
                for sub in aslist(d.get(lf)):
                    if not isinstance(sub, Map):
                        self.add("E22", path, d.at(lf), f"{lf} entries must be mappings")
                    else:
                        self.validate(kind, nsp, sub, path, f"{kind}.{lf}")

    def special(self, kind, d, path):
        """Rules that need more than one field."""
        if kind == "source":
            if d.get("read") != "not_read" and blank(d.get("retrieved")):
                self.add("E22", path, d.line, "source: retrieved is required unless read is not_read")
            if str(d.get("class")) != str(d.get("id", " "))[:1]:
                self.add("E22", path, d.at("class"), "source: class must equal the letter of the id")
        elif kind == "interface" and isinstance(d.get("between"), list) and len(d["between"]) != 2:
            self.add("E22", path, d.at("between"), "interface: between needs two part ids")
        elif kind == "open" and d.get("kind") == "finding":
            for f in ("affects", "phase", "raised_by"):
                if blank(d.get(f)):
                    self.add("E18", path, d.line, f"finding {d.get('id')}: missing {f}")
            if d.get("phase") == "operate" and blank(d.get("run")):
                self.add("E18", path, d.line, f"finding {d.get('id')}: phase operate needs a run")
        elif kind == "open" and blank(d.get("affects")) and d.get("kind") != "finding":
            self.add("E22", path, d.line, "open: missing affects")

    def all_items(self):
        return [it for k in KINDFILES for it in self.items[k]]

    def is_closed(self, it):
        return bool(it.d.get("closed") or it.d.get("closed_by"))

    def ref_ok(self, ref):
        """True when ref resolves; external forms are never checked for existence."""
        ref = str(ref)
        if ref in self.index or ref.startswith("private:") or GLOBAL_RX.fullmatch(ref):
            return True
        if DOCS_RX.fullmatch(ref):
            return (self.root / ref.split("#")[0]).is_file()
        return False

    def refs(self):
        """Yield (item, field, ref, line, code) for every reference in the record."""
        for it in self.all_items():
            for spec in REF_FIELDS:
                if spec[0] != it.kind:
                    continue
                owners = [(it.d, spec[1], it.d.at(spec[1]))] if len(spec) == 2 else [
                    (s, spec[2], s.line) for s in aslist(it.d.get(spec[1])) if isinstance(s, Map)]
                for d, f, line in owners:
                    v = d.get(f)
                    if isinstance(v, str) and spec[:2] == ("check", "target"):
                        continue
                    name = ".".join(spec[1:])
                    for r in aslist(v):
                        yield it, name, str(r), line, REF_CODE.get(spec, "E03")


# ---------------------------------------------------------------- check


def table_md(rec, kind, fields):
    """Markdown table of the items of a kind."""
    def cell(v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, list):
            return ", ".join(cell(x) for x in v)
        return str(v).replace("|", "\\|").replace("\n", " ")
    rows = ["| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)]
    for it in rec.items[kind]:
        rows.append("| " + " | ".join(cell(it.d.get(f)) for f in fields) + " |")
    return "\n".join(rows)


TABLE_RX = re.compile(r"<!-- tdr:table kind=(\S+) fields=(\S+) -->\n(.*?)<!-- tdr:end -->", re.S)


def private_names(path):
    """Yield (line number, lowercase name) from the private names file."""
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if s and not s.startswith("#"):
            yield n, s.lower()


def check_private(root, names_path, out):
    """E13: report file, line, and the number of the name; never the name."""
    try:
        names = list(private_names(names_path))
    except (OSError, UnicodeDecodeError):
        return out.append(("E13", str(names_path), 0, "cannot read the private names file"))
    skip = Path(names_path).resolve()
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for fn in files:
            p = Path(dirpath, fn)
            if p.resolve() == skip:
                continue
            try:
                lines = p.read_text(encoding="utf-8").lower().split("\n")
            except (OSError, UnicodeDecodeError):
                continue
            rel = p.relative_to(root).as_posix()
            for ln, text in enumerate(lines, 1):
                for n, name in names:
                    if name in text:
                        out.append(("E13", rel, ln, f"private name #{n}"))


def run_check(rec, gate=None, names_path=None, with_status=True):
    """Return the list of problems (code, path, line, message)."""
    out = list(rec.problems)
    idx = rec.index
    add = lambda c, it, f, m: out.append((c, it.path, it.d.at(f) if f else it.d.line, m))
    for it, field, ref, line, code in rec.refs():
        if code == "E17":
            if not (ref in idx and idx[ref].kind == "value"):
                out.append((code, it.path, line, f"{it.id}: {field} '{ref}' is not a value key"))
        elif not rec.ref_ok(ref):
            out.append((code, it.path, line, f"{it.id}: {field} '{ref}' does not exist"))
    for it in rec.items["baseline"]:
        for s in aslist(it.d.get("deviations")):
            if isinstance(s, Map) and blank(s.get("decision")):
                out.append(("E19", it.path, s.line, f"{it.id}: deviation without a decision"))
    # values
    for it in rec.items["value"]:
        basis = [str(b) for b in aslist(it.d.get("basis"))]
        tests = dict(
            decided=lambda b: re.fullmatch(r"D-\d{3}", b),
            sourced=lambda b: SRC_RX.fullmatch(b) or GLOBAL_RX.fullmatch(b),
            calculated=lambda b: re.fullmatch(r"K-\d{3}", b) or DOCS_RX.fullmatch(b),
            measured=lambda b: re.fullmatch(r"RN-\d{3}", b),
            assumed=lambda b: re.fullmatch(r"Q-\d{3}", b) and b in idx and not rec.is_closed(idx[b]))
        st = it.d.get("status")
        if st in tests and basis and not any(tests[st](b) for b in basis):
            add("E05", it, "basis", f"{it.id}: status {st} does not match basis {', '.join(basis)}")
    # requirements
    for it in rec.items["requirement"]:
        srcs = [idx[str(b)] for b in aslist(it.d.get("basis"))
                if SRC_RX.fullmatch(str(b)) and str(b) in idx and idx[str(b)].kind == "source"]
        if srcs and all(s.d.get("class") == "F" for s in srcs):
            add("E06", it, "basis", f"{it.id}: design point rests on forum sources alone")
        if gate == "design_build" and it.d.get("status") == "awaiting":
            add("E20", it, "status", f"{it.id}: still awaiting owner confirmation")
    # hazards
    in_steps = {str(c) for p in rec.items["procedure"] for s in aslist(p.d.get("steps"))
                if isinstance(s, Map) for c in aslist(s.get("controls"))}
    for it in rec.items["hazard"]:
        if gate == "build_operate" and it.id not in in_steps:
            add("E09", it, None, f"{it.id}: control appears in no procedure step")
    # open items
    for it in rec.items["open"]:
        closed = rec.is_closed(it)
        if gate and it.d.get("kind") == "notice" and not closed:
            add("E16", it, None, f"{it.id}: notice without an answer at gate {gate}")
        if gate and it.d.get("blocks") == gate and not closed:
            add("E20", it, "blocks", f"{it.id}: blocks gate {gate} and is not closed")
    if gate == "build_operate":
        if not any(b.d.get("kind") == "as_built" for b in rec.items["baseline"]):
            out.append(("E20", "record", 0, "no as_built baseline"))
        if not rec.items["procedure"]:
            out.append(("E20", "record", 0, "no procedure"))
    for it in rec.items["citation"]:
        ref = str(it.d.get("ref", ""))
        if "@" not in ref:
            add("E15", it, "ref", f"citation '{ref}' has no @version")
        elif not GLOBAL_RX.fullmatch(ref):
            add("E15", it, "ref", f"citation '{ref}' is malformed")
    if with_status:
        out += check_generated(rec)
        if names_path:
            check_private(rec.root, names_path, out)
    return sorted(set(out), key=lambda p: (p[1], p[2], p[0], p[3]))


def check_generated(rec):
    """E10 and E14: STATUS.md and generated tables."""
    out = []
    sp = rec.root / "STATUS.md"
    if not sp.is_file():
        out.append(("E10", "STATUS.md", 0, "missing; run 'tdr status'"))
    else:
        text = sp.read_bytes().decode("utf-8", "replace")
        if text != status_text(rec):
            out.append(("E10", "STATUS.md", 0, "differs from what 'tdr status' writes"))
        if est_tokens(text) > 10000:
            out.append(("E14", "STATUS.md", 0, f"about {est_tokens(text)} tokens, over 10,000"))
    for f in sorted((rec.root / "docs").glob("*.md")):
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(rec.root).as_posix()
        for m in TABLE_RX.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            kind, fields = m.group(1), m.group(2).split(",")
            if kind not in KINDFILES:
                out.append(("E10", rel, line, f"table of unknown kind {kind}"))
            elif m.group(3).strip() != table_md(rec, kind, fields).strip():
                out.append(("E10", rel, line, f"table of {kind} differs from the generated one"))
    return out


def fmt_problems(probs):
    return [f"{c} {p}{':' + str(l) if l else ''} {m}" for c, p, l, m in probs]


# ---------------------------------------------------------------- status / get / refs / brief


def one_line(s, n=None):
    s = " ".join(str(s).split())
    return s if n is None or len(s) <= n else s[:n - 3] + "..."


def status_text(rec):
    """The text of STATUS.md, deterministic."""
    opens = rec.items["open"]
    phased = sorted((o for o in opens if o.d.get("phase")),
                    key=lambda o: (str(o.d.get("opened")), o.id))
    phase = phased[-1].d["phase"] if phased else "design"
    nxt = dict(design="design_build", build="build_operate", operate="remove",
               change="build_operate").get(phase, "none")
    L = ["<!-- Generated by tdr. Do not edit. -->", "# Status", "", "## Phase",
         f"- Phase: {phase}", f"- Next gate: {nxt}", "", "## Baselines"]
    bs = sorted(rec.items["baseline"], key=lambda b: b.id)
    L += [f"- {b.id} {b.d.get('kind')} {b.d.get('date')} (decision {b.d.get('decision')})"
          for b in bs] or ["- none"]
    L += ["", "## Open items by waits_for"]
    live = sorted((o for o in opens if not rec.is_closed(o)),
                  key=lambda o: (str(o.d.get("waits_for")), o.id))
    cur = None
    for o in live:
        if o.d.get("waits_for") != cur:
            cur = o.d.get("waits_for")
            L.append(f"### {cur}")
        L.append(f"- {o.id} ({o.d.get('kind')}) {one_line(o.d.get('text'), 120)}")
    if not live:
        L.append("- none")
    L += ["", "## Last decisions"]
    ds = sorted(rec.items["decision"], key=lambda x: (str(x.d.get("date")), x.id))[-10:]
    L += [f"- {x.id} {x.d.get('date')} {x.d.get('by')}: {one_line(x.d.get('what'), 100)}"
          for x in ds] or ["- none"]
    L += ["", "## Unanswered notices"]
    L += [f"- {o.id} {one_line(o.d.get('text'), 100)}" for o in live
          if o.d.get("kind") == "notice"] or ["- none"]
    L += ["", "## Failing checks"]
    L += [f"- {c.id} {c.d.get('result')}: {one_line(c.d.get('target'), 60)}"
          for c in rec.items["check"] if c.d.get("result") in ("fail", "partial")] or ["- none"]
    n = len(run_check(rec, None, None, with_status=False))
    L += ["", "## Problems", f"- {n} (from check, not counting E10, E13, E14)", "",
          "## Next steps"]
    L += [f"- {o.id} {one_line(o.d.get('text'), 120)}" for o in live
          if o.d.get("waits_for") == "assistant"] or ["- none"]
    return "\n".join(L) + "\n"


def fmt(v):
    if isinstance(v, list):
        return ", ".join(fmt(x) for x in v)
    if isinstance(v, Map):
        return "; ".join(f"{k}: {fmt(x)}" for k, x in v.items())
    return "" if v is None else one_line(v)


def label(rec, ref):
    """Short description of a reference, one level."""
    it = rec.index.get(ref)
    if it:
        d = it.d
        txt = d.get("title") or d.get("what") or d.get("text") or d.get("name")
        if txt is None and it.kind == "value":
            txt = f"{fmt(d.get('v'))} {fmt(d.get('unit'))} ({d.get('status')})".replace("  ", " ")
        return one_line(txt or it.kind, 80)
    if ref.startswith("private:"):
        return "(private)"
    if GLOBAL_RX.fullmatch(ref):
        return "(shared item)"
    if DOCS_RX.fullmatch(ref):
        return "(docs section)" if rec.ref_ok(ref) else "(docs file missing)"
    return "(not found)"


def get_text(rec, ident):
    it = rec.index.get(ident)
    if not it:
        return f"unknown id or key: {ident}", False
    L = [f"{it.kind} {it.id} ({it.path}:{it.d.line})"]
    L += [f"{k}: {fmt(v)}" for k, v in it.d.items() if v is not None]
    for b in aslist(it.d.get("basis")):
        L.append(f"  basis {b}: {label(rec, str(b))}")
    return "\n".join(L), True


def refs_of(rec, ident):
    return sorted({(it.kind, it.id, field) for it, field, ref, _, _ in rec.refs()
                   if ref == ident})


def refs_text(rec, ident):
    if ident not in rec.index and not refs_of(rec, ident):
        return f"unknown id or key: {ident}", False
    rows = refs_of(rec, ident)
    return "\n".join(f"{k} {i} {f}" for k, i, f in rows) or "no references", True


def brief_text(rec, ids):
    got, bad = {}, []
    for i in ids:
        if i not in rec.index:
            bad.append(i)
            continue
        got.setdefault(i, rec.index[i])
        if rec.index[i].kind == "part":
            for it in rec.items["requirement"]:
                if i in map(str, aslist(it.d.get("parts"))):
                    got.setdefault(it.id, it)
            for it in rec.items["interface"]:
                if i in map(str, aslist(it.d.get("between"))):
                    got.setdefault(it.id, it)
            for it in rec.items["value"]:
                if it.id.startswith(i + "."):
                    got.setdefault(it.id, it)
    if bad:
        return "unknown id or key: " + ", ".join(bad), False
    opens, hazards = {}, {}
    for i in got:
        for it in rec.items["open"]:
            if i in map(str, aslist(it.d.get("affects"))):
                opens.setdefault(it.id, it)
        for it in rec.items["hazard"]:
            if i in map(str, aslist(it.d.get("controls"))):
                hazards.setdefault(it.id, it)
    docs = sorted({str(b) for it in got.values() for b in aslist(it.d.get("basis"))
                   if DOCS_RX.fullmatch(str(b))})

    def line(it):
        body = "; ".join(f"{k}: {fmt(v)}" for k, v in it.d.items()
                         if v is not None and k not in ("id", "key"))
        return f"- {it.kind} {it.id} | {body}"
    L = ["# Brief: " + ", ".join(ids), "", "## Items"] + [line(i) for i in got.values()]
    for title, group in (("Open items", opens), ("Hazards", hazards)):
        L += ["", f"## {title}"] + ([line(i) for i in group.values()] or ["- none"])
    L += ["", "## Docs to read"] + ([f"- {d}" for d in docs] or ["- none"])
    return "\n".join(L), True


# ---------------------------------------------------------------- tier


def tier_problems(rec, message):
    """E11 and E12 for the trailers of a change description."""
    tr = {}
    for line in message.splitlines():
        m = re.match(r"^(Tier|Checks|Decision):\s*(.*?)\s*$", line)
        if m:
            tr[m.group(1)] = m.group(2)
    out, tier = [], tr.get("Tier")
    if tier not in TIERS:
        return [("E11", "message", 0, "missing or bad Tier trailer")]
    if tier == "T2":
        ids = [c.strip() for c in tr.get("Checks", "").split(",") if c.strip()]
        ok = any(i in rec.index and rec.index[i].kind == "check" and rec.index[i].d.get("method")
                 in ("refute", "independent_model", "hand_calc") for i in ids)
        if not ok:
            out.append(("E11", "message", 0, "T2 needs a check of method refute, "
                        "independent_model, or hand_calc"))
    if tier == "T3":
        d = tr.get("Decision", "")
        if not (d in rec.index and rec.index[d].kind == "decision"):
            out.append(("E12", "message", 0, "T3 needs an existing decision"))
    return out


# ---------------------------------------------------------------- MCP

TOOLS = [
    ("tdr_get", "Show an item by id or key, with its basis one level deep.",
     {"id": {"type": "string"}}, ["id"]),
    ("tdr_refs", "List every item that refers to an id or key.", {"id": {"type": "string"}}, ["id"]),
    ("tdr_brief", "Compact brief for a task on the given ids.",
     {"ids": {"type": "array", "items": {"type": "string"}}}, ["ids"]),
    ("tdr_status", "Return the status text without writing STATUS.md.", {}, []),
    ("tdr_check", "Run all checks; optional gate.",
     {"gate": {"type": "string", "enum": list(GATES)}}, []),
]


def call_tool(root, name, args):
    rec = Record(root)
    if name == "tdr_get":
        return get_text(rec, str(args.get("id", "")))
    if name == "tdr_refs":
        return refs_text(rec, str(args.get("id", "")))
    if name == "tdr_brief":
        return brief_text(rec, [str(x) for x in args.get("ids", [])])
    if name == "tdr_status":
        return status_text(rec), True
    if name == "tdr_check":
        probs = run_check(rec, args.get("gate"), os.environ.get("TDR_PRIVATE_NAMES"))
        return ("\n".join(fmt_problems(probs)) or "ok"), not probs
    return f"unknown tool {name}", False


def mcp_serve(root, stdin=None, stdout=None):
    """Newline-delimited JSON-RPC 2.0 over stdio."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout

    def send(obj):
        stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        stdout.flush()
    for line in stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            method, rid, params = req.get("method"), req.get("id"), req.get("params") or {}
        except (ValueError, AttributeError):
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if "id" not in req:
            continue
        reply = lambda r: send({"jsonrpc": "2.0", "id": rid, "result": r})
        if method == "initialize":
            reply({"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "tdr", "version": "0.1.0"}})
        elif method == "ping":
            reply({})
        elif method == "tools/list":
            reply({"tools": [{"name": n, "description": d, "inputSchema": {
                "type": "object", "properties": p, "required": r}} for n, d, p, r in TOOLS]})
        elif method == "tools/call":
            try:
                text, ok = call_tool(root, params.get("name"), params.get("arguments") or {})
            except Exception as e:  # report, do not crash the server
                text, ok = f"error: {e}", False
            reply({"content": [{"type": "text", "text": text}], "isError": not ok})
        else:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": f"method not found: {method}"}})


# ---------------------------------------------------------------- CLI


def main(argv=None):
    for s in (sys.stdout, sys.stdin):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", **({"newline": "\n"} if s is sys.stdout else {}))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".")
    ap = argparse.ArgumentParser(prog="tdr", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", parents=[common])
    c.add_argument("--gate", choices=GATES)
    c.add_argument("--private-names")
    sub.add_parser("status", parents=[common])
    for n in ("get", "refs"):
        sub.add_parser(n, parents=[common]).add_argument("id")
    sub.add_parser("brief", parents=[common]).add_argument("ids", nargs="+")
    sub.add_parser("tier", parents=[common]).add_argument("--message", required=True)
    sub.add_parser("mcp", parents=[common])
    a = ap.parse_args(argv)
    if a.cmd == "mcp":
        mcp_serve(a.root)
        return 0
    rec = Record(a.root)
    if a.cmd == "check":
        names = a.private_names or os.environ.get("TDR_PRIVATE_NAMES")
        probs = run_check(rec, a.gate, names)
        print("\n".join(fmt_problems(probs)) if probs else "ok")
        return 1 if probs else 0
    if a.cmd == "status":
        text = status_text(rec)
        (rec.root / "STATUS.md").write_bytes(text.encode("utf-8"))
        print(f"STATUS.md written, about {est_tokens(text)} tokens")
        return 0
    if a.cmd == "tier":
        probs = tier_problems(rec, Path(a.message).read_text(encoding="utf-8"))
        print("\n".join(fmt_problems(probs)) if probs else "ok")
        return 1 if probs else 0
    text, ok = {"get": lambda: get_text(rec, a.id), "refs": lambda: refs_text(rec, a.id),
                "brief": lambda: brief_text(rec, a.ids)}[a.cmd]()
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
