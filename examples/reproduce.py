"""Short offline demonstration using saved real-agent traces, not new inference."""
import argparse
import json
from pathlib import Path
import shutil
import time

from web_research_sim.bundles import import_snapshot
from web_research_sim.comparison_verify import verify_comparison
from web_research_sim.core import Store
from web_research_sim.integrity import verify_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New directory for the local world and report")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent / "mossbank"
    args.output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    print("Saved real Codex/Claude runs; fictional evidence; no new model or web calls.")
    store = Store(args.output / "workspace")
    imported = import_snapshot(store, (source / "world.zip").read_bytes())
    print(f"1. Imported {imported['count']} source into a new local workspace.")
    integrity = verify_snapshot(store.objects, imported['id'])
    if not integrity['passed']:
        raise RuntimeError("Frozen source integrity failed; comparison not verified.")
    print("2. Verified the frozen manifest and source bytes.")
    comparison = json.loads((source / "comparison.json").read_text())
    verified = verify_comparison(store, comparison)
    if not verified['matches']:
        raise RuntimeError("Saved comparison metrics do not reproduce.")
    print("3. Recomputed both saved runs' evidence checks and comparison metrics.")
    for name in ("comparison.json", "comparison.html"):
        shutil.copyfile(source / name, args.output / name)
    receipt = {'schema': 'offline-demo-v1', 'elapsed_seconds': time.monotonic()-start,
               'snapshot_id': imported['id'], 'integrity': integrity,
               'comparison': verified, 'new_inference': False,
               'limitations': ['Saved traces are not provider attestations.',
                              'Subscription monetary costs and semantic correctness are not established.']}
    (args.output / "verification.json").write_text(json.dumps(receipt, indent=2)+"\n")
    print(f"4. Open {(args.output / 'comparison.html').resolve()} to inspect answers and quotes.")
    print("For fresh LLM research, follow docs/first-session.txt and docs/mcp.txt.")
    print("Keep the completed comparison outside the evidence available to your agent.")


if __name__ == '__main__':
    main()
