"""Allow `python -m daeyeon_bot` to invoke the CLI."""

from __future__ import annotations

from daeyeon_bot.cli.main import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
