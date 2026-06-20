import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.app_icon import render_app_icon


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "icon.icns"
    render_app_icon(1024).save(out, format="ICNS")


if __name__ == "__main__":
    main()
