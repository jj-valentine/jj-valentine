#!/usr/bin/env python3
"""
sync_model.py — resolve SUMMARY_MODEL from ops/map/model-matrix.md and set the repo variable.

The ✳ summary is a cheap, high-frequency formatting/classification task, so it uses the
Efficiency-mode "Classification / routing" model from the matrix (currently Haiku 4.5),
resolved to a concrete model ID via the matrix's Model Inventory. No model ID is hardcoded
(ops rule: "no default model"). On ANY fetch/parse failure, the existing SUMMARY_MODEL is left
untouched (last-known-good) so a matrix-format change can never break the header pipeline.

CI:    python3 .github/scripts/sync_model.py                          # fetch ops matrix, set var
Local: python3 .github/scripts/sync_model.py --file <model-matrix.md> # dry-run, just print
"""
import os
import re
import subprocess
import sys
import urllib.request

OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", "jj-valentine")
REPO  = os.environ.get("GITHUB_REPOSITORY", "%s/jj-valentine" % OWNER)


def fetch_matrix(token):
    url = "https://api.github.com/repos/%s/ops/contents/map/model-matrix.md" % OWNER
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer %s" % token,
        "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jjv-model-sync",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


def name_to_id(md):
    """{model name (lower) -> id} from the Model Inventory rows (cells with a backticked id)."""
    out = {}
    for name, mid in re.findall(r"\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|", md):
        out[name.strip().lower()] = mid.strip()
    return out


def efficiency_classification_model(md):
    """Primary model name in the Efficiency Mode 'Classification' row, or None."""
    parts = md.split("## Efficiency Mode", 1)
    if len(parts) < 2:
        return None
    body = parts[1].split("\n## ", 1)[0]
    for line in body.splitlines():
        if "|" in line and "classification" in line.lower():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[1]:
                return cells[1]   # Primary column
    return None


def resolve(md):
    name = efficiency_classification_model(md)
    if not name:
        return None
    return name_to_id(md).get(name.lower())


def main():
    if "--file" in sys.argv:
        with open(sys.argv[sys.argv.index("--file") + 1]) as f:
            model = resolve(f.read())
        print("resolved SUMMARY_MODEL = %r (dry-run, not set)" % model)
        sys.exit(0 if model else 1)

    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("error: GH_PAT required")
    try:
        model = resolve(fetch_matrix(token))
    except Exception as e:
        print("fetch/parse failed (%s) -> leaving SUMMARY_MODEL untouched" % e)
        return
    if not model:
        print("could not resolve a model id -> leaving SUMMARY_MODEL untouched")
        return
    print("setting SUMMARY_MODEL = %s" % model)
    subprocess.run(["gh", "variable", "set", "SUMMARY_MODEL", "--body", model, "--repo", REPO],
                   check=True, env=dict(os.environ, GH_TOKEN=token))


if __name__ == "__main__":
    main()
