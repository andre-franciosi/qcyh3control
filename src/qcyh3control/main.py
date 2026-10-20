"""Ponto de entrada do QCY H3 Control."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from .app import QcyControlApp
from .ble_manager import BleManager
from .protocol import ProfileError, VendorProfile


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspeção BLE experimental para QCY H3")
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(__file__).resolve().parent / "profile.example.json",
        help="Caminho para um perfil JSON de protocolo BLE (padrão: perfil de exemplo desabilitado)",
    )
    parser.add_argument("--debug", action="store_true", help="Ativa logs de diagnóstico")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    root = tk.Tk()
    try:
        profile = VendorProfile.load(args.profile)
        manager = BleManager(profile)
        manager.start()
        QcyControlApp(root, manager, profile)
    except (ProfileError, RuntimeError) as exc:
        messagebox.showerror("Não foi possível iniciar", str(exc), parent=root)
        root.destroy()
        raise SystemExit(2) from exc
    root.mainloop()


if __name__ == "__main__":
    main()
