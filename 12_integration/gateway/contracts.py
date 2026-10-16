"""OpenAPI 3.1 contracts, generated from routes.py, and a check for breaking changes.

A change is breaking when a client written against the old contract can fail
against the new one. The rules, from the client's side:

    request  an operation removed; a parameter removed; a parameter added as required,
             or made required; a parameter's type changed; an allowed value removed;
             a body property made required, or no longer accepted
    response a property removed, or no longer required; a property's type changed;
             an enum value added (a client switching on it meets a value it never saw)

Adding an operation, an optional parameter or a response property is not breaking.
"""

from __future__ import annotations

import json
from pathlib import Path

from .routes import PROBLEM, ROUTES, VERSIONS

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def contract(version: str) -> dict:
    paths: dict = {}
    for r in (r for r in ROUTES if r["version"] == version):
        op = {"operationId": r["operationId"], "summary": r["summary"], "parameters": r["parameters"],
              "security": [{"bearer": [r["scope"]]}],
              "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": r["response"]}}},
                            **{code: {"description": d, "content": {"application/problem+json": {"schema": PROBLEM}}}
                               for code, d in (("401", "No valid token"), ("403", "Token lacks the scope"),
                                               ("429", "Rate limit or budget reached"), ("410", "Version sunset"))}}}
        if VERSIONS[version].get("deprecated"):
            op["deprecated"] = True
        if r.get("request"):
            op["requestBody"] = {"required": True, "content": {"application/json": {"schema": r["request"]}}}
        paths.setdefault(r["path"], {})[r["method"]] = op
    return {"openapi": "3.1.0",
            "info": {"title": "Transit operations agent API", "version": version},
            "servers": [{"url": f"/{version}"}],
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
            "paths": paths}


def committed(version: str) -> dict:
    return json.loads((CONTRACTS / f"openapi-{version}.json").read_text())


def write_contracts() -> None:
    CONTRACTS.mkdir(exist_ok=True)
    for v in VERSIONS:
        (CONTRACTS / f"openapi-{v}.json").write_text(json.dumps(contract(v), indent=2, sort_keys=True) + "\n")


def _ops(c: dict) -> dict:
    return {(m, p): op for p, methods in c["paths"].items() for m, op in methods.items()}


def _response_schema(op: dict) -> dict:
    return op["responses"]["200"]["content"]["application/json"]["schema"]


def breaking_changes(old: dict, new: dict) -> list[str]:
    """Every way a client of `old` can break against `new`. Empty means safe to ship as a minor change."""
    out = []
    old_ops, new_ops = _ops(old), _ops(new)
    for key, o in old_ops.items():
        where = f"{key[0].upper()} {key[1]}"
        n = new_ops.get(key)
        if n is None:
            out.append(f"{where}: operation removed")
            continue
        op_params = {(p["name"], p["in"]): p for p in o.get("parameters", [])}
        np_params = {(p["name"], p["in"]): p for p in n.get("parameters", [])}
        for k, p in op_params.items():
            q = np_params.get(k)
            if q is None:
                out.append(f"{where}: parameter {k[0]} removed")
                continue
            if q.get("required") and not p.get("required"):
                out.append(f"{where}: parameter {k[0]} is now required")
            if p["schema"].get("type") != q["schema"].get("type"):
                out.append(f"{where}: parameter {k[0]} type changed")
            lost = set(p["schema"].get("enum", [])) - set(q["schema"].get("enum", p["schema"].get("enum", [])))
            if lost:
                out.append(f"{where}: parameter {k[0]} no longer accepts {sorted(lost)}")
        for k, q in np_params.items():
            if k not in op_params and q.get("required"):
                out.append(f"{where}: new required parameter {k[0]}")
        ob = (o.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
        nb = (n.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
        if ob and nb:
            for prop in set(nb.get("required", [])) - set(ob.get("required", [])):
                out.append(f"{where}: request property {prop} is now required")
            if nb.get("additionalProperties") is False:
                for prop in set(ob.get("properties", {})) - set(nb.get("properties", {})):
                    out.append(f"{where}: request property {prop} is no longer accepted")
        rs, ns = _response_schema(o), _response_schema(n)
        for prop, s in rs.get("properties", {}).items():
            t = ns.get("properties", {}).get(prop)
            if t is None:
                out.append(f"{where}: response property {prop} removed")
                continue
            if s.get("type") != t.get("type"):
                out.append(f"{where}: response property {prop} type changed")
            if prop in rs.get("required", []) and prop not in ns.get("required", []):
                out.append(f"{where}: response property {prop} may now be missing")
            added = set(t.get("enum", [])) - set(s.get("enum", t.get("enum", [])))
            if added:
                out.append(f"{where}: response property {prop} has new values {sorted(added)}")
    return out
