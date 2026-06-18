"""Allow `python -m hyejin_bot` to invoke the CLI."""

from __future__ import annotations

from hyejin_bot.cli.main import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
