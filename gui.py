"""
Discord Bot Control Panel - V1
Focused on sending messages/embeds and creating reaction role messages.
"""

import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
import requests
from tkinter import messagebox
import storage as storage_layer

BLURPLE = "#5865F2"
BLURPLE_H = "#4752C4"
GREEN = "#57F287"
RED = "#ED4245"
YELLOW = "#FEE75C"
BG_DARK = "#1E1F22"
BG_MID = "#2B2D31"
BG_CARD = "#313338"
TEXT_1 = "#FFFFFF"
TEXT_2 = "#B5BAC1"
TEXT_3 = "#80848E"
BORDER = "#3F4147"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

CONFIG_FILE = "config.json"
ENV_FILE = ".env"
storage_layer.init_db()

DEFAULT_CONFIG = {
    "reaction_roles": {},
    "messages": {}
}

COLOR_MAP = {
    "Blurple": 0x5865F2,
    "Green": 0x57F287,
    "Red": 0xED4245,
    "Yellow": 0xFEE75C,
    "White": 0xFFFFFF,
}

ROLE_MODE_DROPDOWN = "Multi Select"
ROLE_MODE_BUTTON = "One-Time Button"
ROLE_MODE_REACTION = "Reaction"
DEFAULT_RR_DESCRIPTION = "使用下拉式選單來更改名字顏色"
MENTION_HELP = "Mention: <@user_id> = member, <@&role_id> = role, <#channel_id> = channel, <:emoji:id> = server emoji"

COMMON_EMOJI_CHOICES = [
    ("🎮 Gaming", "🎮"),
    ("✅ Check", "✅"),
    ("⭐ Star", "⭐"),
    ("🔥 Fire", "🔥"),
    ("💬 Chat", "💬"),
    ("🎨 Color", "🎨"),
    ("❤️ Red Heart", "❤️"),
    ("🧡 Orange Heart", "🧡"),
    ("💛 Yellow Heart", "💛"),
    ("💚 Green Heart", "💚"),
    ("💙 Blue Heart", "💙"),
    ("💜 Purple Heart", "💜"),
    ("🖤 Black Heart", "🖤"),
    ("🤍 White Heart", "🤍"),
    ("🔴 Red Circle", "🔴"),
    ("🟠 Orange Circle", "🟠"),
    ("🟡 Yellow Circle", "🟡"),
    ("🟢 Green Circle", "🟢"),
    ("🔵 Blue Circle", "🔵"),
    ("🟣 Purple Circle", "🟣"),
    ("⚪ White Circle", "⚪"),
    ("🟥 Red Square", "🟥"),
    ("🟧 Orange Square", "🟧"),
    ("🟨 Yellow Square", "🟨"),
    ("🟩 Green Square", "🟩"),
    ("🟦 Blue Square", "🟦"),
    ("🟪 Purple Square", "🟪"),
    ("⬜ White Square", "⬜"),
]


def load_config():
    return storage_layer.load_config()


def save_config(data):
    storage_layer.save_config(data)


def read_env_token():
    path = Path(ENV_FILE)
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if raw.startswith("DISCORD_TOKEN="):
            return raw.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def write_env_token(token):
    path = Path(ENV_FILE)
    lines = []
    found = False
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    next_lines = []
    for line in lines:
        if line.strip().startswith("DISCORD_TOKEN="):
            next_lines.append(f"DISCORD_TOKEN={token}")
            found = True
        else:
            next_lines.append(line)

    if not found:
        next_lines.append(f"DISCORD_TOKEN={token}")

    path.write_text("\n".join(next_lines).strip() + "\n", encoding="utf-8")


class DiscordAPI:
    BASE = "https://discord.com/api/v10"

    def __init__(self, token):
        self.headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }

    def _handle(self, response):
        if response.status_code >= 400:
            try:
                detail = response.json().get("message", response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(f"{response.status_code}: {detail}")
        if response.text:
            return response.json()
        return None

    def get(self, path):
        return self._handle(requests.get(f"{self.BASE}{path}", headers=self.headers, timeout=10))

    def post(self, path, payload):
        return self._handle(requests.post(f"{self.BASE}{path}", headers=self.headers, json=payload, timeout=10))

    def patch(self, path, payload):
        return self._handle(requests.patch(f"{self.BASE}{path}", headers=self.headers, json=payload, timeout=10))

    def put(self, path):
        return self._handle(requests.put(f"{self.BASE}{path}", headers=self.headers, timeout=10))

    def delete(self, path):
        return self._handle(requests.delete(f"{self.BASE}{path}", headers=self.headers, timeout=10))

    def get_me(self):
        return self.get("/users/@me")

    def get_guilds(self):
        return self.get("/users/@me/guilds")

    def get_channel(self, channel_id):
        return self.get(f"/channels/{channel_id}")

    def get_channels(self, guild_id):
        channels = self.get(f"/guilds/{guild_id}/channels")
        return [channel for channel in channels if channel.get("type") in (0, 5)]

    def get_roles(self, guild_id):
        roles = self.get(f"/guilds/{guild_id}/roles")
        return [role for role in roles if role.get("name") != "@everyone" and not role.get("managed")]

    def get_emojis(self, guild_id):
        return self.get(f"/guilds/{guild_id}/emojis")

    def send_message(self, channel_id, content=None, embed=None):
        payload = {}
        if content:
            payload["content"] = content
        if embed:
            payload["embeds"] = [embed]
        payload["allowed_mentions"] = {"parse": ["users", "roles"]}
        return self.post(f"/channels/{channel_id}/messages", payload)

    def edit_message(self, channel_id, message_id, payload):
        return self.patch(f"/channels/{channel_id}/messages/{message_id}", payload)

    def delete_message(self, channel_id, message_id):
        return self.delete(f"/channels/{channel_id}/messages/{message_id}")

    def add_reaction(self, channel_id, message_id, emoji):
        route_emoji = reaction_route_emoji(emoji)
        encoded = urllib.parse.quote(route_emoji, safe="")
        try:
            self.put(f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me")
        except RuntimeError:
            if ":" not in route_emoji:
                raise
            encoded = urllib.parse.quote(route_emoji, safe=":")
            self.put(f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me")


def card(parent, **kwargs):
    return ctk.CTkFrame(
        parent,
        fg_color=BG_CARD,
        corner_radius=8,
        border_width=1,
        border_color=BORDER,
        **kwargs,
    )


def label(parent, text, size=13, weight="normal", color=TEXT_1, **kwargs):
    return ctk.CTkLabel(
        parent,
        text=text,
        font=ctk.CTkFont(size=size, weight=weight),
        text_color=color,
        **kwargs,
    )


def entry(parent, placeholder="", width=320, **kwargs):
    return ctk.CTkEntry(
        parent,
        placeholder_text=placeholder,
        width=width,
        fg_color=BG_MID,
        border_color=BORDER,
        text_color=TEXT_1,
        placeholder_text_color=TEXT_3,
        **kwargs,
    )


def btn(parent, text, command, color=BLURPLE, hover=BLURPLE_H, width=140, **kwargs):
    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        fg_color=color,
        hover_color=hover,
        corner_radius=8,
        width=width,
        font=ctk.CTkFont(size=13, weight="bold"),
        **kwargs,
    )


def section_title(parent, text):
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.pack(fill="x", pady=(0, 12))
    label(frame, text, size=11, color=TEXT_3, weight="bold").pack(side="left")
    ctk.CTkFrame(frame, height=1, fg_color=BORDER).pack(
        side="left", fill="x", expand=True, padx=(10, 0), pady=(0, 1)
    )
    return frame


def parse_custom_emoji(value):
    raw = value.strip()
    if raw.startswith("<:") and raw.endswith(">"):
        name, emoji_id = raw[2:-1].split(":", 1)
        return {"name": name, "id": emoji_id, "animated": False}
    if raw.startswith("<a:") and raw.endswith(">"):
        name, emoji_id = raw[3:-1].split(":", 1)
        return {"name": name, "id": emoji_id, "animated": True}
    return None


def reaction_route_emoji(value):
    parsed = parse_custom_emoji(value)
    if parsed:
        return f"{parsed['name']}:{parsed['id']}"
    return value


def component_emoji(value):
    parsed = parse_custom_emoji(value)
    if parsed:
        payload = {"name": parsed["name"], "id": parsed["id"]}
        if parsed["animated"]:
            payload["animated"] = True
        return payload
    if any(ord(ch) > 127 for ch in value):
        return {"name": value}
    return None


def custom_emoji_value(emoji):
    prefix = "a" if emoji.get("animated") else ""
    return f"<{prefix}:{emoji['name']}:{emoji['id']}>"


def emoji_name_from_text(value):
    raw = value.strip()
    if raw.startswith(":") and raw.endswith(":") and len(raw) > 2:
        return raw[1:-1].lower()
    if re.fullmatch(r"[A-Za-z0-9_]{2,32}", raw):
        return raw.lower()
    return ""


def resolve_emoji_value(api, guild_id, value):
    raw = value.strip()
    if not raw:
        return raw
    if parse_custom_emoji(raw) or not emoji_name_from_text(raw):
        return raw

    target_name = emoji_name_from_text(raw)
    for emoji in api.get_emojis(guild_id):
        if emoji.get("name", "").lower() == target_name:
            return custom_emoji_value(emoji)
    return raw


def build_role_select_components(message_id, mappings):
    options = []
    for item in mappings[:25]:
        option = {
            "label": item["role_name"][:100],
            "value": str(item["role_id"]),
            "description": f"Toggle {item['role_name']}"[:100],
        }
        emoji = item.get("emoji", "").strip()
        if emoji:
            emoji_payload = component_emoji(emoji)
            if emoji_payload:
                option["emoji"] = emoji_payload
        options.append(option)

    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 3,
                    "custom_id": f"role_select:{message_id}",
                    "placeholder": "Select your roles",
                    "min_values": 0,
                    "max_values": min(25, max(1, len(options))),
                    "options": options,
                }
            ],
        }
    ]


def build_role_button_components(message_id, mappings):
    if not mappings:
        return []
    item = mappings[0]
    button_payload = {
        "type": 2,
        "style": 3,
        "label": item["role_name"][:80],
        "custom_id": f"role_button:{message_id}:{item['role_id']}",
    }
    emoji = item.get("emoji", "").strip()
    if emoji:
        emoji_payload = component_emoji(emoji)
        if emoji_payload:
            button_payload["emoji"] = emoji_payload
    return [{"type": 1, "components": [button_payload]}]


def role_mode_value(label_value):
    if label_value == ROLE_MODE_REACTION:
        return "reaction"
    if label_value == ROLE_MODE_BUTTON:
        return "button"
    return "dropdown"


def description_note_only(value):
    lines = []
    for line in str(value or "").splitlines():
        raw = line.strip()
        if raw.startswith("<@&") and raw.endswith(">"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def channel_label(guild, channel):
    return f"{guild['name']} / #{channel['name']} ({channel['id']})"


def fetch_channel_choices(token):
    api = DiscordAPI(token)
    choices = []
    for guild in sorted(api.get_guilds(), key=lambda item: item.get("name", "").lower()):
        channels = api.get_channels(guild["id"])
        channels.sort(key=lambda item: (item.get("position", 0), item.get("name", "")))
        for channel in channels:
            choices.append((channel_label(guild, channel), channel["id"], guild["id"]))
    return choices


def fetch_role_choices(token, guild_id):
    api = DiscordAPI(token)
    roles = api.get_roles(guild_id)
    roles.sort(key=lambda item: item.get("position", 0), reverse=True)
    choices = []
    for role in roles:
        color = int(role.get("color", 0))
        hex_color = f"#{color:06X}" if color else "#000000"
        label_text = f"{role['name']} ({role['id']}) {hex_color}"
        choices.append((label_text, role["id"], role["name"], hex_color))
    return choices


def fetch_emoji_choices(token, guild_id):
    api = DiscordAPI(token)
    choices = list(COMMON_EMOJI_CHOICES)
    custom_count = 0
    for emoji in sorted(api.get_emojis(guild_id), key=lambda item: item.get("name", "").lower()):
        value = custom_emoji_value(emoji)
        choices.append((f":{emoji['name']}: ({emoji['id']})", value))
        custom_count += 1
    return choices, custom_count


class OverviewPage(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._build()

    def _build(self):
        label(self, "Control Panel", size=22, weight="bold").pack(anchor="w", pady=(0, 4))
        label(self, "Manage messages and reaction roles from one place.", size=13, color=TEXT_2).pack(
            anchor="w", pady=(0, 24)
        )

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(0, 20))
        stats = [
            ("Token Source", ".env", GREEN if read_env_token() else YELLOW),
            ("Features Active", "2", BLURPLE),
            ("Storage", "JSON", GREEN),
        ]
        for title, value, color in stats:
            tile = card(row)
            tile.pack(side="left", expand=True, fill="x", padx=(0, 12))
            label(tile, title, size=11, color=TEXT_3).pack(anchor="w", padx=16, pady=(14, 2))
            label(tile, value, size=20, weight="bold", color=color).pack(anchor="w", padx=16, pady=(0, 14))

        section_title(self, "QUICK ACTIONS")
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x")
        btn(actions, "Send Message", lambda: self.app.switch_tab("announce"), width=160).pack(side="left", padx=(0, 10))
        btn(actions, "Reaction Roles", lambda: self.app.switch_tab("reaction"), width=160).pack(side="left", padx=(0, 10))

        section_title(self, "LOG")
        self.log_box = ctk.CTkTextbox(
            self,
            height=180,
            fg_color=BG_MID,
            text_color=TEXT_2,
            font=ctk.CTkFont(family="Courier", size=12),
            corner_radius=8,
        )
        self.log_box.configure(state="disabled")
        self.log_box.pack(fill="both", expand=True)
        self.log("[PANEL] Ready. Token is read from .env only.")

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


class AnnouncePage(ctk.CTkScrollableFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._build()

    def _build(self):
        label(self, "Send Message", size=22, weight="bold").pack(anchor="w", pady=(0, 4))
        label(self, "Send a plain message or embed to any text channel.", size=13, color=TEXT_2).pack(
            anchor="w", pady=(0, 24)
        )

        panel = card(self)
        panel.pack(fill="x")
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="both", padx=20, pady=20)

        section_title(inner, "DESTINATION")
        channel_row = ctk.CTkFrame(inner, fg_color="transparent")
        channel_row.pack(fill="x", pady=(0, 12))
        self.channel_var = ctk.StringVar(value="Manual Channel ID")
        self.channel_options = {}
        self.channel_menu = ctk.CTkOptionMenu(
            channel_row,
            values=["Manual Channel ID"],
            variable=self.channel_var,
            command=self._select_channel,
            fg_color=BG_MID,
            button_color=BLURPLE,
            button_hover_color=BLURPLE_H,
            width=360,
        )
        self.channel_menu.pack(side="left", padx=(0, 10))
        btn(channel_row, "Load Channels", self._load_channels, color=BG_MID, hover=BORDER, width=140).pack(side="left")

        label(inner, "Channel ID", size=12, color=TEXT_2).pack(anchor="w")
        self.channel_entry = entry(inner, "1234567890123456789", width=420)
        self.channel_entry.pack(anchor="w", pady=(4, 14))

        self.use_embed = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            inner,
            text="Send as embed",
            variable=self.use_embed,
            fg_color=BLURPLE,
            hover_color=BLURPLE_H,
            command=self._toggle_embed,
        ).pack(anchor="w", pady=(0, 14))

        section_title(inner, "CONTENT")

        # Embed-only fields — hidden when plain text is selected
        self.embed_options_frame = ctk.CTkFrame(inner, fg_color="transparent")
        self.embed_options_frame.pack(anchor="w", fill="x")

        label(self.embed_options_frame, "Embed Title", size=12, color=TEXT_2).pack(anchor="w")
        self.title_entry = entry(self.embed_options_frame, "Announcement title", width=420)
        self.title_entry.pack(anchor="w", pady=(4, 12))

        embed_row = ctk.CTkFrame(self.embed_options_frame, fg_color="transparent")
        embed_row.pack(anchor="w", pady=(0, 12))
        color_col = ctk.CTkFrame(embed_row, fg_color="transparent")
        color_col.pack(side="left", padx=(0, 12))
        label(color_col, "Embed Color", size=12, color=TEXT_2).pack(anchor="w")
        self.color_var = ctk.StringVar(value="Blurple")
        ctk.CTkOptionMenu(
            color_col,
            values=list(COLOR_MAP.keys()),
            variable=self.color_var,
            fg_color=BG_MID,
            button_color=BLURPLE,
            button_hover_color=BLURPLE_H,
            width=160,
        ).pack(anchor="w", pady=(4, 0))

        footer_col = ctk.CTkFrame(embed_row, fg_color="transparent")
        footer_col.pack(side="left")
        label(footer_col, "Footer", size=12, color=TEXT_2).pack(anchor="w")
        self.footer_entry = entry(footer_col, "Optional footer", width=240)
        self.footer_entry.pack(anchor="w", pady=(4, 0))

        # Always-visible message field
        self.msg_label = label(inner, "Message", size=12, color=TEXT_2)
        self.msg_label.pack(anchor="w")
        self.msg_box = ctk.CTkTextbox(inner, height=120, fg_color=BG_MID, text_color=TEXT_1, corner_radius=8, width=420)
        self.msg_box.pack(anchor="w", pady=(4, 12))
        label(inner, MENTION_HELP, size=11, color=TEXT_3, wraplength=520, justify="left").pack(anchor="w", pady=(0, 12))

        btn(inner, "Send Message", self._send, width=160).pack(anchor="w")

    def _select_channel(self, choice):
        data = self.channel_options.get(choice)
        if not data:
            return
        channel_id = data[0]
        self.channel_entry.delete(0, "end")
        self.channel_entry.insert(0, channel_id)

    def _load_channels(self):
        token = read_env_token()
        if not token:
            messagebox.showerror("No Token", "Set DISCORD_TOKEN in Settings first.")
            return
        self.channel_menu.configure(values=["Loading channels..."])
        self.channel_var.set("Loading channels...")

        def do_load():
            try:
                choices = fetch_channel_choices(token)
                if not choices:
                    raise RuntimeError("No text channels found for this bot.")

                def apply_choices():
                    self.channel_options = {label_text: (channel_id, guild_id) for label_text, channel_id, guild_id in choices}
                    labels = list(self.channel_options.keys())
                    self.channel_menu.configure(values=labels)
                    self.channel_var.set(labels[0])
                    self._select_channel(labels[0])
                    self.app.overview.log(f"[CHANNELS] Loaded {len(labels)} channels")

                self.after(0, apply_choices)
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda: self.channel_menu.configure(values=["Manual Channel ID"]))
                self.after(0, lambda: self.channel_var.set("Manual Channel ID"))
                self.after(0, lambda error=error: messagebox.showerror("Load Failed", error))

        threading.Thread(target=do_load, daemon=True).start()

    def _toggle_embed(self):
        if self.use_embed.get():
            self.embed_options_frame.pack(anchor="w", fill="x", before=self.msg_label)
        else:
            self.embed_options_frame.pack_forget()

    def _send(self):
        token = read_env_token()
        if not token:
            messagebox.showerror("No Token", "Set DISCORD_TOKEN in Settings first.")
            return

        channel_id = self.channel_entry.get().strip()
        if not channel_id.isdigit():
            messagebox.showerror("Invalid Channel", "Channel ID must be numeric.")
            return

        content = self.msg_box.get("1.0", "end").strip()
        if not content:
            messagebox.showerror("Missing Message", "Message cannot be empty.")
            return

        def do_send():
            try:
                api = DiscordAPI(token)
                channel = api.get_channel(channel_id)
                guild_id = channel.get("guild_id", "dm")
                if self.use_embed.get():
                    embed = {
                        "title": self.title_entry.get().strip() or "Announcement",
                        "description": content,
                        "color": COLOR_MAP.get(self.color_var.get(), COLOR_MAP["Blurple"]),
                    }
                    footer = self.footer_entry.get().strip()
                    if footer:
                        embed["footer"] = {"text": footer}
                    result = api.send_message(channel_id, embed=embed)
                    record = {
                        "channel_id": str(channel_id),
                        "type": "embed",
                        "title": embed.get("title", ""),
                        "content": content,
                        "color": self.color_var.get(),
                        "footer": footer,
                    }
                else:
                    result = api.send_message(channel_id, content=content)
                    record = {
                        "channel_id": str(channel_id),
                        "type": "plain",
                        "title": "",
                        "content": content,
                        "color": "",
                        "footer": "",
                    }
                cfg = load_config()
                guild_messages = cfg.setdefault("messages", {}).setdefault(str(guild_id), {})
                guild_messages[str(result["id"])] = record
                save_config(cfg)
                if "saved" in self.app.pages:
                    self.after(0, self.app.pages["saved"].refresh)
                self.after(0, lambda: messagebox.showinfo("Sent", "Message sent successfully."))
                self.after(0, lambda: self.app.overview.log(f"[MESSAGE] Sent to channel {channel_id}"))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: messagebox.showerror("Send Failed", error))

        threading.Thread(target=do_send, daemon=True).start()


class ReactionRolePage(ctk.CTkScrollableFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.rr_data = []
        self.selected_guild_id = None
        self._build()

    def _build(self):
        label(self, "Reaction Roles", size=22, weight="bold").pack(anchor="w", pady=(0, 4))
        label(self, "Post a role picker message and save emoji-role mappings.", size=13, color=TEXT_2).pack(
            anchor="w", pady=(0, 24)
        )

        c1 = card(self)
        c1.pack(fill="x", pady=(0, 14))
        s1 = ctk.CTkFrame(c1, fg_color="transparent")
        s1.pack(fill="both", padx=20, pady=20)
        section_title(s1, "MESSAGE")

        channel_row = ctk.CTkFrame(s1, fg_color="transparent")
        channel_row.pack(fill="x", pady=(0, 12))
        self.channel_var = ctk.StringVar(value="Manual Channel ID")
        self.channel_options = {}
        self.channel_menu = ctk.CTkOptionMenu(
            channel_row,
            values=["Manual Channel ID"],
            variable=self.channel_var,
            command=self._select_channel,
            fg_color=BG_MID,
            button_color=BLURPLE,
            button_hover_color=BLURPLE_H,
            width=360,
        )
        self.channel_menu.pack(side="left", padx=(0, 10))
        btn(channel_row, "Load Channels", self._load_channels, color=BG_MID, hover=BORDER, width=140).pack(side="left")

        label(s1, "Channel ID", size=12, color=TEXT_2).pack(anchor="w")
        self.ch_entry = entry(s1, "Channel ID to post the reaction role message", width=430)
        self.ch_entry.pack(anchor="w", pady=(4, 12))

        mode_row = ctk.CTkFrame(s1, fg_color="transparent")
        mode_row.pack(anchor="w", pady=(0, 12))
        label(mode_row, "Pick Role Mode", size=12, color=TEXT_2).pack(side="left", padx=(0, 10))
        self.mode_var = ctk.StringVar(value=ROLE_MODE_DROPDOWN)
        ctk.CTkSegmentedButton(
            mode_row,
            values=[ROLE_MODE_DROPDOWN, ROLE_MODE_BUTTON, ROLE_MODE_REACTION],
            variable=self.mode_var,
            selected_color=BLURPLE,
            selected_hover_color=BLURPLE_H,
            unselected_color=BG_MID,
            unselected_hover_color=BORDER,
        ).pack(side="left")

        label(s1, "Panel Name", size=12, color=TEXT_2).pack(anchor="w")
        self.panel_name = entry(s1, "Saved name only, optional", width=430)
        self.panel_name.pack(anchor="w", pady=(4, 12))

        label(s1, "Discord Title", size=12, color=TEXT_2).pack(anchor="w")
        self.msg_title = entry(s1, "Optional. Leave empty for no embed title.", width=430)
        self.msg_title.pack(anchor="w", pady=(4, 12))

        label(s1, "Description", size=12, color=TEXT_2).pack(anchor="w")
        self.msg_desc = ctk.CTkTextbox(s1, height=80, fg_color=BG_MID, text_color=TEXT_1, corner_radius=8, width=430)
        self.msg_desc.pack(anchor="w", pady=(4, 0))
        self.msg_desc.insert("1.0", DEFAULT_RR_DESCRIPTION)
        label(s1, MENTION_HELP, size=11, color=TEXT_3, wraplength=520, justify="left").pack(anchor="w", pady=(6, 0))

        style_row = ctk.CTkFrame(s1, fg_color="transparent")
        style_row.pack(anchor="w", pady=(12, 0))
        self.rr_embed_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            style_row,
            text="Send as embed",
            variable=self.rr_embed_var,
            fg_color=BLURPLE,
            hover_color=BLURPLE_H,
        ).pack(side="left", padx=(0, 12))
        label(style_row, "Embed Color", size=12, color=TEXT_2).pack(side="left", padx=(0, 8))
        self.rr_color_var = ctk.StringVar(value="Blurple")
        ctk.CTkOptionMenu(
            style_row,
            values=list(COLOR_MAP.keys()),
            variable=self.rr_color_var,
            fg_color=BG_MID,
            button_color=BLURPLE,
            button_hover_color=BLURPLE_H,
            width=140,
        ).pack(side="left")

        c2 = card(self)
        c2.pack(fill="x", pady=(0, 14))
        s2 = ctk.CTkFrame(c2, fg_color="transparent")
        s2.pack(fill="both", padx=20, pady=20)
        section_title(s2, "EMOJI TO ROLE")

        picker_row = ctk.CTkFrame(s2, fg_color="transparent")
        picker_row.pack(fill="x", pady=(0, 10))
        self.role_options = {}
        self.role_var = ctk.StringVar(value="Load roles from selected server")
        self.role_menu = ctk.CTkOptionMenu(
            picker_row,
            values=["Load roles from selected server"],
            variable=self.role_var,
            command=self._select_role,
            fg_color=BG_MID,
            button_color=BLURPLE,
            button_hover_color=BLURPLE_H,
            width=310,
        )
        self.role_menu.pack(side="left", padx=(0, 10))
        btn(picker_row, "Load Roles", self._load_roles, color=BG_MID, hover=BORDER, width=120).pack(side="left", padx=(0, 10))

        self.emoji_options = {label_text: emoji for label_text, emoji in COMMON_EMOJI_CHOICES}
        self.emoji_var = ctk.StringVar(value=COMMON_EMOJI_CHOICES[0][0])
        self.emoji_menu = ctk.CTkOptionMenu(
            picker_row,
            values=list(self.emoji_options.keys()),
            variable=self.emoji_var,
            command=self._select_emoji,
            fg_color=BG_MID,
            button_color=BLURPLE,
            button_hover_color=BLURPLE_H,
            width=180,
        )
        self.emoji_menu.pack(side="left", padx=(0, 10))
        btn(picker_row, "Load Emojis", self._load_emojis, color=BG_MID, hover=BORDER, width=120).pack(side="left")

        row = ctk.CTkFrame(s2, fg_color="transparent")
        row.pack(fill="x", pady=(0, 10))
        self.emoji_e = entry(row, "Emoji", width=120)
        self.emoji_e.pack(side="left", padx=(0, 10))
        self.role_id_e = entry(row, "Role ID", width=200)
        self.role_id_e.pack(side="left", padx=(0, 10))
        self.role_name_e = entry(row, "Role name", width=160)
        self.role_name_e.pack(side="left", padx=(0, 10))
        btn(row, "+ Add", self._add_mapping, width=80).pack(side="left")
        self._select_emoji(COMMON_EMOJI_CHOICES[0][0])

        self.list_frame = ctk.CTkScrollableFrame(s2, height=120, fg_color=BG_MID, corner_radius=8)
        self.list_frame.pack(fill="x")
        self._refresh_list()

        c3 = card(self)
        c3.pack(fill="x")
        s3 = ctk.CTkFrame(c3, fg_color="transparent")
        s3.pack(fill="both", padx=20, pady=20)
        section_title(s3, "POST")
        btn(s3, "Post Reaction Role Message", self._post, width=240).pack(anchor="w")

    def _select_channel(self, choice):
        data = self.channel_options.get(choice)
        if not data:
            return
        channel_id, guild_id = data
        self.selected_guild_id = guild_id
        self.ch_entry.delete(0, "end")
        self.ch_entry.insert(0, channel_id)

    def _load_channels(self):
        token = read_env_token()
        if not token:
            messagebox.showerror("No Token", "Set DISCORD_TOKEN in Settings first.")
            return
        self.channel_menu.configure(values=["Loading channels..."])
        self.channel_var.set("Loading channels...")

        def do_load():
            try:
                choices = fetch_channel_choices(token)
                if not choices:
                    raise RuntimeError("No text channels found for this bot.")

                def apply_choices():
                    self.channel_options = {label_text: (channel_id, guild_id) for label_text, channel_id, guild_id in choices}
                    labels = list(self.channel_options.keys())
                    self.channel_menu.configure(values=labels)
                    self.channel_var.set(labels[0])
                    self._select_channel(labels[0])
                    self.app.overview.log(f"[CHANNELS] Loaded {len(labels)} channels")

                self.after(0, apply_choices)
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda: self.channel_menu.configure(values=["Manual Channel ID"]))
                self.after(0, lambda: self.channel_var.set("Manual Channel ID"))
                self.after(0, lambda error=error: messagebox.showerror("Load Failed", error))

        threading.Thread(target=do_load, daemon=True).start()

    def _guild_id_for_role_tools(self, api):
        if self.selected_guild_id:
            return self.selected_guild_id
        channel_id = self.ch_entry.get().strip()
        if not channel_id.isdigit():
            raise RuntimeError("Select or enter a channel first.")
        channel = api.get_channel(channel_id)
        guild_id = channel.get("guild_id")
        if not guild_id:
            raise RuntimeError("That channel is not inside a server.")
        self.selected_guild_id = guild_id
        return guild_id

    def _select_role(self, choice):
        data = self.role_options.get(choice)
        if not data:
            return
        role_id, role_name = data
        self.role_id_e.delete(0, "end")
        self.role_id_e.insert(0, role_id)
        self.role_name_e.delete(0, "end")
        self.role_name_e.insert(0, role_name)

    def _select_emoji(self, choice):
        emoji = self.emoji_options.get(choice)
        if not emoji:
            return
        self.emoji_e.delete(0, "end")
        self.emoji_e.insert(0, emoji)

    def _load_roles(self):
        token = read_env_token()
        if not token:
            messagebox.showerror("No Token", "Set DISCORD_TOKEN in Settings first.")
            return
        self.role_menu.configure(values=["Loading roles..."])
        self.role_var.set("Loading roles...")

        def do_load():
            try:
                api = DiscordAPI(token)
                guild_id = self._guild_id_for_role_tools(api)
                choices = fetch_role_choices(token, guild_id)
                if not choices:
                    raise RuntimeError("No assignable roles found.")

                def apply_choices():
                    self.role_options = {label_text: (role_id, role_name) for label_text, role_id, role_name, _ in choices}
                    labels = list(self.role_options.keys())
                    self.role_menu.configure(values=labels)
                    self.role_var.set(labels[0])
                    self._select_role(labels[0])
                    self.app.overview.log(f"[ROLES] Loaded {len(labels)} roles")

                self.after(0, apply_choices)
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda: self.role_menu.configure(values=["Load roles from selected server"]))
                self.after(0, lambda: self.role_var.set("Load roles from selected server"))
                self.after(0, lambda error=error: messagebox.showerror("Load Failed", error))

        threading.Thread(target=do_load, daemon=True).start()

    def _load_emojis(self):
        token = read_env_token()
        if not token:
            messagebox.showerror("No Token", "Set DISCORD_TOKEN in Settings first.")
            return
        self.emoji_menu.configure(values=["Loading emojis..."])
        self.emoji_var.set("Loading emojis...")

        def do_load():
            try:
                api = DiscordAPI(token)
                guild_id = self._guild_id_for_role_tools(api)
                choices, custom_count = fetch_emoji_choices(token, guild_id)

                def apply_choices():
                    self.emoji_options = {label_text: emoji for label_text, emoji in choices}
                    labels = list(self.emoji_options.keys())
                    self.emoji_menu.configure(values=labels)
                    self.emoji_var.set(labels[0])
                    self._select_emoji(labels[0])
                    self.app.overview.log(f"[EMOJIS] Loaded {custom_count} server emojis + {len(COMMON_EMOJI_CHOICES)} common emojis")
                    if custom_count == 0:
                        messagebox.showinfo(
                            "No Server Emojis",
                            "Loaded common emojis, but Discord returned 0 custom emojis for this server.",
                        )

                self.after(0, apply_choices)
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda: self.emoji_menu.configure(values=["Load emojis"]))
                self.after(0, lambda: self.emoji_var.set("Load emojis"))
                self.after(0, lambda error=error: messagebox.showerror("Load Failed", error))

        threading.Thread(target=do_load, daemon=True).start()

    def _add_mapping(self):
        emoji = self.emoji_e.get().strip()
        role_id = self.role_id_e.get().strip()
        role_name = self.role_name_e.get().strip()
        if not emoji or not role_id.isdigit() or not role_name:
            messagebox.showerror("Invalid Mapping", "Fill emoji, numeric role ID, and role name.")
            return
        token = read_env_token()
        if token and emoji_name_from_text(emoji):
            try:
                api = DiscordAPI(token)
                guild_id = self._guild_id_for_role_tools(api)
                resolved = resolve_emoji_value(api, guild_id, emoji)
                if resolved == emoji:
                    messagebox.showwarning(
                        "Emoji Not Found",
                        f"Could not find a server emoji named {emoji}. It will be saved exactly as typed.",
                    )
                emoji = resolved
            except Exception as exc:
                messagebox.showwarning("Emoji Lookup Failed", str(exc))
        if any(item["emoji"] == emoji for item in self.rr_data):
            messagebox.showerror("Duplicate Emoji", "That emoji is already mapped.")
            return
        self.rr_data.append({"emoji": emoji, "role_id": role_id, "role_name": role_name})
        self.emoji_e.delete(0, "end")
        self.role_id_e.delete(0, "end")
        self.role_name_e.delete(0, "end")
        self._refresh_list()

    def _refresh_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        if not self.rr_data:
            label(self.list_frame, "No mappings yet.", color=TEXT_3, size=12).pack(pady=10)
            return
        for index, mapping in enumerate(self.rr_data):
            row = ctk.CTkFrame(self.list_frame, fg_color=BG_CARD, corner_radius=6)
            row.pack(fill="x", pady=3, padx=4)
            label(
                row,
                f"{mapping['emoji']} -> {mapping['role_name']} (ID: {mapping['role_id']})",
                size=12,
            ).pack(side="left", padx=12, pady=6)
            btn(row, "X", lambda idx=index: self._remove(idx), color=RED, hover="#B03030", width=36).pack(
                side="right", padx=8, pady=4
            )

    def _remove(self, idx):
        self.rr_data.pop(idx)
        self._refresh_list()

    def _post(self):
        token = read_env_token()
        if not token:
            messagebox.showerror("No Token", "Set DISCORD_TOKEN in Settings first.")
            return

        channel_id = self.ch_entry.get().strip()
        if not channel_id.isdigit():
            messagebox.showerror("Invalid Channel", "Channel ID must be numeric.")
            return
        if not self.rr_data:
            messagebox.showerror("Missing Mappings", "Add at least one emoji-role mapping.")
            return

        title = self.msg_title.get().strip()
        base_desc = self.msg_desc.get("1.0", "end").strip()
        desc_first_line = next((line.strip() for line in base_desc.splitlines() if line.strip()), "")
        panel_name = self.panel_name.get().strip() or desc_first_line or "Untitled role panel"
        mode = role_mode_value(self.mode_var.get())

        def do_post():
            try:
                api = DiscordAPI(token)
                channel = api.get_channel(channel_id)
                guild_id = channel.get("guild_id")
                if not guild_id:
                    raise RuntimeError("Reaction roles must be posted in a server text channel.")

                resolved_mappings = []
                for item in self.rr_data:
                    resolved = dict(item)
                    resolved["emoji"] = resolve_emoji_value(api, guild_id, item["emoji"])
                    resolved_mappings.append(resolved)
                if mode == "button":
                    resolved_mappings = resolved_mappings[:1]

                mapping_lines = "\n".join(f"<@&{item['role_id']}>" for item in resolved_mappings)
                description = (base_desc + "\n\n" if base_desc else "") + mapping_lines

                if self.rr_embed_var.get():
                    embed_payload = {
                        "description": description,
                        "color": COLOR_MAP.get(self.rr_color_var.get(), COLOR_MAP["Blurple"]),
                    }
                    if title:
                        embed_payload["title"] = title
                    message = api.send_message(
                        channel_id,
                        embed=embed_payload,
                    )
                else:
                    content = f"# {title}\n{description}" if title else description
                    message = api.send_message(channel_id, content=content)
                message_id = message["id"]

                failed_reactions = []
                if mode == "reaction":
                    for item in resolved_mappings:
                        try:
                            api.add_reaction(channel_id, message_id, item["emoji"])
                        except Exception as exc:
                            failed_reactions.append(f"{item['emoji']}: {exc}")
                elif mode == "dropdown":
                    components = build_role_select_components(message_id, resolved_mappings)
                    api.edit_message(channel_id, message_id, {"components": components})
                else:
                    components = build_role_button_components(message_id, resolved_mappings)
                    api.edit_message(channel_id, message_id, {"components": components})

                if mode == "reaction" and len(failed_reactions) == len(resolved_mappings):
                    raise RuntimeError(
                        "Message was sent, but no reactions could be added.\n"
                        "Check bot permissions: Add Reactions, Read Message History, Use External Emoji.\n\n"
                        + "\n".join(failed_reactions[:5])
                    )

                cfg = load_config()
                guild_rr = cfg.setdefault("reaction_roles", {}).setdefault(str(guild_id), {})
                guild_rr[str(message_id)] = {
                    "channel_id": str(channel_id),
                    "title": title,
                    "panel_name": panel_name,
                    "description": description,
                    "mode": mode,
                    "kind": "reaction_role",
                    "mappings": {item["emoji"]: item["role_id"] for item in resolved_mappings},
                }
                save_config(cfg)
                if "saved" in self.app.pages:
                    self.after(0, self.app.pages["saved"].refresh)

                self.rr_data.clear()
                self.after(0, self._refresh_list)
                if failed_reactions:
                    notice = "Posted, but some reactions failed:\n" + "\n".join(failed_reactions[:5])
                    self.after(0, lambda: messagebox.showwarning("Posted With Warnings", notice))
                elif mode in ("dropdown", "button"):
                    self.after(
                        0,
                        lambda: messagebox.showinfo(
                            "Posted",
                            f"Message ID: {message_id}\nDropdown can be used immediately while the bot is running.",
                        ),
                    )
                else:
                    self.after(0, lambda: messagebox.showinfo("Posted", f"Message ID: {message_id}"))
                self.after(0, lambda: self.app.overview.log(f"[RR] Posted {mode} message {message_id} in channel {channel_id}"))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: messagebox.showerror("Post Failed", error))

        threading.Thread(target=do_post, daemon=True).start()


class SavedPage(ctk.CTkScrollableFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._build()

    def _build(self):
        label(self, "Saved", size=22, weight="bold").pack(anchor="w", pady=(0, 4))
        label(self, "View, load, or delete saved messages and role panels.", size=13, color=TEXT_2).pack(
            anchor="w", pady=(0, 24)
        )
        btn(self, "Refresh", self.refresh, color=BG_MID, hover=BORDER, width=120).pack(anchor="w", pady=(0, 14))
        self.list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True)
        self.refresh()

    def refresh(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        cfg = load_config()
        rows = []
        for guild_id, messages in cfg.get("messages", {}).items():
            for message_id, item in messages.items():
                rows.append(("message", guild_id, message_id, item))
        for guild_id, messages in cfg.get("reaction_roles", {}).items():
            for message_id, item in messages.items():
                rows.append(("reaction_role", guild_id, message_id, item))

        if not rows:
            label(self.list_frame, "No saved items yet.", color=TEXT_3, size=13).pack(anchor="w", pady=10)
            return

        for item_type, guild_id, message_id, item in rows:
            panel = card(self.list_frame)
            panel.pack(fill="x", pady=(0, 10))
            inner = ctk.CTkFrame(panel, fg_color="transparent")
            inner.pack(fill="x", padx=14, pady=12)
            title = item.get("title") or item.get("content", "")[:40] or "Untitled"
            if item_type == "reaction_role":
                title = item.get("panel_name") or title
            label(inner, f"{item_type} - {title}", size=14, weight="bold").pack(anchor="w")
            label(
                inner,
                f"Guild: {guild_id}  Channel: {item.get('channel_id')}  Message: {message_id}",
                size=11,
                color=TEXT_3,
            ).pack(anchor="w", pady=(2, 8))
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(anchor="w")
            btn(row, "Load", lambda t=item_type, g=guild_id, m=message_id: self._load_item(t, g, m), width=90).pack(
                side="left", padx=(0, 8)
            )
            btn(
                row,
                "Delete Record",
                lambda t=item_type, g=guild_id, m=message_id: self._delete_record(t, g, m),
                color=BG_MID,
                hover=BORDER,
                width=130,
            ).pack(side="left", padx=(0, 8))
            btn(
                row,
                "Delete Discord",
                lambda t=item_type, g=guild_id, m=message_id: self._delete_discord(t, g, m),
                color=RED,
                hover="#B03030",
                width=130,
            ).pack(side="left")

    def _get_item(self, item_type, guild_id, message_id):
        cfg = load_config()
        section = "messages" if item_type == "message" else "reaction_roles"
        return cfg, cfg.get(section, {}).get(str(guild_id), {}).get(str(message_id))

    def _load_item(self, item_type, guild_id, message_id):
        _, item = self._get_item(item_type, guild_id, message_id)
        if not item:
            messagebox.showerror("Missing", "Saved item no longer exists.")
            self.refresh()
            return

        if item_type == "message":
            page = self.app.pages["announce"]
            page.channel_entry.delete(0, "end")
            page.channel_entry.insert(0, item.get("channel_id", ""))
            page.use_embed.set(item.get("type") == "embed")
            page._toggle_embed()
            page.title_entry.delete(0, "end")
            page.title_entry.insert(0, item.get("title", ""))
            page.footer_entry.delete(0, "end")
            page.footer_entry.insert(0, item.get("footer", ""))
            if item.get("color") in COLOR_MAP:
                page.color_var.set(item.get("color"))
            page.msg_box.delete("1.0", "end")
            page.msg_box.insert("1.0", item.get("content", ""))
            self.app.switch_tab("announce")
            return

        page = self.app.pages["reaction"]
        page.ch_entry.delete(0, "end")
        page.ch_entry.insert(0, item.get("channel_id", ""))
        page.panel_name.delete(0, "end")
        page.panel_name.insert(0, item.get("panel_name", ""))
        page.msg_title.delete(0, "end")
        page.msg_title.insert(0, item.get("title", ""))
        page.msg_desc.delete("1.0", "end")
        page.msg_desc.insert("1.0", description_note_only(item.get("description", "")) or DEFAULT_RR_DESCRIPTION)
        if item.get("mode") == "reaction":
            page.mode_var.set(ROLE_MODE_REACTION)
        elif item.get("mode") == "button":
            page.mode_var.set(ROLE_MODE_BUTTON)
        else:
            page.mode_var.set(ROLE_MODE_DROPDOWN)
        page.rr_data = [
            {"emoji": emoji, "role_id": role_id, "role_name": role_id}
            for emoji, role_id in item.get("mappings", {}).items()
        ]
        page._refresh_list()
        self.app.switch_tab("reaction")

    def _delete_record(self, item_type, guild_id, message_id):
        cfg = load_config()
        section = "messages" if item_type == "message" else "reaction_roles"
        cfg.get(section, {}).get(str(guild_id), {}).pop(str(message_id), None)
        save_config(cfg)
        self.refresh()

    def _delete_discord(self, item_type, guild_id, message_id):
        cfg, item = self._get_item(item_type, guild_id, message_id)
        if not item:
            messagebox.showerror("Missing", "Saved item no longer exists.")
            self.refresh()
            return
        token = read_env_token()
        if not token:
            messagebox.showerror("No Token", "Set DISCORD_TOKEN in Settings first.")
            return

        def do_delete():
            try:
                api = DiscordAPI(token)
                api.delete_message(item.get("channel_id"), message_id)
                section = "messages" if item_type == "message" else "reaction_roles"
                cfg.get(section, {}).get(str(guild_id), {}).pop(str(message_id), None)
                save_config(cfg)
                self.after(0, self.refresh)
                self.after(0, lambda: messagebox.showinfo("Deleted", "Discord message and saved record deleted."))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: messagebox.showerror("Delete Failed", error))

        threading.Thread(target=do_delete, daemon=True).start()


class SettingsPage(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._bot_proc = None
        self._build()
        self._load()

    def _build(self):
        label(self, "Settings", size=22, weight="bold").pack(anchor="w", pady=(0, 4))
        label(self, "Token is stored in .env as DISCORD_TOKEN.", size=13, color=TEXT_2).pack(
            anchor="w", pady=(0, 24)
        )

        panel = card(self)
        panel.pack(fill="x", pady=(0, 16))
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="both", padx=20, pady=20)

        section_title(inner, "BOT TOKEN")
        label(inner, "Discord Bot Token", size=12, color=TEXT_2).pack(anchor="w")
        self.token_entry = entry(inner, "Paste your bot token here", width=500)
        self.token_entry.configure(show="*")
        self.token_entry.pack(anchor="w", pady=(4, 8))

        self.show_var = ctk.BooleanVar()
        ctk.CTkCheckBox(
            inner,
            text="Show token",
            variable=self.show_var,
            command=self._toggle_show,
            fg_color=BLURPLE,
            hover_color=BLURPLE_H,
        ).pack(anchor="w", pady=(0, 16))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(anchor="w")
        btn(row, "Save Token", self._save, width=140).pack(side="left", padx=(0, 10))
        btn(row, "Test Connection", self._test, color=BG_MID, hover=BORDER, width=160).pack(side="left")

        self.status_label = label(inner, "", size=12, color=TEXT_3)
        self.status_label.pack(anchor="w", pady=(12, 0))

        controls = card(self)
        controls.pack(fill="x")
        controls_inner = ctk.CTkFrame(controls, fg_color="transparent")
        controls_inner.pack(fill="both", padx=20, pady=20)
        section_title(controls_inner, "BOT PROCESS")
        row2 = ctk.CTkFrame(controls_inner, fg_color="transparent")
        row2.pack(anchor="w")
        btn(row2, "Start Bot", self._start_bot, color="#2D7D46", hover="#245F37", width=140).pack(side="left", padx=(0, 10))
        btn(row2, "Stop Bot", self._stop_bot, color=RED, hover="#B03030", width=140).pack(side="left")
        self.bot_status = label(controls_inner, "Bot process: not started", size=12, color=TEXT_3)
        self.bot_status.pack(anchor="w", pady=(10, 0))

    def _toggle_show(self):
        self.token_entry.configure(show="" if self.show_var.get() else "*")

    def _load(self):
        token = read_env_token()
        if token:
            self.token_entry.insert(0, token)
            self.status_label.configure(text="Token loaded from .env.", text_color=GREEN)
        else:
            self.status_label.configure(text="No DISCORD_TOKEN found in .env.", text_color=YELLOW)

    def _save(self):
        token = self.token_entry.get().strip()
        if not token:
            messagebox.showerror("No Token", "Token cannot be empty.")
            return
        write_env_token(token)
        self.status_label.configure(text="Token saved to .env.", text_color=GREEN)
        self.app.overview.log("[SETTINGS] Token saved to .env")

    def _test(self):
        token = self.token_entry.get().strip()
        if not token:
            self.status_label.configure(text="No token entered.", text_color=YELLOW)
            return
        self.status_label.configure(text="Testing...", text_color=TEXT_3)

        def do_test():
            try:
                api = DiscordAPI(token)
                me = api.get_me()
                guilds = api.get_guilds()
                username = me.get("global_name") or me.get("username", "bot")
                msg = f"Connected as {username} - {len(guilds)} server(s)"
                self.after(0, lambda: self.status_label.configure(text=msg, text_color=GREEN))
                self.after(0, lambda: self.app.overview.log(f"[SETTINGS] {msg}"))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: self.status_label.configure(text=error, text_color=RED))

        threading.Thread(target=do_test, daemon=True).start()

    def _start_bot(self):
        if self._bot_proc and self._bot_proc.poll() is None:
            self.bot_status.configure(text="Bot is already running.", text_color=YELLOW)
            return
        try:
            self._bot_proc = subprocess.Popen(
                [sys.executable, "bot.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.bot_status.configure(text=f"Bot started (PID {self._bot_proc.pid})", text_color=GREEN)
            self.app.overview.log(f"[BOT] Started PID {self._bot_proc.pid}")
        except Exception as exc:
            self.bot_status.configure(text=str(exc), text_color=RED)

    def _stop_bot(self):
        if self._bot_proc and self._bot_proc.poll() is None:
            self._bot_proc.terminate()
            self.bot_status.configure(text="Bot stopped.", text_color=RED)
            self.app.overview.log("[BOT] Stopped")
        else:
            self.bot_status.configure(text="No bot process running.", text_color=TEXT_3)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Discord Bot Panel")
        self.geometry("940x680")
        self.minsize(860, 580)
        self.configure(fg_color=BG_DARK)
        self._build()

    def _build(self):
        sidebar = ctk.CTkFrame(self, width=200, fg_color=BG_MID, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        logo = ctk.CTkFrame(sidebar, fg_color=BLURPLE, height=56, corner_radius=0)
        logo.pack(fill="x")
        label(logo, "Bot Panel", size=15, weight="bold").pack(side="left", padx=16, pady=14)

        self._nav_buttons = {}
        nav_items = [
            ("overview", "Overview"),
            ("announce", "Send Message"),
            ("reaction", "Reaction Roles"),
            ("saved", "Saved"),
            ("settings", "Settings"),
        ]
        nav_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_frame.pack(fill="x", pady=12)
        for key, label_txt in nav_items:
            nav_button = ctk.CTkButton(
                nav_frame,
                text=label_txt,
                anchor="w",
                fg_color="transparent",
                hover_color=BG_CARD,
                text_color=TEXT_2,
                corner_radius=8,
                height=40,
                font=ctk.CTkFont(size=13),
                command=lambda k=key: self.switch_tab(k),
            )
            nav_button.pack(fill="x", padx=10, pady=2)
            self._nav_buttons[key] = nav_button

        label(sidebar, "v1.0.0", size=10, color=TEXT_3).pack(side="bottom", pady=12)

        self.content = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        self.content.pack(side="left", fill="both", expand=True)

        self.pages = {}
        self.overview = OverviewPage(self.content, self)
        self.pages["overview"] = self.overview
        self.pages["announce"] = AnnouncePage(self.content, self)
        self.pages["reaction"] = ReactionRolePage(self.content, self)
        self.pages["saved"] = SavedPage(self.content, self)
        self.pages["settings"] = SettingsPage(self.content, self)
        self.switch_tab("overview")

    def switch_tab(self, key):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True, padx=32, pady=28)
        for nav_key, nav_button in self._nav_buttons.items():
            nav_button.configure(
                fg_color=BG_CARD if nav_key == key else "transparent",
                text_color=TEXT_1 if nav_key == key else TEXT_2,
            )


if __name__ == "__main__":
    app = App()
    app.mainloop()
