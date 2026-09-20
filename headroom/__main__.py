"""python -m headroom 等价于 headroom CLI。"""

from headroom.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
