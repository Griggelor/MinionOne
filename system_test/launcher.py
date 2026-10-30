"""Frozen Windows entry point; child networking processes use freeze_support."""
if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    from minionone.pilot_cli import main
    raise SystemExit(main())
