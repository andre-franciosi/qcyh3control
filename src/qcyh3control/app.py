"""Interface Tkinter em português para controle BLE do QCY H3."""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from .ble_manager import BleManager
from .protocol import VendorProfile
from .rcsp import (
    ANC_LABELS,
    ANC_ORDER,
    EQ_BAND_LABELS,
    EQ_MAX_DB100,
    EQ_MIN_DB100,
    EQ_PRESET_IDS,
    EQ_PRESET_NAMES,
    EQ_PRESET_OFFSETS,
)


class QcyControlApp:
    """GUI principal; somente a thread Tk altera widgets."""

    BACKGROUND = "#10151f"
    PANEL = "#192231"
    TEXT = "#e8edf5"
    MUTED = "#9aa8ba"
    ACCENT = "#50d3b2"

    def __init__(self, root: tk.Tk, manager: BleManager, profile: VendorProfile) -> None:
        self.root = root
        self.manager = manager
        self.profile = profile
        self.devices: dict[str, str] = {}
        self.system_addresses: set[str] = set()
        self.characteristic_uuids: dict[str, str] = {}
        self.connected = False
        self.protocol_ready = False
        self.selected_mode = ""
        self.battery_percent: int | None = None
        self.eq_offsets: list[int] = [0] * len(EQ_BAND_LABELS)
        self._updating_eq_ui = False

        self.root.title("QCY H3 Control · BLE")
        self.root.geometry("1120x860")
        self.root.minsize(900, 700)
        self.root.configure(bg=self.BACKGROUND)
        self._configure_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll_events)
        # Conexão automática: se o fone já está no BlueZ (áudio do GNOME),
        # abre o canal BLE sem exigir clique em Conectar.
        self.root.after(300, self.manager.auto_connect)
        # Se o fone conectar ao sistema depois do app aberto, tenta de novo.
        self.root.after(10000, self._auto_connect_tick)
        self._update_controls()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.BACKGROUND)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("TLabel", background=self.BACKGROUND, foreground=self.TEXT, font=("TkDefaultFont", 10))
        style.configure("Title.TLabel", font=("TkDefaultFont", 20, "bold"), foreground=self.TEXT)
        style.configure("Subtitle.TLabel", foreground=self.MUTED, font=("TkDefaultFont", 10))
        style.configure("Panel.TLabel", background=self.PANEL, foreground=self.TEXT)
        style.configure("Muted.Panel.TLabel", background=self.PANEL, foreground=self.MUTED)
        style.configure("Panel.TCheckbutton", background=self.PANEL, foreground=self.TEXT, font=("TkDefaultFont", 9))
        style.configure("Panel.TRadiobutton", background=self.PANEL, foreground=self.TEXT, font=("TkDefaultFont", 10))
        style.configure("TButton", padding=(12, 8), font=("TkDefaultFont", 10, "bold"))
        style.configure("Accent.TButton", background=self.ACCENT, foreground="#102019")
        style.map("Accent.TButton", background=[("disabled", "#47545c"), ("active", "#78e6cb")])
        style.map("TButton", foreground=[("disabled", "#697585")])
        style.configure("Treeview", background="#131b27", fieldbackground="#131b27", foreground=self.TEXT, rowheight=24)
        style.configure("Treeview.Heading", background=self.PANEL, foreground=self.TEXT, font=("TkDefaultFont", 9, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 16))
        title_row = ttk.Frame(header)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="QCY H3 Control", style="Title.TLabel").pack(side="left", anchor="w")
        self.battery_var = tk.StringVar(value="🔋 —")
        ttk.Label(title_row, textvariable=self.battery_var, style="Subtitle.TLabel").pack(side="right", anchor="e")
        ttk.Label(
            header,
            text="Conecta sozinho quando o fone já está no Bluetooth do sistema · A2DP continua no GNOME",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        self.status_var = tk.StringVar(value="Conectando automaticamente ao QCY H3…")
        status = tk.Label(
            outer,
            textvariable=self.status_var,
            bg="#14352f",
            fg="#aaf2df",
            anchor="w",
            padx=12,
            pady=10,
            font=("TkDefaultFont", 10),
        )
        status.pack(fill="x", pady=(0, 14))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, style="Panel.TFrame", padding=14)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ttk.Frame(body, style="Panel.TFrame", padding=14)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        ttk.Label(left, text="Dispositivos BLE", style="Panel.TLabel", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(left, text="O app conecta sozinho ao QCY H3 do sistema. Use manual só se falhar.", style="Muted.Panel.TLabel").pack(anchor="w", pady=(4, 10))
        columns = ("name", "address", "rssi")
        self.device_tree = ttk.Treeview(left, columns=columns, show="headings", height=9, selectmode="browse")
        self.device_tree.heading("name", text="Nome")
        self.device_tree.heading("address", text="Endereço")
        self.device_tree.heading("rssi", text="RSSI")
        self.device_tree.column("name", width=160)
        self.device_tree.column("address", width=145)
        self.device_tree.column("rssi", width=55, anchor="center")
        self.device_tree.pack(fill="both", expand=True)
        device_buttons = ttk.Frame(left, style="Panel.TFrame")
        device_buttons.pack(fill="x", pady=(10, 0))
        self.scan_button = ttk.Button(device_buttons, text="Buscar BLE", style="Accent.TButton", command=self.manager.scan)
        self.scan_button.pack(side="left")
        ttk.Button(device_buttons, text="Cancelar busca", command=self.manager.cancel_scan).pack(side="left", padx=(8, 0))
        self.connect_button = ttk.Button(device_buttons, text="Conectar", command=self._connect_selected)
        self.connect_button.pack(side="left", padx=(8, 0))
        self.disconnect_button = ttk.Button(device_buttons, text="Desconectar", command=self.manager.disconnect)
        self.disconnect_button.pack(side="right")
        reconnect_row = ttk.Frame(left, style="Panel.TFrame")
        reconnect_row.pack(fill="x", pady=(8, 0))
        self.auto_reconnect_var = tk.BooleanVar(value=True)
        self.auto_reconnect_check = ttk.Checkbutton(
            reconnect_row,
            text="Reconectar automaticamente se cair",
            variable=self.auto_reconnect_var,
            command=self._toggle_auto_reconnect,
            style="Panel.TCheckbutton",
        )
        self.auto_reconnect_check.pack(side="left")
        self.refresh_button = ttk.Button(reconnect_row, text="Atualizar estado", command=self.manager.refresh_state)
        self.refresh_button.pack(side="right")

        ttk.Label(right, text="Controle de ruído (ANC)", style="Panel.TLabel", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        profile_message = (
            "Perfil BLE local configurado."
            if self.profile.enabled
            else "Conecte o QCY H3 (automático) e aguarde o handshake JieLi RCSP."
        )
        self.profile_label = ttk.Label(right, text=profile_message, style="Muted.Panel.TLabel", wraplength=380, justify="left")
        self.profile_label.pack(anchor="w", pady=(4, 10))
        self.mode_var = tk.StringVar(value="")
        modes = ttk.Frame(right, style="Panel.TFrame")
        modes.pack(fill="x", pady=(0, 12))
        self.mode_buttons: dict[str, ttk.Radiobutton] = {}
        for key in ANC_ORDER:
            label = ANC_LABELS.get(key, key)
            button = ttk.Radiobutton(
                modes, text=label, value=key, variable=self.mode_var,
                command=lambda mode=key: self.manager.set_mode(mode),
                style="Panel.TRadiobutton",
            )
            button.pack(fill="x", pady=2)
            self.mode_buttons[key] = button

        ttk.Label(right, text="Equalizador · 10 bandas", style="Panel.TLabel", font=("TkDefaultFont", 12, "bold")).pack(anchor="w", pady=(6, 0))
        ttk.Label(right, text="Sliders em dB (−12 a +12). Aplicar envia ao fone via RCSP.", style="Muted.Panel.TLabel").pack(anchor="w", pady=(4, 8))
        preset_row = ttk.Frame(right, style="Panel.TFrame")
        preset_row.pack(fill="x", pady=(0, 8))
        self.preset_var = tk.StringVar(value=EQ_PRESET_NAMES[0])
        self.preset_combo = ttk.Combobox(
            preset_row, textvariable=self.preset_var, values=list(EQ_PRESET_NAMES),
            state="readonly", width=16,
        )
        self.preset_combo.pack(side="left")
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        self.apply_eq_button = ttk.Button(preset_row, text="Aplicar EQ", style="Accent.TButton", command=self._apply_eq)
        self.apply_eq_button.pack(side="left", padx=(8, 0))
        self.reset_eq_button = ttk.Button(preset_row, text="Zerar", command=self._reset_eq)
        self.reset_eq_button.pack(side="left", padx=(8, 0))
        eq_frame = ttk.Frame(right, style="Panel.TFrame")
        eq_frame.pack(fill="x", pady=(0, 8))
        self.eq_scales: list[ttk.Scale] = []
        self.eq_value_labels: list[ttk.Label] = []
        for index, band in enumerate(EQ_BAND_LABELS):
            col = ttk.Frame(eq_frame, style="Panel.TFrame")
            col.pack(side="left", fill="both", expand=True, padx=2)
            value_label = ttk.Label(col, text="0.0 dB", style="Muted.Panel.TLabel", font=("TkDefaultFont", 8))
            value_label.pack(anchor="center")
            self.eq_value_labels.append(value_label)
            scale = ttk.Scale(
                col, from_=12.0, to=-12.0, orient="vertical", length=110,
                command=lambda _v, i=index: self._on_eq_slider(i),
            )
            scale.set(0.0)
            scale.pack(anchor="center", pady=4)
            ttk.Label(col, text=band, style="Muted.Panel.TLabel", font=("TkDefaultFont", 8)).pack(anchor="center")
            self.eq_scales.append(scale)

        ttk.Label(right, text="Inspeção GATT", style="Panel.TLabel", font=("TkDefaultFont", 12, "bold")).pack(anchor="w", pady=(6, 0))
        ttk.Label(right, text="UUIDs e propriedades reportados pelo BlueZ.", style="Muted.Panel.TLabel").pack(anchor="w", pady=(4, 8))
        self.gatt_tree = ttk.Treeview(right, columns=("uuid", "properties"), show="headings", height=5)
        self.gatt_tree.heading("uuid", text="Característica")
        self.gatt_tree.heading("properties", text="Propriedades")
        self.gatt_tree.column("uuid", width=230)
        self.gatt_tree.column("properties", width=110)
        self.gatt_tree.pack(fill="both", expand=True)
        inspect_buttons = ttk.Frame(right, style="Panel.TFrame")
        inspect_buttons.pack(fill="x", pady=(8, 0))
        self.notify_button = ttk.Button(inspect_buttons, text="Ativar notificações", command=self.manager.subscribe_notifications)
        self.notify_button.pack(side="left")
        self.read_button = ttk.Button(inspect_buttons, text="Ler selecionada", command=self._read_selected)
        self.read_button.pack(side="right")

        log_panel = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        log_panel.pack(fill="x", pady=(16, 0))
        ttk.Label(log_panel, text="Atividade", style="Panel.TLabel", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.log_text = tk.Text(
            log_panel,
            height=6,
            wrap="word",
            bg="#131b27",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            state="disabled",
            font=("TkFixedFont", 9),
        )
        self.log_text.pack(fill="x", pady=(6, 0))
        self._append_log("Aplicativo iniciado. Conexão automática ao QCY H3 ativada.")

    def _connect_selected(self) -> None:
        selection = self.device_tree.selection()
        if not selection:
            messagebox.showinfo("Selecione um dispositivo", "Escolha um dispositivo na lista primeiro.")
            return
        address = self.devices.get(selection[0])
        if address:
            values = self.device_tree.item(selection[0], "values")
            device_name = str(values[0]) if values else None
            self.manager.connect(address, device_name=device_name)

    def _read_selected(self) -> None:
        selection = self.gatt_tree.selection()
        if not selection:
            messagebox.showinfo("Selecione uma característica", "Escolha uma característica GATT primeiro.")
            return
        characteristic_uuid = self.characteristic_uuids.get(selection[0])
        if characteristic_uuid:
            self.manager.read_characteristic(characteristic_uuid)

    def _update_controls(self) -> None:
        self.connect_button.configure(state="normal" if not self.connected else "disabled")
        self.disconnect_button.configure(state="normal" if self.connected else "disabled")
        self.notify_button.configure(state="normal" if self.connected else "disabled")
        self.read_button.configure(state="normal" if self.connected else "disabled")
        self.refresh_button.configure(
            state="normal" if self.connected and self.protocol_ready else "disabled"
        )
        mode_state = (
            "normal"
            if self.connected and (self.protocol_ready or self.profile.enabled)
            else "disabled"
        )
        for button in self.mode_buttons.values():
            button.configure(state=mode_state)
        eq_state = "normal" if self.connected and self.protocol_ready else "disabled"
        for scale in self.eq_scales:
            scale.configure(state=eq_state)
        self.apply_eq_button.configure(state=eq_state)
        self.reset_eq_button.configure(state=eq_state)
        self.preset_combo.configure(state="readonly" if eq_state == "normal" else "disabled")

    def _toggle_auto_reconnect(self) -> None:
        enabled = self.auto_reconnect_var.get()
        self.manager.set_auto_reconnect(enabled)
        self._append_log(
            "Reconexão automática ativada." if enabled else "Reconexão automática desativada."
        )

    def _auto_connect_tick(self) -> None:
        # Se o fone apareceu no sistema depois da abertura, conecta sem clique.
        if self.root.winfo_exists():
            if not self.connected:
                self.manager.auto_connect()
            self.root.after(30000, self._auto_connect_tick)

    def _on_eq_slider(self, index: int) -> None:
        if self._updating_eq_ui:
            return
        try:
            db = float(self.eq_scales[index].get())
        except (tk.TclError, ValueError):
            return
        db100 = int(round(db * 100))
        db100 = max(EQ_MIN_DB100, min(EQ_MAX_DB100, db100))
        self.eq_offsets[index] = db100
        self.eq_value_labels[index].configure(text=f"{db100 / 100:+.1f} dB" if db100 else "0.0 dB")
        self.preset_var.set(EQ_PRESET_NAMES[0])  # qualquer ajuste manual vira Custom

    def _set_eq_ui(self, offsets: list[int]) -> None:
        self._updating_eq_ui = True
        try:
            self.eq_offsets = list(offsets)
            for index, scale in enumerate(self.eq_scales):
                db100 = offsets[index] if index < len(offsets) else 0
                scale.set(db100 / 100)
                self.eq_value_labels[index].configure(
                    text=f"{db100 / 100:+.1f} dB" if db100 else "0.0 dB"
                )
        finally:
            self._updating_eq_ui = False

    def _on_preset_selected(self, _event: Any = None) -> None:
        name = self.preset_var.get()
        try:
            preset_id = EQ_PRESET_IDS[EQ_PRESET_NAMES.index(name)]
        except ValueError:
            return
        offsets = EQ_PRESET_OFFSETS.get(preset_id, [0] * len(EQ_BAND_LABELS))
        self._set_eq_ui(list(offsets))
        # Presets nomeados vão direto ao fone; Custom espera o Aplicar EQ.
        if preset_id != 0x00 and self.connected and self.protocol_ready:
            self.manager.set_eq(list(offsets), preset_id)

    def _apply_eq(self) -> None:
        try:
            preset_id = EQ_PRESET_IDS[EQ_PRESET_NAMES.index(self.preset_var.get())]
        except ValueError:
            preset_id = 0x00
        self.manager.set_eq(list(self.eq_offsets), preset_id)
        self._append_log("Equalizador enviado ao fone.")

    def _reset_eq(self) -> None:
        self.preset_var.set(EQ_PRESET_NAMES[0])
        self._set_eq_ui([0] * len(EQ_BAND_LABELS))
        if self.connected and self.protocol_ready:
            self.manager.set_eq([0] * len(EQ_BAND_LABELS), 0x00)

    def _append_log(self, message: str) -> None:
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.manager.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._poll_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == "status":
            self.status_var.set(event["message"])
        elif kind == "error":
            self.status_var.set(event["message"])
            self._append_log(f"ERRO: {event['message']}")
        elif kind == "log":
            self._append_log(event["message"])
        elif kind == "devices":
            scanned = {device["address"] for device in event["devices"]}
            for item_id, address in list(self.devices.items()):
                if address not in self.system_addresses and address not in scanned:
                    self.device_tree.delete(item_id)
                    del self.devices[item_id]
            for device in event["devices"]:
                if device["address"] not in self.system_addresses:
                    self._upsert_device(device)
            if not event["devices"] and not self.system_addresses:
                self._append_log("Nenhum anúncio BLE encontrado. Confirme que o fone está ligado e próximo.")
            elif not event["devices"] and self.system_addresses:
                self._append_log("Nenhum anúncio BLE novo. O QCY H3 conectado no sistema continua na lista.")
        elif kind == "system_devices":
            self.system_addresses = {device["address"] for device in event["devices"]}
            for device in event["devices"]:
                self._upsert_device(device)
            ble = next((device for device in event["devices"] if device.get("kind") == "ble"), None)
            target = ble or (event["devices"][0] if event["devices"] else None)
            if target is not None:
                selected = next(
                    (item_id for item_id, address in self.devices.items() if address == target["address"]),
                    None,
                )
                if selected is not None:
                    self.device_tree.selection_set(selected)
                    self.device_tree.see(selected)
        elif kind == "connected":
            self.connected = True
            self.protocol_ready = False
            address = event["address"]
            existing = next(
                (item_id for item_id, known_address in self.devices.items() if known_address == address),
                None,
            )
            if existing is None:
                existing = f"device-auto-{len(self.devices)}"
                self.devices[existing] = address
                self.device_tree.insert(
                    "", "end", iid=existing,
                    values=("QCY H3 conectado", address, "—"),
                )
            self.device_tree.selection_set(existing)
            self._append_log(f"Conectado via BLE: {address}")
            self._update_controls()
        elif kind == "disconnected":
            self.connected = False
            self.protocol_ready = False
            self.status_var.set(event.get("message", "Desconectado."))
            self._update_controls()
        elif kind == "protocol_ready":
            self.protocol_ready = True
            self.profile_label.configure(
                text="Handshake JieLi RCSP concluído. ANC, EQ e bateria liberados."
            )
            self._update_controls()
        elif kind == "protocol_unavailable":
            self.protocol_ready = False
            self.profile_label.configure(text=event["message"])
            self._update_controls()
        elif kind == "services":
            self.gatt_tree.delete(*self.gatt_tree.get_children())
            self.characteristic_uuids.clear()
            for service_index, service in enumerate(event["services"]):
                self._append_log(f"Serviço GATT: {service['uuid']}")
                for characteristic_index, characteristic in enumerate(service["characteristics"]):
                    uuid = characteristic["uuid"]
                    properties = ", ".join(characteristic["properties"])
                    item_id = f"gatt-{service_index}-{characteristic_index}"
                    self.characteristic_uuids[item_id] = uuid
                    self.gatt_tree.insert("", "end", iid=item_id, values=(uuid, properties))
                    self._append_log(f"  {uuid} [{properties or 'sem propriedades'}]")
        elif kind == "notification":
            self._append_log(f"NOTIFY {event['uuid']}: {event['value'] or '(vazio)'}")
        elif kind == "mode":
            mode = str(event["mode"])
            canonical = "anc_high" if mode == "anc" else mode
            self.selected_mode = canonical
            if canonical in self.mode_buttons:
                self.mode_var.set(canonical)
            label = ANC_LABELS.get(canonical, canonical)
            self._append_log(f"Modo ativo: {label}.")
        elif kind == "eq":
            offsets = [int(v) for v in event["offsets"]]
            self._set_eq_ui(offsets)
            self._append_log("Equalizador sincronizado com o fone.")
        elif kind == "battery":
            percent = int(event["percent"])
            self.battery_percent = percent
            self.battery_var.set(f"🔋 {percent}%")
            self._append_log(f"Bateria do fone: {percent}%.")

    def _close(self) -> None:
        self.manager.stop()
        self.root.destroy()

    def _upsert_device(self, device: dict[str, Any]) -> None:
        address = device["address"]
        values = (device["name"], address, device.get("rssi", "—"))
        existing = next((item_id for item_id, known in self.devices.items() if known == address), None)
        if existing is not None:
            self.device_tree.item(existing, values=values)
            return
        item_id = f"device-{address.replace(':', '-')}"
        self.devices[item_id] = address
        self.device_tree.insert("", 0 if address in self.system_addresses else "end", iid=item_id, values=values)
