"""Gerencia Bleak em uma thread asyncio separada da interface Tk."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice

from .protocol import VendorProfile
from .rcsp import (
    ADV_BATTERY,
    ANC_MODES,
    EQ_BAND_FREQS,
    EQ_PRESET_OFFSETS,
    GET_ANC_PAYLOAD,
    GET_EQ_PAYLOAD,
    MODE_PAYLOADS,
    OP_CUSTOM,
    OP_GET_ADV_INFO,
    OP_NOTIFY_COMMUNICATION_WAY,
    OP_SET_ADV_INFO,
    RCSPPacket,
    decode_anc_mode,
    decode_battery,
    decode_eq_response,
    decode_packet,
    encode_command,
    encode_eq_command,
    HEADER as RCSP_HEADER,
)

LOG = logging.getLogger(__name__)
_BLUETOOTH_ADDRESS = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_INFO_UUID = re.compile(
    r"\(([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\)"
)
_QCY_H3_NAME = re.compile(
    r"qcy[\s_-]*h3(?:[\s_-]*(?:anc|le|ble|app))?(?:[\s_-]*headphones)?",
    re.IGNORECASE,
)
_BLE_NAME_HINT = re.compile(r"(?:app|le|ble)", re.IGNORECASE)
RCSP_SERVICE_UUID = "0000a002-0000-1000-8000-00805f9b34fb"
_CLASSIC_AUDIO_UUIDS = {
    "0000110a-0000-1000-8000-00805f9b34fb",
    "0000110b-0000-1000-8000-00805f9b34fb",
    "0000111e-0000-1000-8000-00805f9b34fb",
}


def is_qcy_h3_name(name: str) -> bool:
    """Aceita o nome QCY H3 e sufixos como ANC, sem casar outros modelos QCY."""
    return bool(_QCY_H3_NAME.fullmatch(name.strip()))


def classify_qcy_device(
    name: str, uuids: Iterable[str] = (), icon: str = ""
) -> str | None:
    """Separa o fone clássico de áudio do endpoint BLE de controle."""
    if not is_qcy_h3_name(name):
        return None
    lowered = {uuid.lower() for uuid in uuids}
    if RCSP_SERVICE_UUID in lowered or _BLE_NAME_HINT.search(name):
        return "ble"
    if icon == "audio-headset" or lowered & _CLASSIC_AUDIO_UUIDS:
        return "audio"
    return "audio"


def parse_bluetoothctl_device_lines(text: str) -> list[tuple[str, str]]:
    """Lê `bluetoothctl devices`, ignorando códigos de cor."""
    devices: list[tuple[str, str]] = []
    for line in _ANSI.sub("", text).splitlines():
        fields = line.strip().split(maxsplit=2)
        if (
            len(fields) == 3
            and fields[0].lower() == "device"
            and _BLUETOOTH_ADDRESS.fullmatch(fields[1])
        ):
            devices.append((fields[1], fields[2]))
    return devices


def parse_bluetoothctl_info(text: str) -> dict[str, Any]:
    """Extrai nome, conexão e UUIDs da saída de `bluetoothctl info`."""
    name = ""
    alias = ""
    icon = ""
    connected = False
    paired = False
    uuids: list[str] = []
    for line in _ANSI.sub("", text).splitlines():
        stripped = line.strip()
        if stripped.startswith("Name:"):
            name = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Alias:"):
            alias = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Icon:"):
            icon = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Connected:"):
            connected = stripped.split(":", 1)[1].strip().lower() == "yes"
        elif stripped.startswith("Paired:"):
            paired = stripped.split(":", 1)[1].strip().lower() == "yes"
        elif stripped.startswith("UUID:"):
            match = _INFO_UUID.search(stripped)
            if match:
                uuids.append(match.group(1))
    return {
        "name": name,
        "alias": alias or name,
        "icon": icon,
        "connected": connected,
        "paired": paired,
        "uuids": uuids,
    }


@dataclass(frozen=True)
class BluezDevice:
    """Dispositivo QCY H3 já presente no BlueZ, com ou sem anúncio BLE novo."""

    path: str
    address: str
    name: str
    connected: bool
    paired: bool
    uuids: tuple[str, ...]
    icon: str
    adapter: str
    kind: str
    battery: int | None = None

    def row(self) -> dict[str, Any]:
        battery = f" · {self.battery}%" if self.battery is not None else ""
        if self.kind == "audio" and self.connected:
            label = f"{self.name} · conectado no sistema{battery}"
            rssi = "A2DP"
        elif self.kind == "ble" and self.connected:
            label = f"{self.name} · BLE do sistema"
            rssi = "BLE"
        elif self.kind == "ble":
            label = f"{self.name} · BLE conhecido"
            rssi = "—"
        else:
            label = self.name
            rssi = "—"
        return {
            "name": label,
            "address": self.address,
            "rssi": rssi,
            "kind": self.kind,
            "connected": self.connected,
        }


def _unwrap_dbus(value: Any) -> Any:
    try:
        from dbus_fast.signature import Variant
    except ImportError:
        Variant = ()  # type: ignore[assignment]
    if Variant and isinstance(value, Variant):
        return _unwrap_dbus(value.value)
    if isinstance(value, dict):
        return {str(key): _unwrap_dbus(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_unwrap_dbus(item) for item in value]
    return value


def _kind_for_names(name: str, alias: str, uuids: Iterable[str], icon: str) -> str | None:
    return classify_qcy_device(name, uuids, icon) or classify_qcy_device(alias, uuids, icon)


class BleManager:
    """Executa operações BLE em um loop dedicado e publica eventos numa fila."""

    #: Intervalo entre tentativas de reconexão automática (segundos).
    RECONNECT_DELAYS = (2.0, 5.0, 10.0, 30.0)

    def __init__(self, profile: VendorProfile) -> None:
        self.profile = profile
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop, name="qcyh3-ble-loop", daemon=True
        )
        self._client: BleakClient | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._rcsp_lock: asyncio.Lock | None = None
        self._rcsp_sequence = 0
        self._rcsp_pending: dict[int, asyncio.Future[RCSPPacket]] = {}
        self._rcsp_rx_buffer = bytearray()
        self._rcsp_write_characteristic: BleakGATTCharacteristic | None = None
        self._rcsp_notify_characteristic: BleakGATTCharacteristic | None = None
        self._rcsp_notify_active = False
        self._rcsp_ready = False
        self._closing = False
        self._auto_reconnect = True
        self._reconnect_task: asyncio.Task[None] | None = None
        self._last_address: str | None = None
        self._last_name: str | None = None
        self._manual_disconnect = False

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("O loop BLE não iniciou a tempo.")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._rcsp_lock = asyncio.Lock()
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def _emit(self, event: str, **data: Any) -> None:
        self.events.put({"event": event, **data})

    def _submit(self, coroutine: Any) -> None:
        if self._closing or self._loop is None or not self._loop.is_running():
            self._emit("error", message="O gerenciador Bluetooth está encerrando.")
            coroutine.close()
            return
        asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def scan(self) -> None:
        self._submit(self._scan())

    def auto_connect(self) -> None:
        """Reconhece o QCY H3 já conectado no Bluetooth do sistema e abre o canal BLE."""
        self._submit(self._auto_connect())

    def refresh_system_devices(self) -> None:
        """Atualiza a lista de dispositivos QCY conectados no BlueZ sem iniciar scan."""
        self._submit(self._refresh_system_devices())

    async def _refresh_system_devices(self) -> None:
        try:
            await self._publish_system_qcy()
        except Exception as exc:
            LOG.debug("Falha atualizando estado do BlueZ: %s", exc)

    async def _scan(self) -> None:
        await self._find_and_connect(manual=True)

    async def _auto_connect(self) -> None:
        await self._find_and_connect(manual=False)

    def cancel_scan(self) -> None:
        self._submit(self._cancel_scan())

    async def _cancel_scan(self) -> None:
        if self._scan_task is not None and not self._scan_task.done():
            self._scan_task.cancel()
        else:
            self._emit("status", message="Nenhuma busca BLE está em andamento.")

    async def _find_and_connect(self, *, manual: bool) -> None:
        """Reconhece o QCY H3 já conectado no BlueZ e só então procura anúncios."""
        if self._scan_task is not None and not self._scan_task.done():
            self._emit("status", message="Uma busca BLE já está em andamento.")
            return
        self._scan_task = asyncio.current_task()
        try:
            audio, ble = await self._publish_system_qcy()
            saved_address = self._load_saved_address()
            battery = next((item.battery for item in audio if item.battery is not None), None)
            battery_text = f", bateria {battery}%" if battery is not None else ""
            if audio:
                detail = ", ".join(f"{item.name} ({item.address})" for item in audio)
                self._emit(
                    "log",
                    message=f"QCY H3 conectado no Bluetooth do sistema: {detail}{battery_text}.",
                )
                self._emit(
                    "status",
                    message=(
                        f"QCY H3 conectado no Bluetooth do sistema ({audio[0].address})"
                        f"{battery_text}. Abrindo o canal BLE de controle…"
                    ),
                )
            elif ble:
                self._emit("status", message="Endpoint BLE do QCY H3 encontrado no BlueZ. Conectando ao controle…")
            else:
                self._emit(
                    "status",
                    message="QCY H3 não aparece conectado no Bluetooth do sistema. Procurando anúncios BLE…",
                )

            if ble and await self._connect_known_ble(ble):
                if audio:
                    self._emit(
                        "status",
                        message=(
                            "Controle BLE ativo. O áudio continua conectado no Bluetooth do sistema "
                            f"({audio[0].address}){battery_text}."
                        ),
                    )
                return

            if audio and not ble:
                self._emit(
                    "log",
                    message=(
                        "O fone está no áudio do sistema, mas o endpoint BLE (QCY H3-APP) ainda não está no BlueZ. "
                        "Procurando anúncio…"
                    ),
                )
            timeout = 8.0 if manual else 10.0
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
            self._emit("devices", devices=self._format_devices(found))
            targets = []
            for device, advertisement in found.values():
                name = device.name or advertisement.local_name or "Dispositivo sem nome"
                if is_qcy_h3_name(name):
                    targets.append((device, name, advertisement.rssi))
            targets.sort(
                key=lambda item: (
                    item[0].address.lower() != (saved_address or "").lower(),
                    -(item[2] if item[2] is not None else -999),
                )
            )
            for device, name, _rssi in targets:
                self._emit("log", message=f"QCY H3 localizado por anúncio BLE: {name} ({device.address}).")
                if await self._connect(
                    device.address,
                    report_failure=False,
                    device_name=name,
                    ble_device=device,
                ):
                    if audio:
                        self._emit(
                            "status",
                            message=(
                                "Controle BLE ativo. O áudio continua conectado no Bluetooth do sistema "
                                f"({audio[0].address}){battery_text}."
                            ),
                        )
                    return
            if targets:
                self._emit("status", message="QCY H3 anunciado, mas a conexão BLE falhou. Veja Atividade.")
                return
            if audio:
                self._emit(
                    "status",
                    message=(
                        f"QCY H3 está conectado no Bluetooth do sistema ({audio[0].address}){battery_text}, "
                        "mas o canal BLE de controle não abriu. O áudio A2DP não foi desconectado."
                    ),
                )
                return
            self._emit(
                "status",
                message=(
                    "Nenhum QCY H3 conectado no Bluetooth do sistema e nenhum anúncio BLE encontrado. "
                    "Confirme se ele está ligado e conectado no GNOME."
                    if not manual
                    else "Busca concluída sem anúncio do QCY H3. Se ele está no áudio do sistema, ele aparecerá na lista."
                ),
            )
        except asyncio.CancelledError:
            self._emit("status", message="Busca BLE cancelada.")
        except Exception as exc:
            LOG.exception("Falha ao localizar o QCY H3")
            self._emit("error", message=f"Falha ao procurar o QCY H3: {exc}")
        finally:
            self._scan_task = None

    @staticmethod
    def _format_devices(found: dict[str, Any]) -> list[dict[str, Any]]:
        devices = []
        for value in found.values():
            device, advertisement = value
            devices.append(
                {
                    "name": device.name or advertisement.local_name or "Dispositivo sem nome",
                    "address": device.address,
                    "rssi": advertisement.rssi,
                }
            )
        devices.sort(key=lambda item: (item["name"].lower(), item["address"]))
        return devices

    def connect(self, address: str, device_name: str | None = None) -> None:
        self._submit(self._connect_requested(address, device_name))

    async def _connect_requested(self, address: str, device_name: str | None) -> None:
        """Conecta pelo objeto BlueZ quando o endereço já é o fone do sistema."""
        known = await self._load_qcy_devices()
        selected = next((item for item in known if item.address.lower() == address.lower()), None)
        ble_targets = [item for item in known if item.kind == "ble"]
        if selected is not None and selected.kind == "audio":
            self._emit(
                "log",
                message=(
                    f"{selected.name} ({selected.address}) é o áudio A2DP do sistema, não o canal de controle. "
                    "Abrindo o endpoint BLE."
                ),
            )
            if ble_targets and await self._connect_known_ble(ble_targets):
                self._emit(
                    "status",
                    message=f"Controle BLE ativo. O áudio continua no Bluetooth do sistema ({selected.address}).",
                )
                return
            self._emit(
                "status",
                message=(
                    f"QCY H3 está conectado no Bluetooth do sistema ({selected.address}), "
                    "mas o canal BLE de controle não abriu."
                ),
            )
            return
        if selected is not None and selected.kind == "ble":
            await self._connect(
                selected.address,
                device_name=selected.name,
                ble_device=self._as_ble_device(selected),
            )
            return
        await self._connect(address, device_name=device_name)

    async def _bluetoothctl(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=4.0)
        return _ANSI.sub("", stdout.decode("utf-8", errors="replace"))

    async def _load_qcy_devices(self) -> list[BluezDevice]:
        try:
            return await self._load_qcy_devices_dbus()
        except Exception as exc:
            LOG.info("Consulta D-Bus do BlueZ indisponível: %s", exc)
            self._emit("log", message=f"Consulta D-Bus do BlueZ falhou ({exc}); usando bluetoothctl.")
            return await self._load_qcy_devices_bluetoothctl()

    async def _load_qcy_devices_dbus(self) -> list[BluezDevice]:
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType, MessageType
        from dbus_fast.message import Message

        bus = await asyncio.wait_for(MessageBus(bus_type=BusType.SYSTEM).connect(), timeout=4.0)
        try:
            reply = await asyncio.wait_for(
                bus.call(
                    Message(
                        destination="org.bluez",
                        path="/",
                        interface="org.freedesktop.DBus.ObjectManager",
                        member="GetManagedObjects",
                    )
                ),
                timeout=4.0,
            )
            if reply.message_type != MessageType.METHOD_RETURN or not reply.body:
                raise RuntimeError("o BlueZ não respondeu à lista de dispositivos")
            objects = _unwrap_dbus(reply.body[0])
        finally:
            bus.disconnect()

        found: list[BluezDevice] = []
        for path, interfaces in objects.items():
            if not isinstance(interfaces, dict):
                continue
            props = interfaces.get("org.bluez.Device1")
            if not isinstance(props, dict):
                continue
            address = str(props.get("Address") or "")
            if not _BLUETOOTH_ADDRESS.fullmatch(address):
                continue
            name = str(props.get("Name") or "")
            alias = str(props.get("Alias") or name)
            uuids = tuple(str(item) for item in props.get("UUIDs") or [])
            icon = str(props.get("Icon") or "")
            kind = _kind_for_names(name, alias, uuids, icon)
            if kind is None:
                continue
            battery_props = interfaces.get("org.bluez.Battery1")
            battery = None
            if isinstance(battery_props, dict) and battery_props.get("Percentage") is not None:
                try:
                    battery = int(battery_props["Percentage"])
                except (TypeError, ValueError):
                    battery = None
            found.append(
                BluezDevice(
                    path=str(path),
                    address=address,
                    name=alias or name,
                    connected=bool(props.get("Connected")),
                    paired=bool(props.get("Paired")),
                    uuids=uuids,
                    icon=icon,
                    adapter=str(props.get("Adapter") or "/org/bluez/hci0"),
                    kind=kind,
                    battery=battery,
                )
            )
        return found

    async def _load_qcy_devices_bluetoothctl(self) -> list[BluezDevice]:
        try:
            listed = parse_bluetoothctl_device_lines(await self._bluetoothctl("devices"))
        except (FileNotFoundError, OSError, asyncio.TimeoutError) as exc:
            self._emit("log", message=f"Não foi possível consultar o BlueZ via bluetoothctl: {exc}")
            return []
        found: list[BluezDevice] = []
        for address, listed_name in listed:
            try:
                info = parse_bluetoothctl_info(await self._bluetoothctl("info", address))
            except (OSError, asyncio.TimeoutError):
                info = {
                    "name": listed_name,
                    "alias": listed_name,
                    "icon": "",
                    "connected": False,
                    "paired": False,
                    "uuids": [],
                }
            name = str(info["name"] or listed_name)
            alias = str(info["alias"] or name)
            kind = _kind_for_names(name, alias, info["uuids"], str(info["icon"]))
            if kind is None:
                continue
            found.append(
                BluezDevice(
                    path=f"/org/bluez/hci0/dev_{address.replace(':', '_')}",
                    address=address,
                    name=alias,
                    connected=bool(info["connected"]),
                    paired=bool(info["paired"]),
                    uuids=tuple(info["uuids"]),
                    icon=str(info["icon"]),
                    adapter="/org/bluez/hci0",
                    kind=kind,
                )
            )
        return found

    async def _publish_system_qcy(self) -> tuple[list[BluezDevice], list[BluezDevice]]:
        known = await self._load_qcy_devices()
        audio = [item for item in known if item.kind == "audio" and item.connected]
        ble = [item for item in known if item.kind == "ble"]
        ble.sort(key=lambda item: (not item.connected, item.address))
        visible = [item for item in known if item.connected or item.kind == "ble"]
        self._emit("system_devices", devices=[item.row() for item in visible])
        return audio, ble

    @staticmethod
    def _as_ble_device(device: BluezDevice) -> BLEDevice:
        """Monta o handle que o Bleak usa para conectar sem um anúncio novo."""
        return BLEDevice(
            device.address,
            device.name,
            {
                "path": device.path,
                "props": {
                    "Address": device.address,
                    "Alias": device.name,
                    "Adapter": device.adapter,
                },
            },
        )

    async def _connect_known_ble(self, devices: list[BluezDevice]) -> bool:
        for device in devices:
            state = "já conectado no BlueZ" if device.connected else "conhecido pelo BlueZ, sem anúncio novo"
            self._emit("log", message=f"Endpoint de controle {device.name} ({device.address}) {state}.")
            if await self._connect(
                device.address,
                report_failure=False,
                device_name=device.name,
                ble_device=self._as_ble_device(device),
            ):
                return True
        return False

    def _save_address(self, address: str) -> None:
        path = Path.home() / ".config" / "qcyh3control" / "device.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"address": address}, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            self._emit("log", message=f"Não foi possível salvar o endereço BLE para reconexão: {exc}")

    def _load_saved_address(self) -> str | None:
        path = Path.home() / ".config" / "qcyh3control" / "device.json"
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        address = saved.get("address") if isinstance(saved, dict) else None
        return address if isinstance(address, str) and _BLUETOOTH_ADDRESS.fullmatch(address) else None

    async def _connect(
        self,
        address: str,
        report_failure: bool = True,
        device_name: str | None = None,
        ble_device: BLEDevice | None = None,
    ) -> bool:
        await self._disconnect(emit=False)
        self._emit("status", message=f"Conectando a {address}…")
        client: BleakClient | None = None
        try:
            # O BLEDevice com path do BlueZ permite usar o endpoint já conectado,
            # sem exigir um anúncio novo. O MAC A2DP não deve ser passado aqui.
            client = BleakClient(ble_device or address, disconnected_callback=self._on_disconnect)
            self._client = client
            await client.connect(timeout=20.0 if report_failure else 8.0)
            if not client.is_connected:
                raise RuntimeError("O BlueZ não confirmou a conexão.")
            if device_name and is_qcy_h3_name(device_name):
                self._save_address(address)
            self._last_address = address
            self._last_name = device_name
            self._manual_disconnect = False
            self._cancel_reconnect_locked()
            self._emit("connected", address=address)
            self._emit("status", message=f"Conectado via BLE a {address}.")
            self._publish_services()
            await self._initialize_rcsp(client)
            if self._rcsp_ready:
                await self._refresh_state()
            return True
        except Exception as exc:
            if report_failure:
                LOG.exception("Falha ao conectar a %s", address)
            else:
                LOG.info("Tentativa BLE a %s não funcionou: %s", address, exc)
                self._emit("log", message=f"Tentativa de conexão BLE a {address} falhou: {exc}")
            if client is not None and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    LOG.debug("Falha limpando conexão BLE incompleta", exc_info=True)
            self._client = None
            if report_failure:
                self._emit("error", message=f"Não foi possível conectar: {exc}")
            self._emit("disconnected", message="Conexão BLE não estabelecida.")
            return False

    async def _initialize_rcsp(self, client: BleakClient) -> None:
        """Ativa o canal RCSP do QCY H3 e envia o handshake BLE obrigatório."""
        service = next(
            (item for item in client.services if item.uuid.lower() == "0000a002-0000-1000-8000-00805f9b34fb"),
            None,
        )
        if service is None:
            self._emit("protocol_unavailable", message="Serviço RCSP do QCY H3 não encontrado.")
            return

        characteristics = {item.uuid.lower(): item for item in service.characteristics}
        write_characteristic = characteristics.get("00000001-0000-1000-8000-00805f9b34fb")
        notify_characteristic = characteristics.get("00000002-0000-1000-8000-00805f9b34fb")
        if write_characteristic is None or "write-without-response" not in write_characteristic.properties:
            self._emit("protocol_unavailable", message="Característica RCSP de escrita não disponível.")
            return
        if notify_characteristic is None or not (
            {"notify", "indicate"} & set(notify_characteristic.properties)
        ):
            self._emit("protocol_unavailable", message="Característica RCSP de notificação não disponível.")
            return

        self._rcsp_write_characteristic = write_characteristic
        self._rcsp_notify_characteristic = notify_characteristic
        self._rcsp_rx_buffer.clear()
        try:
            await client.start_notify(notify_characteristic, self._handle_rcsp_notification)
            self._rcsp_notify_active = True
            response = await self._send_rcsp(
                OP_NOTIFY_COMMUNICATION_WAY,
                bytes((0x00, 0x00)),
                timeout=3.0,
            )
            if response is None:
                raise TimeoutError("o headphone não respondeu ao handshake RCSP")
            if response.status != 0:
                raise RuntimeError(f"handshake RCSP recusado (status 0x{response.status:02X})")
            self._rcsp_ready = True
            self._emit("protocol_ready", protocol="JieLi RCSP")
            self._emit("log", message="Handshake JieLi RCSP concluído; controles ANC habilitados.")
        except Exception as exc:
            self._rcsp_ready = False
            LOG.exception("Não foi possível inicializar RCSP no QCY H3")
            self._emit("protocol_unavailable", message=f"Handshake RCSP falhou: {exc}")

    def _handle_rcsp_notification(
        self, _characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Reagrupa quadros recebidos por notify e resolve respostas pendentes."""
        self._rcsp_rx_buffer.extend(data)
        self._emit(
            "notification",
            uuid=(self._rcsp_notify_characteristic.uuid if self._rcsp_notify_characteristic else ""),
            value=bytes(data).hex(" ").upper(),
        )
        while True:
            header_index = self._rcsp_rx_buffer.find(RCSP_HEADER)
            if header_index < 0:
                if len(self._rcsp_rx_buffer) > len(RCSP_HEADER) - 1:
                    del self._rcsp_rx_buffer[: -(len(RCSP_HEADER) - 1)]
                return
            if header_index:
                del self._rcsp_rx_buffer[:header_index]
            if len(self._rcsp_rx_buffer) < 7:
                return
            body_length = (self._rcsp_rx_buffer[5] << 8) | self._rcsp_rx_buffer[6]
            frame_length = 7 + body_length + 1
            if len(self._rcsp_rx_buffer) < frame_length:
                return
            frame = bytes(self._rcsp_rx_buffer[:frame_length])
            if frame[-1] != 0xEF:
                del self._rcsp_rx_buffer[0]
                continue
            del self._rcsp_rx_buffer[:frame_length]
            packet = decode_packet(frame)
            if packet is None:
                continue
            if packet.is_response:
                future = self._rcsp_pending.pop(packet.sequence, None)
                if future is not None and not future.done():
                    future.set_result(packet)
                else:
                    self._emit(
                        "log",
                        message=f"Resposta RCSP sem solicitação pendente (SN {packet.sequence}).",
                    )
            else:
                self._emit(
                    "log",
                    message=f"Notificação RCSP opcode 0x{packet.op_code:02X}: {packet.params.hex(' ').upper()}",
                )

    async def _send_rcsp(
        self,
        op_code: int,
        params: bytes,
        *,
        timeout: float = 2.5,
    ) -> RCSPPacket | None:
        """Envia um comando RCSP e aguarda a resposta com o mesmo número de sequência."""
        client = self._client
        characteristic = self._rcsp_write_characteristic
        if client is None or not client.is_connected or characteristic is None:
            raise RuntimeError("Canal RCSP não está conectado.")
        if self._rcsp_lock is None:
            raise RuntimeError("O serializador RCSP não está inicializado.")

        async with self._rcsp_lock:
            self._rcsp_sequence = (self._rcsp_sequence + 1) & 0xFF
            if self._rcsp_sequence == 0:
                self._rcsp_sequence = 1
            sequence = self._rcsp_sequence
            loop = asyncio.get_running_loop()
            response_future: asyncio.Future[RCSPPacket] = loop.create_future()
            self._rcsp_pending[sequence] = response_future
            frame = encode_command(op_code, sequence, params, expect_response=True)
            try:
                await client.write_gatt_char(characteristic, frame, response=False)
                try:
                    return await asyncio.wait_for(response_future, timeout=timeout)
                except asyncio.TimeoutError:
                    return None
            finally:
                self._rcsp_pending.pop(sequence, None)
                if not response_future.done():
                    response_future.cancel()

    def _on_disconnect(self, _client: BleakClient) -> None:
        self._client = None
        self._rcsp_ready = False
        self._rcsp_write_characteristic = None
        self._rcsp_notify_characteristic = None
        self._rcsp_notify_active = False
        for future in self._rcsp_pending.values():
            if not future.done():
                future.cancel()
        self._rcsp_pending.clear()
        was_manual = self._manual_disconnect
        self._emit("disconnected", message="O dispositivo desconectou ou ficou indisponível.")
        if self._closing or was_manual:
            self._emit("status", message="Conexão BLE encerrada.")
            return
        self._emit("status", message="Conexão BLE perdida. Tentando reconectar automaticamente…")
        self._emit("log", message="Desconexão detectada; reconexão automática agendada.")
        self._schedule_reconnect()

    def _cancel_reconnect_locked(self) -> None:
        task = self._reconnect_task
        self._reconnect_task = None
        if task is not None and not task.done():
            task.cancel()

    def _schedule_reconnect(self) -> None:
        if self._closing or not self._auto_reconnect or self._manual_disconnect:
            return
        if self._loop is None or not self._loop.is_running():
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Chamado fora do loop (callback do Bleak): agenda via thread-safe.
            assert self._loop is not None
            self._loop.call_soon_threadsafe(
                lambda: self._submit(self._reconnect_loop())
            )
            return
        self._reconnect_task = loop.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        try:
            for attempt, delay in enumerate(self.RECONNECT_DELAYS, start=1):
                if self._closing or self._manual_disconnect or not self._auto_reconnect:
                    return
                if self._client is not None and self._client.is_connected:
                    return
                await asyncio.sleep(delay)
                if self._closing or self._manual_disconnect or not self._auto_reconnect:
                    return
                if self._client is not None and self._client.is_connected:
                    return
                self._emit(
                    "status",
                    message=f"Tentativa de reconexão {attempt}/{len(self.RECONNECT_DELAYS)}…",
                )
                # Prefere o último endereço BLE; senão redescobre via BlueZ/scan.
                if self._last_address:
                    known = await self._load_qcy_devices()
                    ble_targets = [d for d in known if d.kind == "ble"]
                    same = next(
                        (d for d in ble_targets if d.address.lower() == self._last_address.lower()),
                        None,
                    )
                    targets = ([same] if same else ble_targets) or None
                    if targets and await self._connect_known_ble(targets):
                        return
                    if await self._connect(
                        self._last_address,
                        report_failure=False,
                        device_name=self._last_name,
                    ):
                        return
                else:
                    await self._find_and_connect(manual=False)
                    if self._client is not None and self._client.is_connected:
                        return
            self._emit(
                "status",
                message="Reconexão automática pausada. Clique em Conectar ou Buscar BLE.",
            )
        except asyncio.CancelledError:
            pass
        finally:
            if asyncio.current_task() is self._reconnect_task:
                self._reconnect_task = None

    def _publish_services(self) -> None:
        if self._client is None or self._client.services is None:
            return
        services = []
        for service in self._client.services:
            characteristics = []
            for characteristic in service.characteristics:
                characteristics.append(
                    {
                        "uuid": characteristic.uuid,
                        "handle": characteristic.handle,
                        "properties": list(characteristic.properties),
                    }
                )
            services.append({"uuid": service.uuid, "characteristics": characteristics})
        self._emit("services", services=services)

    def subscribe_notifications(self) -> None:
        self._submit(self._subscribe_notifications())

    async def _subscribe_notifications(self) -> None:
        client = self._client
        if client is None or not client.is_connected:
            self._emit("error", message="Conecte ao dispositivo antes de ativar notificações.")
            return
        candidates: dict[str, BleakGATTCharacteristic] = {}
        for service in client.services:
            for characteristic in service.characteristics:
                if "notify" in characteristic.properties or "indicate" in characteristic.properties:
                    candidates[characteristic.uuid.lower()] = characteristic
        if self.profile.notify_characteristics:
            requested = {uuid.lower() for uuid in self.profile.notify_characteristics}
            candidates = {key: value for key, value in candidates.items() if key in requested}
        if not candidates:
            self._emit(
                "log",
                message="Nenhuma característica notificável selecionada/encontrada. Configure UUIDs no perfil ou inspecione GATT.",
            )
            return

        for characteristic in candidates.values():
            try:
                await client.start_notify(characteristic, self._notification_callback)
                self._emit("log", message=f"Notificações ativadas: {characteristic.uuid}")
            except Exception as exc:
                self._emit("log", message=f"Não foi possível assinar {characteristic.uuid}: {exc}")

    def _notification_callback(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        self._emit(
            "notification",
            uuid=characteristic.uuid,
            value=bytes(data).hex(" ").upper(),
        )

    def read_characteristic(self, characteristic_uuid: str) -> None:
        self._submit(self._read_characteristic(characteristic_uuid))

    async def _read_characteristic(self, characteristic_uuid: str) -> None:
        client = self._client
        if client is None or not client.is_connected:
            self._emit("error", message="Conecte ao dispositivo antes de ler uma característica.")
            return
        try:
            value = await client.read_gatt_char(characteristic_uuid)
            self._emit(
                "log",
                message=f"Leitura {characteristic_uuid}: {bytes(value).hex(' ').upper() or '(vazio)'}",
            )
        except Exception as exc:
            self._emit("error", message=f"Falha ao ler {characteristic_uuid}: {exc}")

    def set_mode(self, mode: str) -> None:
        self._submit(self._set_mode(mode))

    async def _set_mode(self, mode: str) -> None:
        client = self._client
        if client is None or not client.is_connected:
            self._emit("error", message="Conecte ao dispositivo antes de selecionar um modo.")
            return
        if self._rcsp_ready:
            try:
                payload = ANC_MODES.get(mode) or MODE_PAYLOADS.get(mode)
                if payload is None:
                    raise ValueError(f"Modo ANC desconhecido: {mode!r}.")
                response = await self._send_rcsp(OP_CUSTOM, payload)
                if response is None:
                    raise TimeoutError("o headphone não confirmou a mudança de modo")
                if response.status != 0:
                    raise RuntimeError(f"comando recusado (status 0x{response.status:02X})")
                canonical = "anc_high" if mode == "anc" else mode
                self._emit("mode", mode=canonical)
                self._emit("log", message=f"Modo '{canonical}' confirmado pelo RCSP do headphone.")
            except Exception as exc:
                self._emit("error", message=f"Não foi possível alterar o modo: {exc}")
            return
        if not self.profile.enabled or not self.profile.write_characteristic_uuid:
            self._emit("error", message="Controle de modos bloqueado: handshake RCSP não concluído.")
            return
        try:
            payload = self.profile.payload_for(mode)
            target_service = next(
                (
                    service
                    for service in client.services
                    if service.uuid.lower() == self.profile.service_uuid.lower()
                ),
                None,
            )
            if target_service is None:
                raise RuntimeError("O serviço configurado não existe neste dispositivo.")
            target_characteristic = next(
                (
                    characteristic
                    for characteristic in target_service.characteristics
                    if characteristic.uuid.lower()
                    == self.profile.write_characteristic_uuid.lower()
                ),
                None,
            )
            if target_characteristic is None:
                raise RuntimeError("A característica de escrita não pertence ao serviço configurado.")
            required_property = (
                "write"
                if self.profile.write_type == "with_response"
                else "write-without-response"
            )
            if required_property not in target_characteristic.properties:
                raise RuntimeError(
                    f"A característica não anuncia a propriedade GATT '{required_property}'."
                )
            await client.write_gatt_char(
                target_characteristic,
                payload,
                response=self.profile.write_type == "with_response",
            )
            self._emit("mode", mode=mode)
            self._emit("log", message=f"Solicitação '{mode}' enviada ({len(payload)} byte(s)).")
        except Exception as exc:
            LOG.exception("Falha ao enviar modo %s", mode)
            self._emit("error", message=f"Falha ao enviar o modo: {exc}")

    def disconnect(self) -> None:
        self._submit(self._disconnect())

    def set_auto_reconnect(self, enabled: bool) -> None:
        """Liga/desliga a reconexão automática após quedas de BLE."""
        self._auto_reconnect = enabled
        if not enabled:
            self._submit(self._cancel_reconnect())

    async def _cancel_reconnect(self) -> None:
        self._cancel_reconnect_locked()

    def refresh_state(self) -> None:
        """Rele modo ANC, EQ e bateria do fone (após handshake)."""
        self._submit(self._refresh_state())

    async def _refresh_state(self) -> None:
        if not self._rcsp_ready:
            return
        # Modo ANC atual.
        try:
            response = await self._send_rcsp(OP_CUSTOM, GET_ANC_PAYLOAD, timeout=3.0)
            if response is not None and response.status == 0:
                mode = decode_anc_mode(response.params)
                if mode is not None:
                    self._emit("mode", mode=mode)
                    self._emit("log", message=f"Modo atual do fone: {mode}.")
        except Exception as exc:
            LOG.debug("Falha ao ler modo ANC: %s", exc)
        # Equalizador atual.
        try:
            response = await self._send_rcsp(OP_CUSTOM, GET_EQ_PAYLOAD, timeout=3.0)
            if response is not None and response.status == 0:
                offsets = decode_eq_response(response.params)
                if offsets is not None:
                    self._emit("eq", offsets=offsets)
                    self._emit("log", message="Equalizador lido do fone.")
        except Exception as exc:
            LOG.debug("Falha ao ler EQ: %s", exc)
        # Bateria via GET_ADV_INFO (máscara bit 0).
        try:
            mask = (1 << ADV_BATTERY).to_bytes(4, "big")
            response = await self._send_rcsp(OP_GET_ADV_INFO, mask, timeout=3.0)
            if response is not None and response.status == 0:
                battery = decode_battery(response.params)
                if battery is not None:
                    self._emit("battery", percent=battery)
        except Exception as exc:
            LOG.debug("Falha ao ler bateria: %s", exc)

    def set_eq(self, offsets: list[int], preset_id: int = 0x00) -> None:
        self._submit(self._set_eq(list(offsets), preset_id))

    async def _set_eq(self, offsets: list[int], preset_id: int) -> None:
        client = self._client
        if client is None or not client.is_connected:
            self._emit("error", message="Conecte ao dispositivo antes de ajustar o equalizador.")
            return
        if not self._rcsp_ready:
            self._emit("error", message="Equalizador bloqueado: handshake RCSP não concluído.")
            return
        if len(offsets) != len(EQ_BAND_FREQS):
            self._emit("error", message=f"EQ precisa de {len(EQ_BAND_FREQS)} bandas.")
            return
        try:
            payload = encode_eq_command(offsets, preset_id)
            response = await self._send_rcsp(OP_CUSTOM, payload, timeout=3.0)
            if response is None:
                raise TimeoutError("o headphone não confirmou o equalizador")
            if response.status != 0:
                raise RuntimeError(f"EQ recusado (status 0x{response.status:02X})")
            self._emit("eq", offsets=[max(-1200, min(1200, int(v))) for v in offsets])
            self._emit("log", message=f"Equalizador aplicado (preset 0x{preset_id:02X}).")
        except Exception as exc:
            self._emit("error", message=f"Não foi possível aplicar o EQ: {exc}")

    def apply_eq_preset(self, preset_id: int) -> None:
        offsets = EQ_PRESET_OFFSETS.get(preset_id & 0xFF)
        if offsets is None:
            self._emit("error", message=f"Preset de EQ desconhecido: {preset_id}.")
            return
        self.set_eq(list(offsets), preset_id & 0xFF)

    async def _disconnect(self, emit: bool = True) -> None:
        if emit:
            # Desconexão pedida pelo usuário: não tenta reconectar sozinho.
            self._manual_disconnect = True
            self._cancel_reconnect_locked()
        client, self._client = self._client, None
        if client is not None:
            if client.is_connected:
                if self._rcsp_notify_active and self._rcsp_notify_characteristic is not None:
                    try:
                        await client.stop_notify(self._rcsp_notify_characteristic)
                    except Exception as exc:
                        LOG.debug("Não foi possível parar a notificação RCSP: %s", exc)
                try:
                    await client.disconnect()
                except Exception as exc:
                    LOG.warning("Erro ao desconectar: %s", exc)
                    if emit:
                        self._emit("log", message=f"Aviso ao desconectar: {exc}")
        self._rcsp_ready = False
        self._rcsp_write_characteristic = None
        self._rcsp_notify_characteristic = None
        self._rcsp_notify_active = False
        self._rcsp_rx_buffer.clear()
        if emit:
            self._emit("disconnected", message="Desconectado.")
            self._emit("status", message="Desconectado.")

    def stop(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._auto_reconnect = False
        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._disconnect(emit=False), self._loop)
            try:
                future.result(timeout=3)
            except Exception:
                LOG.debug("Desconexão na saída não concluiu", exc_info=True)
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=3)
