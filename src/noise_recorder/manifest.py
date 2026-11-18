"""Seal a run folder: SHA-256 manifest, sha256sum file, RFC 3161 timestamp, optional GPG signature.

Integrity model (see docs/integrity.md):
- manifest.json lists every capture-time file with its SHA-256 and size, plus the
  recording metadata. Its own hash is what gets timestamped and signed.
- SHA256SUMS is the same list in `sha256sum -c` format, so anyone can check the files
  with coreutils alone.
- timestamp/manifest.tsr is an RFC 3161 token from a public Time Stamping Authority over
  the SHA-512 of manifest.json: independent proof the manifest (and so the audio hash)
  existed no later than the token's time. Only the hash leaves the machine.
- manifest.json.asc is an optional detached GPG signature by the operator.
- Later work (AI evaluations, re-analysis) goes in addenda/<name>/ with its own
  addendum.json that pins the sealed manifest's hash, so the seal is never rewritten.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import APP_ID, __version__

SEAL_FILES = {"manifest.json", "SHA256SUMS", "manifest.json.asc"}
TSA_CERTS = {
    # FreeTSA publishes its root and signing certs here; saved beside the token so the
    # run verifies offline later.
    "https://freetsa.org/tsr": ("https://freetsa.org/files/cacert.pem", "https://freetsa.org/files/tsa.crt"),
}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _role(rel: str) -> str:
    if rel.endswith((".wav", ".flac")):
        return "audio-original"
    if rel == "recording.json":
        return "recording-metadata"
    if rel.startswith("analysis/"):
        return "analysis"
    return "other"


def tool_info() -> dict:
    return {"name": APP_ID, "version": __version__, "python": sys.version.split()[0],
            "platform": platform.platform(), "host": platform.node()}


def _files_under(root: Path, exclude_dirs=("addenda", "timestamp")) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and rel.parts[0] not in exclude_dirs and rel.as_posix() not in SEAL_FILES:
            out.append(p)
    return out


def _timestamp(target: Path, tsa_url: str, outdir: Path) -> dict:
    """RFC 3161 token over `target`. Needs openssl; returns a status dict, never raises."""
    outdir.mkdir(parents=True, exist_ok=True)
    if not shutil.which("openssl"):
        return {"status": "skipped", "reason": "openssl not installed"}
    tsq, tsr = outdir / f"{target.name}.tsq", outdir / f"{target.name}.tsr"
    try:
        subprocess.run(["openssl", "ts", "-query", "-data", str(target), "-sha512", "-cert", "-out", str(tsq)],
                       check=True, capture_output=True, timeout=20)
        r = requests.post(tsa_url, data=tsq.read_bytes(),
                          headers={"Content-Type": "application/timestamp-query"}, timeout=30)
        r.raise_for_status()
        tsr.write_bytes(r.content)
        text = subprocess.run(["openssl", "ts", "-reply", "-in", str(tsr), "-text"],
                              capture_output=True, text=True, timeout=20).stdout
        gen = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("Time stamp:")), None)
        info = {"status": "ok", "tsa_url": tsa_url, "hash_algorithm": "sha512", "gen_time": gen,
                "query": tsq.name, "reply": tsr.name}
        for url in TSA_CERTS.get(tsa_url, ()):
            dest = outdir / Path(url).name
            if not dest.exists():
                c = requests.get(url, timeout=30)
                c.raise_for_status()
                dest.write_bytes(c.content)
        if tsa_url in TSA_CERTS:
            info["certs"] = [Path(u).name for u in TSA_CERTS[tsa_url]]
        info["verify"] = verify_timestamp(target, outdir)
        return info
    except Exception as e:
        return {"status": "failed", "tsa_url": tsa_url, "error": str(e)}


def verify_timestamp(target: Path, tsdir: Path) -> dict:
    tsr = tsdir / f"{target.name}.tsr"
    if not tsr.exists():
        return {"status": "absent"}
    cmd = ["openssl", "ts", "-verify", "-data", str(target), "-in", str(tsr)]
    ca, untrusted = tsdir / "cacert.pem", tsdir / "tsa.crt"
    if ca.exists():
        cmd += ["-CAfile", str(ca)]
        if untrusted.exists():
            cmd += ["-untrusted", str(untrusted)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    ok = r.returncode == 0 and "Verification: OK" in (r.stdout + r.stderr)
    return {"status": "ok" if ok else "failed", "output": (r.stdout + r.stderr).strip()[-400:]}


def _gpg_sign(target: Path, key: str) -> dict:
    if not key:
        return {"status": "skipped", "reason": "no signing key configured"}
    if not shutil.which("gpg"):
        return {"status": "skipped", "reason": "gpg not installed"}
    r = subprocess.run(["gpg", "--batch", "--yes", "--armor", "--detach-sign", "-u", key,
                        "-o", str(target) + ".asc", str(target)], capture_output=True, text=True, timeout=120)
    return {"status": "ok", "key": key} if r.returncode == 0 else {"status": "failed", "error": r.stderr[-300:]}


def seal(run_dir: Path, recording_meta: dict, cfg: dict) -> dict:
    """Write manifest.json + SHA256SUMS, then timestamp and sign. Returns a report dict."""
    run_dir = Path(run_dir)
    files = [{"path": p.relative_to(run_dir).as_posix(), "sha256": sha256_file(p), "bytes": p.stat().st_size,
              "role": _role(p.relative_to(run_dir).as_posix())} for p in _files_under(run_dir)]
    manifest = {
        "schema": f"{APP_ID}/manifest/v1",
        "sealed_utc": datetime.now(timezone.utc).isoformat(),
        "tool": tool_info(),
        "recording": recording_meta,
        "files": files,
        "hash_algorithm": "sha256",
    }
    mpath = run_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    mhash = sha256_file(mpath)
    (run_dir / "SHA256SUMS").write_text(
        "".join(f"{f['sha256']}  {f['path']}\n" for f in files) + f"{mhash}  manifest.json\n")
    report = {"manifest_sha256": mhash, "files": len(files)}
    report["timestamp"] = (_timestamp(mpath, cfg.get("rfc3161_tsa_url", ""), run_dir / "timestamp")
                           if cfg.get("rfc3161_enabled") else {"status": "disabled"})
    report["signature"] = _gpg_sign(mpath, cfg.get("gpg_sign_key", ""))
    (run_dir / "timestamp").mkdir(exist_ok=True)
    (run_dir / "timestamp" / "seal-report.json").write_text(json.dumps(report, indent=2))
    return report


def add_addendum(run_dir: Path, name: str, cfg: dict, extra: dict | None = None) -> dict:
    """Seal addenda/<name>/: hashes of its files, pinned to the run's manifest hash."""
    run_dir = Path(run_dir)
    adir = run_dir / "addenda" / name
    mpath = run_dir / "manifest.json"
    files = [{"path": p.relative_to(adir).as_posix(), "sha256": sha256_file(p), "bytes": p.stat().st_size}
             for p in sorted(adir.rglob("*")) if p.is_file() and p.name not in ("addendum.json", "addendum.json.asc")
             and "timestamp" not in p.relative_to(adir).parts]
    doc = {
        "schema": f"{APP_ID}/addendum/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "tool": tool_info(),
        "parent_manifest_sha256": sha256_file(mpath) if mpath.exists() else None,
        "files": files,
        **(extra or {}),
    }
    apath = adir / "addendum.json"
    apath.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    report = {"addendum_sha256": sha256_file(apath)}
    report["timestamp"] = (_timestamp(apath, cfg.get("rfc3161_tsa_url", ""), adir / "timestamp")
                           if cfg.get("rfc3161_enabled") else {"status": "disabled"})
    report["signature"] = _gpg_sign(apath, cfg.get("gpg_sign_key", ""))
    return report


def _gpg_verify(target: Path) -> dict:
    sig = Path(str(target) + ".asc")
    if not sig.exists():
        return {"status": "absent"}
    r = subprocess.run(["gpg", "--batch", "--verify", str(sig), str(target)], capture_output=True, text=True, timeout=60)
    signer = next((l.split("Good signature from", 1)[1].strip() for l in r.stderr.splitlines()
                   if "Good signature from" in l), None)
    return {"status": "ok" if r.returncode == 0 else "failed", "signer": signer}


def verify(run_dir: Path) -> dict:
    """Re-hash everything and check timestamp/signature. `ok` is the overall verdict."""
    run_dir = Path(run_dir)
    mpath = run_dir / "manifest.json"
    if not mpath.exists():
        return {"ok": False, "error": "manifest.json missing"}
    m = json.loads(mpath.read_text())
    mhash = sha256_file(mpath)
    problems, checked = [], []
    for f in m["files"]:
        p = run_dir / f["path"]
        if not p.exists():
            problems.append(f"missing: {f['path']}")
            continue
        actual = sha256_file(p)
        if actual != f["sha256"]:
            problems.append(f"hash mismatch: {f['path']}")
        checked.append({"path": f["path"], "ok": actual == f["sha256"]})
    listed = {f["path"] for f in m["files"]}
    unlisted = [p.relative_to(run_dir).as_posix() for p in _files_under(run_dir)
                if p.relative_to(run_dir).as_posix() not in listed]
    sums = run_dir / "SHA256SUMS"
    if sums.exists() and f"{mhash}  manifest.json" not in sums.read_text():
        problems.append("SHA256SUMS does not match manifest.json")
    ts = verify_timestamp(mpath, run_dir / "timestamp")
    if ts["status"] == "failed":
        problems.append("RFC 3161 timestamp does not verify against manifest.json")
    sig = _gpg_verify(mpath)
    if sig["status"] == "failed":
        problems.append("GPG signature does not verify")

    addenda = []
    for a in sorted((run_dir / "addenda").glob("*/addendum.json")) if (run_dir / "addenda").exists() else []:
        doc = json.loads(a.read_text())
        bad = [f["path"] for f in doc["files"]
               if not (a.parent / f["path"]).exists() or sha256_file(a.parent / f["path"]) != f["sha256"]]
        pinned = doc.get("parent_manifest_sha256") == mhash
        ats = verify_timestamp(a, a.parent / "timestamp")
        entry = {"name": a.parent.name, "files_ok": not bad, "bad_files": bad, "pinned_to_manifest": pinned,
                 "timestamp": ats["status"], "signature": _gpg_verify(a)["status"]}
        if bad or not pinned or ats["status"] == "failed":
            problems.append(f"addendum {a.parent.name} failed verification")
        addenda.append(entry)

    return {
        "ok": not problems,
        "run": run_dir.name,
        "manifest_sha256": mhash,
        "sealed_utc": m.get("sealed_utc"),
        "files_checked": len(checked),
        "problems": problems,
        "unlisted_files": unlisted,
        "timestamp": ts,
        "signature": sig,
        "addenda": addenda,
    }
