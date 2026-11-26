#!/usr/bin/env python3
"""Arranca el bot. Antes, comprueba que no falta nada por configurar."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from usalbot.bot import construir
from usalbot.config import cfg


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s · %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # El .gitignore ya cubre bot.log: rota para que no crezca sin fin.
    try:
        raiz = logging.getLogger()
        if not any(isinstance(h, RotatingFileHandler) for h in raiz.handlers):
            fichero = RotatingFileHandler("bot.log", maxBytes=1_000_000,
                                          backupCount=3, encoding="utf-8")
            fichero.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s · %(message)s", "%H:%M:%S"))
            raiz.addHandler(fichero)
    except OSError:
        pass

    faltan = cfg.missing()
    if faltan:
        print("Falta configurar esto en el fichero .env:\n")
        for f in faltan:
            print(f"  · {f}")
        print("\nSi aún no tienes .env:  python setup_wizard.py init")
        return 1

    print("Bot en marcha. Ctrl+C para parar.")
    construir().run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
