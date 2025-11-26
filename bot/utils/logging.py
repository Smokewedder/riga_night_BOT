def log_exception(e):
    import traceback
    print(f"[ERROR] {e}")
    traceback.print_exc()
