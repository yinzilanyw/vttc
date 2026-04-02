from svmap.demos.run_demo import run_demo


if __name__ == "__main__":
    try:
        run_demo()
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
