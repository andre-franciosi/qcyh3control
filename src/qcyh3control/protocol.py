"""Validação estrita de perfis BLE fornecidos pelo próprio usuário.

Não há UUIDs ou payloads do QCY H3 presumidos neste projeto. Só um perfil
explicitamente habilitado pode autorizar escritas na característica definida.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

MODE_NAMES = ("normal", "anc", "transparency")
_HEX_BYTES = re.compile(r"^(?:[0-9a-fA-F]{2}(?:[\s:-]?[0-9a-fA-F]{2})*)$")


class ProfileError(ValueError):
    """Perfil local ausente, inválido ou incompleto."""


def normalize_uuid(value: Any, field: str) -> str:
    """Valida e normaliza um UUID Bluetooth de 128 bits."""
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{field} precisa ser um UUID Bluetooth completo.")
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError) as exc:
        raise ProfileError(f"UUID inválido em {field}: {value!r}.") from exc


def decode_hex_payload(value: Any, field: str) -> bytes:
    """Converte bytes hexadecimais aceitando espaços, ':' ou '-' como separadores."""
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{field} precisa conter bytes hexadecimais.")
    compact = re.sub(r"[\s:-]", "", value)
    if not compact or len(compact) % 2 or not _HEX_BYTES.fullmatch(value.strip()):
        raise ProfileError(f"Payload hexadecimal inválido em {field}.")
    return bytes.fromhex(compact)


@dataclass(frozen=True)
class VendorProfile:
    """Perfil validado que autoriza comandos definidos manualmente."""

    enabled: bool
    service_uuid: str | None
    write_characteristic_uuid: str | None
    write_type: str
    modes: dict[str, bytes]
    notify_characteristics: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Any) -> "VendorProfile":
        if not isinstance(raw, dict):
            raise ProfileError("A raiz do perfil deve ser um objeto JSON.")

        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ProfileError("'enabled' precisa ser true ou false.")
        write_type = raw.get("write_type", "with_response")
        if write_type not in ("with_response", "without_response"):
            raise ProfileError("'write_type' deve ser with_response ou without_response.")

        # Um perfil desabilitado é seguro mesmo que ainda contenha campos vazios.
        if not enabled:
            return cls(False, None, None, write_type, {}, ())

        service_uuid = normalize_uuid(raw.get("service_uuid"), "service_uuid")
        write_uuid = normalize_uuid(
            raw.get("write_characteristic_uuid"), "write_characteristic_uuid"
        )
        raw_modes = raw.get("modes")
        if not isinstance(raw_modes, dict):
            raise ProfileError("'modes' deve mapear normal, anc e transparency para hex.")
        modes: dict[str, bytes] = {}
        for mode in MODE_NAMES:
            if mode not in raw_modes:
                raise ProfileError(f"Falta o payload do modo '{mode}'.")
            modes[mode] = decode_hex_payload(raw_modes[mode], f"modes.{mode}")

        raw_notify = raw.get("notify_characteristics", [])
        if not isinstance(raw_notify, list):
            raise ProfileError("'notify_characteristics' deve ser uma lista de UUIDs.")
        notify = tuple(
            normalize_uuid(item, f"notify_characteristics[{index}]")
            for index, item in enumerate(raw_notify)
        )
        return cls(True, service_uuid, write_uuid, write_type, modes, notify)

    @classmethod
    def load(cls, path: str | Path) -> "VendorProfile":
        """Carrega e valida um perfil JSON local."""
        file_path = Path(path).expanduser()
        try:
            with file_path.open("r", encoding="utf-8") as profile_file:
                raw = json.load(profile_file)
        except FileNotFoundError as exc:
            raise ProfileError(f"Perfil não encontrado: {file_path}") from exc
        except json.JSONDecodeError as exc:
            raise ProfileError(f"JSON inválido na linha {exc.lineno}: {exc.msg}") from exc
        except OSError as exc:
            raise ProfileError(f"Não foi possível ler o perfil: {exc}") from exc
        return cls.from_mapping(raw)

    def payload_for(self, mode: str) -> bytes:
        """Retorna o comando de um modo somente se o perfil estiver habilitado."""
        if not self.enabled:
            raise ProfileError("Controle bloqueado: configure e valide um perfil BLE primeiro.")
        if mode not in self.modes:
            raise ProfileError(f"Modo desconhecido: {mode}.")
        return self.modes[mode]
