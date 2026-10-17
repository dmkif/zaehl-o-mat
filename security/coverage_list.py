#!/usr/bin/env python3
"""Route x experiment coverage table (markdown on stdout). Needs no running rig.

Imports the backend FastAPI app with dummy DATABASE_URL / JWT_SECRET_KEY (no DB connection is opened), reads its
OpenAPI, then scans security/experiments/*.py:

* a route is "named" by an experiment when its exact path template appears in the body of a test function --
  matched on the static path segments in order, params as wildcards, so f-string and `{pid}`-style spellings hit
  (module-level tables/helpers are NOT attributed: too coarse);
* experiments that enumerate the live OpenAPI at run time (source mentions `openapi`/`enumerate_operations`) cover
  every route generically; they are listed as "generic" and do not count as route-specific coverage.

Usage: security/.venv/bin/python security/coverage_list.py   (exit 0 always; uncovered routes are flagged in the table)
"""
import ast
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ACCESS = {"A01", "A04", "A06", "A07"}          # access control / authn / races on identity
INPUT = {"A05", "A08", "A02", "A10", "UP"}     # input handling, upstream data, errors, uploads


def load_openapi() -> dict:
    os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@127.0.0.1:1/dummy")
    import tempfile
    os.environ["UPLOAD_PATH"] = tempfile.mkdtemp(prefix="cov-uploads-")
    os.environ["ENABLE_API_DOCS"] = "true"  # feature 010: docs are off by default; the table needs the OpenAPI
    os.environ["JWT_SECRET_KEY"] = "coverage-list-dummy-secret-0123456789abcdef"
    sys.path.insert(0, str(REPO / "backend"))
    from app.main import app  # noqa: E402
    return app.openapi()


def static_re(path: str) -> re.Pattern:
    """`/api/properties/{pid}/meters` -> regex over the static segments, params as any token.

    Exact-template match: the text must not continue with another path segment (so `/properties` does not hit
    `/properties/{id}`), and the leading `/api` is optional in the experiments' spelling.
    """
    segs = [s for s in path.strip("/").split("/") if s != "api"]
    parts = [r"[^/\s\"'`]+" if s.startswith("{") else re.escape(s) for s in segs]
    return re.compile(r"(?<![\w-])" + "/".join(parts) + r"(?!/?[\w{-])")


def scan_experiments():
    """-> list of (id, kind, source_text); kind 'test' or 'module'."""
    out = []
    for f in sorted((HERE / "experiments").glob("test_*.py")):
        src = f.read_text()
        tree = ast.parse(src)
        covered_lines = set()
        ids_in_file = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_E_"):
                m = re.match(r"test_(E_[A-Z0-9]+_\d+)", node.name)
                if not m:
                    continue
                eid = m.group(1).replace("_", "-")
                seg = ast.get_source_segment(src, node) or ""
                doc = ast.get_docstring(node) or ""
                out.append((eid, f.name, "test", node.name, seg + "\n" + doc))
                ids_in_file.append(eid)
                covered_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        rest = "\n".join(l for i, l in enumerate(src.splitlines(), 1) if i not in covered_lines)
        for eid in sorted(set(ids_in_file)):
            out.append((eid, f.name, "module", f.name, rest))
    return out


def main() -> int:
    spec = load_openapi()
    exps = scan_experiments()
    generic = {}  # id -> set(test names)
    generic_tests = set()
    for eid, fn, kind, name, text in exps:
        if kind == "test" and re.search(r"openapi|enumerate_operations", text):
            generic.setdefault(eid, set()).add(name)
            generic_tests.add((eid, name))

    ops = [(m.upper(), p) for p, item in spec["paths"].items() for m in item
           if m in ("get", "post", "put", "patch", "delete")]
    rows, uncovered, generic_only = [], [], []
    for method, path in sorted(ops, key=lambda x: (x[1], x[0])):
        rx = static_re(path)
        hit_access, hit_input = set(), set()
        for eid, fn, kind, name, text in exps:
            if kind != "test" or (eid, name) in generic_tests:
                continue
            # method-aware where cheap: a line naming the path and the lowercase verb call, else path alone
            if rx.search(text):
                cat = eid.split("-")[1]
                label = eid
                if cat in ACCESS:
                    hit_access.add(label)
                if cat in INPUT:
                    hit_input.add(label)
        # a path matched by the bare prefix of a longer route is a false hit; require full-template match only
        gen_a = sorted(i for i in generic if i.split("-")[1] in ACCESS)
        gen_i = sorted(i for i in generic if i.split("-")[1] in INPUT)
        if not hit_access and not hit_input:
            status = "NOT NAMED (generic only)"
            generic_only.append((method, path))
        elif not hit_access:
            status = "no route-specific access test"
        elif not hit_input:
            status = "no route-specific input test"
        else:
            status = "ok"
        if status.startswith("NOT"):
            uncovered.append((method, path))
        rows.append((method, path, sorted(hit_access), sorted(hit_input), gen_a, gen_i, status))

    print("# Route coverage (generated by security/coverage_list.py)\n")
    print(f"{len(ops)} operations in OpenAPI. `generic` = test enumerates OpenAPI at run time and hits every route.\n")
    print("| Method | Path | Access control (specific) | Input handling (specific) | Generic | Status |")
    print("|---|---|---|---|---|---|")
    for method, path, ha, hi, ga, gi, status in rows:
        gen = ", ".join(ga + gi)
        print(f"| {method} | `{path}` | {', '.join(ha) or '-'} | {', '.join(hi) or '-'} | {gen or '-'} | {status} |")
    print()
    if uncovered:
        print("## Routes with no route-specific experiment (only generic enumeration)\n")
        for m, p in uncovered:
            print(f"- {m} `{p}`")
    else:
        print("All routes are named by at least one experiment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
