import os

from svmap.demos.case_study import run_case_study


if __name__ == "__main__":
    try:
        run_case_study(query=os.getenv("DEMO_QUERY"))
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
