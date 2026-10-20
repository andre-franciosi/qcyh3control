"""Daemon headless que expõe o controle QCY H3 à extensão do GNOME via D-Bus."""

from __future__ import annotations

import argparse
import asyncio
import logging
import queue
import signal
from pathlib import Path
from typing import Any

from dbus_fast.aio import MessageBus
from dbus_fast.constants import BusType, RequestNameReply
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method

from .ble_manager import BleManager
from .protocol import ProfileError, VendorProfile
from .rcsp import ANC_ORDER, EQ_BAND_LABELS, EQ_PRESET_IDS

LOG = logging.getLogger(__name__)
BUS_NAME = "io.github.qcyh3control"
OBJECT_PATH = "/io/github/qcyh3control/Control"
INTERFACE = "io.github.qcyh3control.Control"


class QcyControlService(ServiceInterface):
    """API de sessão para a extensão GNOME independente do QCY H3."""

    def __init__(self, manager: BleManager) -> None:
        super().__init__(INTERFACE)
        self._manager = manager
        self._connected = False
        self._audio_connected = False
        self._protocol_ready = False
        self._device_name = "QCY H3"
        self._status = "Aguardando o QCY H3"
        self._battery = -1
        self._mode = ""
        self._eq_offsets = [0] * len(EQ_BAND_LABELS)

    @dbus_property(access=PropertyAccess.READ)
    def Connected(self) -> "b":
        return self._connected

    @dbus_property(access=PropertyAccess.READ)
    def ProtocolReady(self) -> "b":
        return self._protocol_ready

    @dbus_property(access=PropertyAccess.READ)
    def AudioConnected(self) -> "b":
        return self._audio_connected

    @dbus_property(access=PropertyAccess.READ)
    def DeviceName(self) -> "s":
        return self._device_name

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return self._status

    @dbus_property(access=PropertyAccess.READ)
    def Battery(self) -> "i":
        return self._battery

    @dbus_property(access=PropertyAccess.READ)
    def Mode(self) -> "s":
        return self._mode

    @dbus_property(access=PropertyAccess.READ)
    def Equalizer(self) -> "ai":
        return self._eq_offsets

    def consume(self, event: dict[str, Any]) -> None:
        """Atualiza propriedades D-Bus a partir dos eventos do gerenciador BLE."""
        changed: dict[str, Any] = {}
        kind = event.get("event")
        if kind == "connected":
            if not self._connected:
                self._connected = True
                changed["Connected"] = self._connected
            address = event.get("address", "")
            self._device_name = f"QCY H3 · {address}" if address else "QCY H3"
            changed["DeviceName"] = self._device_name
            self._status = "Conectado via BLE"
            changed["Status"] = self._status
        elif kind == "disconnected":
            self._connected = False
            self._protocol_ready = False
            self._mode = ""
            changed.update(
                Connected=False,
                ProtocolReady=False,
                Mode="",
            )
            self._status = str(event.get("message", "Desconectado"))
            changed["Status"] = self._status
        elif kind == "system_devices":
            audio = next(
                (
                    device
                    for device in event.get("devices", [])
                    if device.get("kind") == "audio" and device.get("connected")
                ),
                None,
            )
            audio_connected = audio is not None
            if self._audio_connected != audio_connected:
                self._audio_connected = audio_connected
                changed["AudioConnected"] = audio_connected
            if audio is not None:
                name = str(audio.get("name", "QCY H3"))
                if name != self._device_name:
                    self._device_name = name
                    changed["DeviceName"] = name
        elif kind == "protocol_ready":
            self._protocol_ready = True
            changed["ProtocolReady"] = True
            self._status = "QCY H3 pronto"
            changed["Status"] = self._status
        elif kind == "protocol_unavailable":
            self._protocol_ready = False
            changed["ProtocolReady"] = False
            self._status = str(event.get("message", "Protocolo RCSP indisponível"))
            changed["Status"] = self._status
        elif kind in ("status", "error"):
            self._status = str(event.get("message", ""))
            changed["Status"] = self._status
        elif kind == "battery":
            self._battery = int(event["percent"])
            changed["Battery"] = self._battery
        elif kind == "mode":
            self._mode = str(event["mode"])
            changed["Mode"] = self._mode
        elif kind == "eq":
            values = [int(value) for value in event["offsets"]]
            if len(values) == len(EQ_BAND_LABELS):
                self._eq_offsets = values
                changed["Equalizer"] = self._eq_offsets

        if changed:
            self.emit_properties_changed(changed)

    @method()
    def SetMode(self, mode: "s") -> "":
        if mode not in ANC_ORDER:
            raise ValueError(f"Modo ANC desconhecido: {mode}")
        self._manager.set_mode(mode)

    @method()
    def SetEQ(self, offsets: "ai", preset_id: "y") -> "":
        if len(offsets) != len(EQ_BAND_LABELS):
            raise ValueError(f"O equalizador precisa de {len(EQ_BAND_LABELS)} bandas")
        if preset_id not in EQ_PRESET_IDS:
            raise ValueError("Preset de equalizador inválido")
        self._manager.set_eq([int(value) for value in offsets], preset_id)

    @method()
    def Refresh(self) -> "":
        self._manager.refresh_state()

    @method()
    def Connect(self) -> "":
        self._manager.auto_connect()

    @method()
    def Scan(self) -> "":
        self._manager.scan()

    @method()
    def Disconnect(self) -> "":
        self._manager.disconnect()


def system_transition_action(
    audio_was_connected: bool,
    audio_connected: bool,
    ble_connected: bool,
) -> str | None:
    """Decide o que fazer quando o estado do áudio do sistema muda.

    Retorna "connect" quando o áudio acabou de conectar e o canal BLE ainda
    não está aberto, "disconnect" quando o áudio caiu e o canal BLE continua
    aberto (para não manter o fone preso à extensão), ou None.
    """
    if audio_connected and not audio_was_connected and not ble_connected:
        return "connect"
    if audio_was_connected and not audio_connected and ble_connected:
        return "disconnect"
    return None


async def _run(profile_path: Path) -> None:
    profile = VendorProfile.load(profile_path)
    manager = BleManager(profile)
    manager.start()
    service = QcyControlService(manager)
    bus: MessageBus | None = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        bus.export(OBJECT_PATH, service)
        reply = await bus.request_name(BUS_NAME)
        if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
            raise RuntimeError(f"Não foi possível adquirir o nome D-Bus {BUS_NAME}: {reply}")
        LOG.info("Serviço D-Bus disponível: %s %s", BUS_NAME, OBJECT_PATH)
        manager.auto_connect()
        last_discovery = loop.time()
        last_device_refresh = 0.0
        while not stop.is_set():
            # Drena eventos BLE sem bloquear o event loop de sessão.
            while True:
                try:
                    event = manager.events.get_nowait()
                except queue.Empty:
                    break
                audio_was_connected = service._audio_connected
                service.consume(event)
                if event.get("event") == "system_devices":
                    action = system_transition_action(
                        audio_was_connected,
                        service._audio_connected,
                        service._connected,
                    )
                    if action == "connect":
                        manager.auto_connect()
                        last_discovery = loop.time()
                    elif action == "disconnect":
                        # O Bluetooth do sistema caiu: encerra o canal BLE de
                        # controle para não manter o fone preso à extensão.
                        LOG.info("Áudio do sistema desconectado; encerrando o canal BLE de controle.")
                        manager.disconnect()
                if event.get("event") in ("log", "error"):
                    LOG.info("BLE: %s", event.get("message", ""))
            # Atualiza presença no Bluetooth de áudio mesmo com BLE já conectado.
            # Intervalo curto para o botão aparecer/sumir logo após conectar ou
            # desconectar o fone no sistema.
            now = loop.time()
            if now - last_device_refresh >= 2:
                manager.refresh_system_devices()
                last_device_refresh = now
            # Também tenta abrir o BLE se o usuário conectar o áudio depois do login.
            if service._audio_connected and not service._connected and now - last_discovery >= 30:
                manager.auto_connect()
                last_discovery = now
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.15)
            except asyncio.TimeoutError:
                pass
    finally:
        manager.stop()
        if bus is not None:
            bus.unexport(OBJECT_PATH, service)
            bus.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Daemon BLE headless do QCY H3 para a extensão GNOME")
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(__file__).resolve().parent / "profile.example.json",
        help="Caminho de perfil BLE (padrão: exemplo desabilitado)",
    )
    parser.add_argument("--debug", action="store_true", help="Ativa logs de diagnóstico")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run(args.profile.expanduser()))
    except (ProfileError, RuntimeError, OSError) as exc:
        LOG.error("Daemon QCY H3 encerrado: %s", exc)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
