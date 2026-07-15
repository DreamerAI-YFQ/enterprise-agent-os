"""Quick validation of competition datasets."""
import glob
import yaml

for f in sorted(glob.glob("benchmarks/competition/datasets/*.yaml")):
    data = yaml.safe_load(open(f, encoding="utf-8"))
    # Support different key names
    cases = []
    if isinstance(data, dict):
        for key in ("cases", "queries", "tasks"):
            if key in data:
                cases = data[key]
                break
    elif isinstance(data, list):
        cases = data

    name = f.split("\\")[-1]
    print(f"{name}: {len(cases)} cases")
    if cases:
        print(f"  First: {cases[0].get('case_id', '?')}")
        print(f"  Last:  {cases[-1].get('case_id', '?')}")
        # Count categories
        cats = {}
        for c in cases:
            cat = c.get("category", c.get("attack_type", "unknown"))
            cats[cat] = cats.get(cat, 0) + 1
        for cat, count in sorted(cats.items()):
            print(f"  {cat}: {count}")
    print()
