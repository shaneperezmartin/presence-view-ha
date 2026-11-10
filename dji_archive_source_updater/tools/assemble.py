"""Assemble a fixed public payload with an exact public baseline config template."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import stat

MODULE = Path(__file__).resolve().parents[1] / "updater.py"
spec = importlib.util.spec_from_file_location("updater", MODULE)
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def assemble(baseline_manifest, baseline_config, payload_source, payload_files, destination):
    baseline = json.loads(Path(baseline_manifest).read_bytes())
    # Baseline hashes and the disabled/empty public config are the only inputs.
    # Never accept a live/private config or an arbitrary config override.
    u.require(set(baseline) == {"version", "files"}, "Invalid baseline input")
    names = json.loads(Path(payload_files).read_bytes())
    u.require(isinstance(names, list) and len(names) == len(set(names)), "Invalid payload whitelist")
    u.require(all(isinstance(n, str) and u.SAFE_NAME.fullmatch(n) for n in names), "Unsafe payload filename")
    payload = {"version": None, "files": {}}
    contents = {}
    with u.Updater.directory(payload_source) as fd:
        for name in names:
            item = u.read_at(fd, name)
            u.require(item is not None, "Payload source missing")
            contents[name] = item[0]
            payload["files"][name] = u.file_info(item)
    configs = set(names) & set(u.CONFIGS)
    u.require(len(configs) == 1, "One payload config required")
    config = configs.pop()
    u.require(config == "config.json", "Public baseline template requires JSON config")
    cfg = u.parse_config(config, contents[config])
    payload["version"] = str(cfg["version"])
    manifest = {"schema": 2, "slug": cfg["slug"], "config": config, "baseline": baseline, "payload": payload}
    u.validate_manifest(manifest)
    baseline_path = Path(baseline_config)
    with u.Updater.directory(baseline_path.parent) as fd:
        baseline_item = u.read_at(fd, baseline_path.name)
        u.require(u.file_info(baseline_item) == baseline["files"][config], "Public baseline config does not match baseline manifest")
    baseline_cfg = u.parse_config(config, baseline_item[0])
    u.require(baseline_cfg.get("slug") == manifest["slug"] and
              str(baseline_cfg.get("version")) == baseline["version"],
              "Baseline config template identity/version mismatch")
    for template in (baseline_cfg, cfg):
        defaults = template.get("options")
        u.require(isinstance(defaults, dict) and defaults.get("transcript_export_enabled") is False and
                  defaults.get("transcript_export_token_sha256") == "", "Only disabled/empty public configs may be bundled")
    dest = Path(destination)
    u.require(not dest.exists() or not any(dest.iterdir()), "Output must be absent or empty")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "files").mkdir()
    (dest / "baseline-config.blob").write_bytes(baseline_item[0])
    (dest / "baseline-config.blob").chmod(baseline["files"][config]["mode"])
    for name, data in contents.items():
        (dest / "files" / (u.digest(name.encode()) + ".blob")).write_bytes(data)
        (dest / "files" / (u.digest(name.encode()) + ".blob")).chmod(payload["files"][name]["mode"])
    (dest / "manifest.json").write_bytes(u.canonical(manifest) + b"\n")
    u.Updater("/local_apps", dest)  # Full final payload hash/identity validation.
    return {"bundle_sha256": u.digest(u.canonical(manifest)), "files": len(names)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-manifest", required=True)
    p.add_argument("--baseline-config", required=True)
    p.add_argument("--payload-source", required=True)
    p.add_argument("--payload-files", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    print(json.dumps(assemble(args.baseline_manifest, args.baseline_config, args.payload_source, args.payload_files, args.output), sort_keys=True))
