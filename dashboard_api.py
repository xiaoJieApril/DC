import os
import secrets
import hmac
import hashlib
import base64
import time
import urllib.parse
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from storage import (
    append_audit_log,
    append_moderation_case,
    delete_record,
    init_db,
    load_config,
    save_config,
    set_moderation_settings,
    set_ticket_settings,
    storage_name,
    update_moderation_case,
    update_ticket,
    upsert_message,
    upsert_onboarding,
    upsert_reaction_role,
)


load_dotenv()
init_db()

DISCORD_API = "https://discord.com/api/v10"
COLOR_MAP = {
    "Blurple": 0x5865F2,
    "Green": 0x57F287,
    "Red": 0xED4245,
    "Yellow": 0xFEE75C,
    "White": 0xFFFFFF,
}
DEFAULT_RR_DESCRIPTION = "使用下拉式選單來更改名字顏色"
DEFAULT_ONBOARDING_LANGUAGES = {
    "zh": {"label": "中文", "rules": "請閱讀規則並點擊 Agree 取得 fan role。", "enabled": True, "language_role_id": ""},
    "en": {"label": "English", "rules": "Please read the rules and click Agree to receive the fan role.", "enabled": True, "language_role_id": ""},
    "ja": {"label": "日本語", "rules": "ルールを読んで Agree を押すと fan role を受け取れます。", "enabled": True, "language_role_id": ""},
}
DEFAULT_ONBOARDING_TEXT = {
    "panel_title": "Choose your rules language",
    "panel_description": "Select one language for private rules. If you select multiple languages, English rules will be shown.",
    "panel_placeholder": "Select language",
    "panel_color": "Blurple",
    "rules_title": "{label} Rules",
    "rules_color": "Blurple",
    "rules_footer": "",
    "agree_label": "Agree",
}
SERVER_RULES_ONBOARDING_TEXT = {
    "panel_title": "📜 Server Guidelines",
    "panel_description": "Please choose your language to read the server rules privately. If you select multiple languages, English rules will be shown.",
    "panel_placeholder": "Choose your language",
    "panel_color": "Blurple",
    "rules_title": "{label} Server Guidelines",
    "rules_color": "Blurple",
    "rules_footer": "Respect others. Respect creators. Respect privacy.",
    "agree_label": "✅ Agree and Unlock",
}
SERVER_RULES_LANGUAGES = {
    "zh": {
        "label": "中文",
        "enabled": True,
        "language_role_id": "",
        "rules": """## 📜 伺服器守則｜Server Guidelines｜サーバールール

### 1️⃣ 尊重彼此｜Mutual Respect｜相互尊重
本伺服器嚴禁任何形式的人身攻擊、歧視、騷擾、霸凌、惡意抹黑及仇恨言論。若意見產生分歧時，請理性，針對議題進行討論，對事不對人。

### 2️⃣ 維護氛圍｜Maintain a Healthy Atmosphere｜雰囲気の維持
本伺服器嚴禁引戰、釣魚、帶風向、煽動粉絲對立等行為。成員間私人糾紛請私下協調，或尋求管理團隊介入。請盡量避免討論政治或宗教相關話題，以免引發不必要的爭執。本站支援中、英、日三語交流，請尊重非母語使用者。

### 3️⃣ 全年齡規範｜All-Age Standard｜全年齢対象
本伺服器為全年齡向空間，嚴禁任何色情、裸露、性暗示、R18／NSFW 內容及相關連結。Discord 服務條款所禁止之內容亦同。

### 4️⃣ 資訊查證｜Verify Information｜情報の検証
嚴禁散布謠言、未經證實之消息、惡意揣測及誤導性資訊。如涉及 VTuber 畢業、轉生等敏感議題時，請附上可信來源。

### 5️⃣ 尊重智慧財產｜Respect Intellectual Property｜知的財産の尊重
嚴禁盜用圖片、未經授權轉載他人作品，以及外流會員限定或付費內容等。轉貼時請附上原作者來源，使用前應取得其授權。

### 6️⃣ 隱私絕對保護｜Absolute Privacy Protection｜プライバシーの絶対的保護
隱私是本伺服器最核心的紅線。嚴禁公開、索取或散布任何成員的真實姓名、地址、電話、學校、工作場所、社群帳號等個人資訊。公共頻道嚴禁以明示、暗示、縮寫、謎語或引導查詢等方式討論 VTuber 中之人或前世身份。如欲討論轉生相關話題，請至 `#roles` 領取「👻 深度旅人」身分組後，於專屬頻道進行。

允許討論 VTuber 的過去經歷與當前活動，但嚴禁在公共頻道中連結、暗示或揭示「過去身份」與「當前身份」為同一人。此類行為視同違反隱私紅線，將逕行處分。

### 7️⃣ 禁止洗版與廣告｜No Spam or Advertising｜スパム・広告の禁止
嚴禁洗版、惡意刷屏、大量重複訊息、表情符號氾濫、機器人濫用及惡意標記。未經管理團隊許可，不得宣傳其他伺服器、社群、商業內容或招募資訊。

### 8️⃣ 管理與申訴｜Management and Appeals｜運営と異議申し立て
遇違規行為請在 `#TICKET｜客服` 通報管理團隊處理，請勿於公開頻道對線。初犯者給予一次申訴機會，將賦予「觀察期」身分組並限制部分權限，可於 `#申訴法庭｜appeal-court` 提出申訴。再犯或申訴失敗者，將依情節輕重處以禁言、頻道限制、踢出或封鎖。

紅線行為將逕行處分，不經警告：洩漏個資、惡意騷擾、仇恨言論、詐騙、NSFW 內容、會員限定內容外流、違反 Discord 服務條款，或其他嚴重破壞社群秩序之行為。

### 💖 管理團隊的話
本伺服器無複雜潛規則。謹記三項基本原則：尊重他人、尊重創作者、尊重隱私。如有任何疑問，請使用 `#TICKET｜客服` 聯繫 🛡️ 管理團隊。

### 📋 關於處分機制
🟢 初犯＝機會：初犯者會拿到「觀察期」身分組，暫時限制部分權限，同時保有申訴權利。
🟡 再犯＝後果：可能是禁言、限制頻道、暫時踢出，或永久封鎖。
🔴 紅線＝即時處分：個資、騷擾、仇恨、詐騙、NSFW、付費內容外流、Discord TOS 違規將直接處分。""",
    },
    "en": {
        "label": "English",
        "enabled": True,
        "language_role_id": "",
        "rules": """## 📜 Server Guidelines｜伺服器守則｜サーバールール

### 1️⃣ Mutual Respect｜尊重彼此｜相互尊重
Personal attacks, discrimination, harassment, bullying, slander, and hate speech are strictly prohibited. When disagreements arise, please remain rational and engage in issue-focused discourse rather than personal attacks.

### 2️⃣ Maintain a Healthy Atmosphere｜維護氛圍｜雰囲気の維持
Drama-baiting, trolling, and fan war incitement are strictly prohibited. Personal disputes should be resolved privately or escalated to the staff team. Please refrain from political or religious topics to avoid unnecessary conflicts. This server supports Chinese, English, and Japanese; please be respectful to non-native speakers.

### 3️⃣ All-Age Standard｜全年齡規範｜全年齢対象
This server is an all-ages space. NSFW content, nudity, sexual material, and related links are strictly prohibited. Discord Terms of Service apply.

### 4️⃣ Verify Information｜資訊查證｜情報の検証
Rumors, unverified claims, malicious speculation, and misinformation are prohibited. For sensitive topics such as VTuber graduations or reincarnation, please provide credible sources.

### 5️⃣ Respect Intellectual Property｜尊重智慧財產｜知的財産の尊重
Art theft, unauthorized reposting, and leaking of members-only or paid content are strictly forbidden. Proper credit to original creators is required, and permission must be obtained before sharing.

### 6️⃣ Absolute Privacy Protection｜隱私絕對保護｜プライバシーの絶対的保護
Privacy is the absolute red line of this server. Sharing, requesting, or distributing personal information is prohibited. Public channels strictly forbid any discussion of VTubers' past identities, whether explicit, implied, abbreviated, or alluded. Reincarnation-related discussions must be conducted in designated channels after obtaining the 「👻 Deep Traveler」 role in `#roles`.

Past activities and current activities may be discussed as separate topics, but linking or implying that a past identity and current identity are the same person is prohibited in public channels and will be treated as a privacy violation.

### 7️⃣ No Spam or Advertising｜禁止洗版與廣告｜スパム・広告の禁止
Spam, flooding, repeated messaging, emoji spam, bot abuse, and mass pings are prohibited. Advertising other servers, communities, commercial content, or recruitment requires prior staff approval.

### 8️⃣ Management and Appeals｜管理與申訴｜運営と異議申し立て
Please report violations to staff via `#TICKET｜客服`; do not engage publicly. First-time offenders are granted one appeal opportunity, receive a probationary role with restricted permissions, and may appeal in `#申訴法庭｜appeal-court`. Repeated violations or failed appeals may result in mutes, channel restrictions, kicks, or bans.

Immediate action without warning applies to doxxing, harassment, hate speech, scams, NSFW content, paid content leaks, Discord TOS violations, and severe disruption.

### 💖 A Note from the Staff
No hidden rules. Three core principles: Respect others. Respect creators. Respect privacy. For questions, contact 🛡️ Staff via `#TICKET｜客服`.

### 📋 About Enforcement
🟢 First Offense = A Chance: first-time offenders receive a probationary role and appeal rights.
🟡 Repeated Offense = Consequences: actions may escalate to mutes, restrictions, kicks, or bans.
🔴 Red Line = Immediate Action: doxxing, harassment, hate, scams, NSFW, paid content leaks, and Discord TOS violations receive immediate action.""",
    },
    "ja": {
        "label": "日本語",
        "enabled": True,
        "language_role_id": "",
        "rules": """## 📜 サーバールール｜伺服器守則｜Server Guidelines

### 1️⃣ 相互尊重｜尊重彼此｜Mutual Respect
人格攻撃・差別・ハラスメント・いじめ・誹謗中傷・ヘイトスピーチを固く禁じます。意見が異なる場合は、冷静に建設的な議論をお願いいたします。

### 2️⃣ 雰囲気の維持｜維護氛圍｜Maintain a Healthy Atmosphere
対立煽り・釣り・ファン同士の争いを誘発する行為を固く禁じます。個人的な揉め事は個別に解決するか、スタッフへご連絡ください。政治的・宗教的な話題は、不要な争いを避けるためお控えください。当サーバーは中国語・英語・日本語での交流を支援しております。非母語話者への配慮をお願いいたします。

### 3️⃣ 全年齢対象｜全年齡規範｜All-Age Standard
当サーバーは全年齢対象の空間です。わいせつ・露出・性的暗示・R18／NSFWコンテンツ及び関連リンクを固く禁じます。Discord利用規約も厳守してください。

### 4️⃣ 情報の検証｜資訊查證｜Verify Information
デマ・未確認情報・悪意のある憶測・誤解を招く発言を禁止します。VTuberの卒業・転生などデリケートな話題については、信頼できる情報源を明示してください。

### 5️⃣ 知的財産の尊重｜尊重智慧財產｜Respect Intellectual Property
画像の無断使用・無断転載・有料コンテンツの流出を固く禁じます。転載時は必ず出典を明記し、事前に許可を取得してください。

### 6️⃣ プライバシーの絶対的保護｜隱私絕對保護｜Absolute Privacy Protection
プライバシーは当サーバーにおける絶対的な守備線です。個人情報の共有・要求・拡散を固く禁じます。公開チャンネルにおける「中の人」に関する言及・暗示・略語・謎かけ・誘導を一切禁止します。転生関連の話題は `#roles` にて「👻 深度旅人」ロールを取得の上、専用チャンネルをご利用ください。

過去の活動と現在の活動は別個の話題として扱えますが、公開チャンネルで過去の身份と現在の身份を同一人物として連結・暗示する行為は禁止されます。

### 7️⃣ スパム・広告の禁止｜禁止洗版與廣告｜No Spam or Advertising
連投・スパム・大量メッセージ・絵文字荒らし・Bot乱用・大量メンションを禁止します。他サーバー・コミュニティ・商業コンテンツ・募集情報の宣伝は、事前にスタッフの許可を得てください。

### 8️⃣ 運営と異議申し立て｜管理與申訴｜Management and Appeals
違反行為は `#TICKET｜客服` にてスタッフへご報告ください。初回違反者には異議申し立ての機会を保証し、「観察期間」ロールを付与の上、`#申訴法庭｜appeal-court` にて説明の機会を設けます。再違反または申し立て却下の場合は、ミュート・チャンネル制限・キック・BAN等の措置を取ります。

即時処分対象：個人情報流出・嫌がらせ・ヘイト・詐欺・NSFW・有料コンテンツ流出・Discord利用規約違反・その他重大な秩序破壊。

### 💖 スタッフより
複雑な暗黙ルールはございません。他者への尊重・クリエイターへの尊重・プライバシーの尊重をお守りください。ご不明な点は `#TICKET｜客服` にて 🛡️ スタッフまでお問い合わせください。

### 📋 処分について
🟢 初回違反＝チャンス：観察期間ロールと異議申し立ての権利があります。
🟡 再違反＝結果：ミュート、制限、キック、BAN へ進む場合があります。
🔴 レッドライン＝即時処分：個人情報、嫌がらせ、ヘイト、詐欺、NSFW、有料コンテンツ流出、Discord規約違反は警告なしで処分されます。""",
    },
}
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
BOT_LOG_PATH = LOG_DIR / "dashboard_bot.log"
BOT_PID_PATH = LOG_DIR / "bot.pid"
BOT_LOCK = threading.Lock()
BOT_PROCESS = None
BOT_STARTED_AT = 0.0


def env(name, default=""):
    return os.getenv(name, default).strip()


def allowed_origins():
    origins = {
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }
    public_origin = env("PUBLIC_FRONTEND_ORIGIN")
    if public_origin:
        origins.add(public_origin.rstrip("/"))
    return sorted(origins)


app = FastAPI(title="DC-Gra-vt-bot Dashboard API")
app.add_middleware(
    SessionMiddleware,
    secret_key=env("SESSION_SECRET") or secrets.token_urlsafe(32),
    same_site="lax",
    https_only=False,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginPayload(BaseModel):
    username: str
    password: str


class MessagePayload(BaseModel):
    channel_id: str
    content: str
    use_embed: bool = True
    title: str = "Announcement"
    color: str = "Blurple"
    footer: str = ""


class MappingPayload(BaseModel):
    emoji: str
    role_id: str
    role_name: str = ""


class ReactionRolePayload(BaseModel):
    channel_id: str
    panel_name: str = ""
    title: str = ""
    description: str = DEFAULT_RR_DESCRIPTION
    mode: str = "dropdown"
    use_embed: bool = True
    include_role_mentions: bool = False
    color: str = "Blurple"
    mappings: list[MappingPayload]


class OnboardingLanguagePayload(BaseModel):
    label: str = ""
    rules: str = ""
    enabled: bool = True
    language_role_id: str = ""


class OnboardingPayload(BaseModel):
    enabled: bool = False
    fan_role_id: str = ""
    channel_id: str = ""
    member_role_id: str = ""
    panel_message_id: str = ""
    panel_title: str = DEFAULT_ONBOARDING_TEXT["panel_title"]
    panel_description: str = DEFAULT_ONBOARDING_TEXT["panel_description"]
    panel_placeholder: str = DEFAULT_ONBOARDING_TEXT["panel_placeholder"]
    panel_color: str = DEFAULT_ONBOARDING_TEXT["panel_color"]
    rules_title: str = DEFAULT_ONBOARDING_TEXT["rules_title"]
    rules_color: str = DEFAULT_ONBOARDING_TEXT["rules_color"]
    rules_footer: str = ""
    agree_label: str = DEFAULT_ONBOARDING_TEXT["agree_label"]
    languages: dict[str, OnboardingLanguagePayload] = Field(default_factory=dict)


class SavedUpdatePayload(BaseModel):
    section: str
    guild_id: str
    message_id: str
    payload: dict


class ModerationSettingsPayload(BaseModel):
    probation_role_id: str = ""
    log_channel_id: str = ""


class ModerationCasePayload(BaseModel):
    guild_id: str
    target_user_id: str
    target_display: str = ""
    rule_number: str = ""
    violation_type: str = ""
    severity: str = "normal"
    action: str = "warning"
    reason: str
    evidence_url: str = ""
    notes: str = ""
    status: str = "open"
    probation_role_id: str = ""
    remove_role_id: str = ""
    timeout_minutes: int = 0
    log_channel_id: str = ""


class ModerationResolvePayload(BaseModel):
    status: str = "resolved"
    notes: str = ""


class TicketSettingsPayload(BaseModel):
    ticket_channel_id: str = ""
    log_channel_id: str = ""
    panel_message_id: str = ""
    panel_title: str = "Need help?"
    panel_description: str = "Open a private ticket for staff review. Your message will be visible to staff only."
    button_label: str = "Open Ticket"
    panel_color: str = "Blurple"


class TicketStatusPayload(BaseModel):
    status: str = "resolved"
    notes: str = ""


def bot_returncode():
    global BOT_PROCESS
    if BOT_PROCESS is None:
        return None
    return BOT_PROCESS.poll()


def bot_control_mode():
    return env("BOT_CONTROL_MODE", "process").lower()


def dashboard_bot_control_enabled():
    return bot_control_mode() not in ("systemd", "disabled", "off", "false", "0")


def systemd_bot_status():
    service_name = env("SYSTEMD_BOT_SERVICE", "dc-gra-vt-bot")
    try:
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            cwd=str(BASE_DIR),
            timeout=4,
            check=False,
        )
        pid = subprocess.run(
            ["systemctl", "show", service_name, "--property=MainPID", "--value"],
            cwd=str(BASE_DIR),
            timeout=4,
            check=False,
            capture_output=True,
            text=True,
        )
        raw_pid = (pid.stdout or "").strip()
        return {
            "running": active.returncode == 0,
            "pid": int(raw_pid) if raw_pid.isdigit() and raw_pid != "0" else None,
            "service": service_name,
            "status_available": True,
        }
    except Exception as exc:
        return {
            "running": None,
            "pid": None,
            "service": service_name,
            "status_available": False,
            "status_error": str(exc),
        }


def tail_text(path, max_lines=80):
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-max_lines:])
    except OSError:
        return ""


def bot_status_payload():
    returncode = bot_returncode()
    running = BOT_PROCESS is not None and returncode is None
    control_enabled = dashboard_bot_control_enabled()
    systemd_status = systemd_bot_status() if bot_control_mode() == "systemd" else {}
    return {
        "running": systemd_status.get("running", running),
        "pid": systemd_status.get("pid") if systemd_status else (BOT_PROCESS.pid if BOT_PROCESS is not None and running else None),
        "returncode": returncode,
        "started_at": BOT_STARTED_AT if running else None,
        "mode": "dashboard-managed" if control_enabled else bot_control_mode(),
        "control_enabled": control_enabled,
        "service": systemd_status.get("service"),
        "status_available": systemd_status.get("status_available", True),
        "status_error": systemd_status.get("status_error", ""),
        "log_path": str(BOT_LOG_PATH),
        "last_log": tail_text(BOT_LOG_PATH),
    }


def start_bot_process():
    global BOT_PROCESS, BOT_STARTED_AT
    if not dashboard_bot_control_enabled():
        raise HTTPException(status_code=409, detail="Bot is managed by systemd on this host")
    if not env("DISCORD_TOKEN"):
        raise HTTPException(status_code=500, detail="DISCORD_TOKEN is missing on the server")
    with BOT_LOCK:
        if BOT_PROCESS is not None and BOT_PROCESS.poll() is None:
            return bot_status_payload()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_handle = BOT_LOG_PATH.open("a", encoding="utf-8", errors="replace")
        log_handle.write(f"\n--- Starting bot from dashboard at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log_handle.flush()
        try:
            BOT_PROCESS = subprocess.Popen(
                [sys.executable, "bot.py"],
                cwd=str(BASE_DIR),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
        finally:
            log_handle.close()
        BOT_STARTED_AT = time.time()
        BOT_PID_PATH.write_text(str(BOT_PROCESS.pid), encoding="utf-8")
        return bot_status_payload()


def stop_bot_process():
    global BOT_PROCESS, BOT_STARTED_AT
    if not dashboard_bot_control_enabled():
        raise HTTPException(status_code=409, detail="Bot is managed by systemd on this host")
    with BOT_LOCK:
        if BOT_PROCESS is None or BOT_PROCESS.poll() is not None:
            BOT_PROCESS = None
            BOT_STARTED_AT = 0.0
            return bot_status_payload()
        BOT_PROCESS.terminate()
        try:
            BOT_PROCESS.wait(timeout=12)
        except subprocess.TimeoutExpired:
            BOT_PROCESS.kill()
            BOT_PROCESS.wait(timeout=5)
        status = bot_status_payload()
        BOT_PROCESS = None
        BOT_STARTED_AT = 0.0
        BOT_PID_PATH.unlink(missing_ok=True)
        return status


def require_admin(request: Request):
    if request.session.get("admin") or verify_bearer(request):
        return True
    raise HTTPException(status_code=401, detail="Not logged in")


def auth_secret():
    value = env("SESSION_SECRET")
    if not value:
        raise HTTPException(status_code=500, detail="SESSION_SECRET is missing on the server")
    return value.encode("utf-8")


def create_access_token(username):
    issued = str(int(time.time()))
    body = f"{username}:{issued}"
    sig = hmac.new(auth_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{body}:{sig}".encode("utf-8")).decode("ascii")


def verify_bearer(request: Request):
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return False
    try:
        raw = base64.urlsafe_b64decode(header.split(" ", 1)[1].encode("ascii")).decode("utf-8")
        username, issued, sig = raw.rsplit(":", 2)
        body = f"{username}:{issued}"
        expected = hmac.new(auth_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        return int(time.time()) - int(issued) < 60 * 60 * 24 * 14
    except Exception:
        return False


def is_admin_request(request: Request):
    try:
        return bool(request.session.get("admin") or verify_bearer(request))
    except HTTPException:
        return False


def require_configured_auth():
    if not env("ADMIN_PASSWORD"):
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD is missing on the server")
    if not env("SESSION_SECRET"):
        raise HTTPException(status_code=500, detail="SESSION_SECRET is missing on the server")


def require_logged_in(request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Not logged in")
    return True


def request_actor():
    return env("ADMIN_USERNAME", "admin") or "admin"


def token():
    value = env("DISCORD_TOKEN")
    if not value:
        raise HTTPException(status_code=500, detail="DISCORD_TOKEN is missing on the server")
    return value


def discord_request(method, path, payload=None):
    headers = {
        "Authorization": f"Bot {token()}",
        "Content-Type": "application/json",
    }
    response = requests.request(
        method,
        f"{DISCORD_API}{path}",
        headers=headers,
        json=payload,
        timeout=15,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("message", response.text)
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)
    if response.text:
        return response.json()
    return None


def first_non_empty_line(value):
    for line in str(value or "").splitlines():
        clean = line.strip()
        if clean:
            return clean
    return ""


def parse_custom_emoji(value):
    raw = value.strip()
    if raw.startswith("<:") and raw.endswith(">"):
        name, emoji_id = raw[2:-1].split(":", 1)
        return {"name": name, "id": emoji_id, "animated": False}
    if raw.startswith("<a:") and raw.endswith(">"):
        name, emoji_id = raw[3:-1].split(":", 1)
        return {"name": name, "id": emoji_id, "animated": True}
    return None


def custom_emoji_value(emoji):
    prefix = "a" if emoji.get("animated") else ""
    return f"<{prefix}:{emoji['name']}:{emoji['id']}>"


SHORTCODE_EMOJI = {
    "white_flag": "🏳️",
    "black_flag": "🏴",
    "pirate_flag": "🏴‍☠️",
    "checkered_flag": "🏁",
    "triangular_flag_on_post": "🚩",
    "crossed_flags": "🎌",
    "rainbow_flag": "🏳️‍🌈",
    "transgender_flag": "🏳️‍⚧️",
    "united_nations": "🇺🇳",
}


def flag_shortcode_to_unicode(name):
    normalized = name.lower()
    if normalized in SHORTCODE_EMOJI:
        return SHORTCODE_EMOJI[normalized]
    if normalized.startswith("flag_"):
        code = normalized.removeprefix("flag_")
        if re.fullmatch(r"[a-z]{2}", code):
            return "".join(chr(0x1F1E6 + ord(ch) - ord("a")) for ch in code)
    return ""


def emoji_name_from_text(value):
    raw = value.strip()
    if raw.startswith(":") and raw.endswith(":") and len(raw) > 2:
        return raw[1:-1].lower()
    if re.fullmatch(r"[A-Za-z0-9_]{2,32}", raw):
        return raw.lower()
    return ""


def resolve_emoji_value(guild_id, value):
    raw = value.strip()
    if parse_custom_emoji(raw) or not emoji_name_from_text(raw):
        return raw
    target = emoji_name_from_text(raw)
    shortcode = flag_shortcode_to_unicode(target)
    if shortcode:
        return shortcode
    for emoji in discord_request("GET", f"/guilds/{guild_id}/emojis"):
        if emoji.get("name", "").lower() == target:
            return custom_emoji_value(emoji)
    return raw


def resolve_emoji_detail(guild_id, value):
    raw = value.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Emoji cannot be empty")
    if parse_custom_emoji(raw):
        return {"input": raw, "resolved": raw, "found": True, "kind": "custom"}
    target = emoji_name_from_text(raw)
    if target:
        shortcode = flag_shortcode_to_unicode(target)
        if shortcode:
            return {"input": raw, "resolved": shortcode, "found": True, "kind": "unicode_shortcode", "name": target}
        for emoji in discord_request("GET", f"/guilds/{guild_id}/emojis"):
            if emoji.get("name", "").lower() == target:
                resolved = custom_emoji_value(emoji)
                return {
                    "input": raw,
                    "resolved": resolved,
                    "found": True,
                    "kind": "server",
                    "name": emoji.get("name"),
                    "id": emoji.get("id"),
                    "animated": bool(emoji.get("animated")),
                }
        raise HTTPException(status_code=404, detail=f"Server emoji '{raw}' was not found")
    if any(ord(ch) > 127 for ch in raw):
        return {"input": raw, "resolved": raw, "found": True, "kind": "unicode"}
    raise HTTPException(
        status_code=400,
        detail="Use a Unicode emoji, :server_emoji_name:, server_emoji_name, or <:name:id>.",
    )


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


def role_select_components(message_id, mappings):
    options = []
    for item in mappings[:25]:
        option = {
            "label": (item.get("role_name") or item["role_id"])[:100],
            "value": str(item["role_id"]),
            "description": f"Toggle {(item.get('role_name') or item['role_id'])}"[:100],
        }
        if item.get("emoji"):
            emoji_payload = component_emoji(item["emoji"])
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


def role_button_components(message_id, mappings):
    if not mappings:
        return []
    item = mappings[0]
    button = {
        "type": 2,
        "style": 3,
        "label": (item.get("role_name") or "Accept")[:80],
        "custom_id": f"role_button:{message_id}:{item['role_id']}",
    }
    if item.get("emoji"):
        emoji_payload = component_emoji(item["emoji"])
        if emoji_payload:
            button["emoji"] = emoji_payload
    return [{"type": 1, "components": [button]}]


def model_to_dict(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)


def default_onboarding_config():
    return {
        "enabled": False,
        "fan_role_id": "",
        "channel_id": "",
        "member_role_id": "",
        "panel_message_id": "",
        **DEFAULT_ONBOARDING_TEXT,
        "languages": {code: dict(value) for code, value in DEFAULT_ONBOARDING_LANGUAGES.items()},
    }


def normalize_onboarding_config(value):
    config = default_onboarding_config()
    if not isinstance(value, dict):
        return config
    # Normalize both the current language-role gate and older panel fields.
    config["enabled"] = bool(value.get("enabled", config["enabled"]))
    config["fan_role_id"] = str(value.get("fan_role_id") or value.get("member_role_id") or "")
    config["channel_id"] = str(value.get("channel_id", config["channel_id"]) or "")
    config["member_role_id"] = str(value.get("member_role_id") or value.get("fan_role_id") or "")
    config["panel_message_id"] = str(value.get("panel_message_id", config["panel_message_id"]) or "")
    for key, fallback in DEFAULT_ONBOARDING_TEXT.items():
        config[key] = str(value.get(key, fallback) or fallback)
    if config["panel_color"] not in COLOR_MAP:
        config["panel_color"] = DEFAULT_ONBOARDING_TEXT["panel_color"]
    if config["rules_color"] not in COLOR_MAP:
        config["rules_color"] = DEFAULT_ONBOARDING_TEXT["rules_color"]
    languages = value.get("languages", {})
    if isinstance(languages, dict):
        for code, item in languages.items():
            clean_code = str(code).strip().lower()
            if not clean_code:
                continue
            base = config["languages"].get(clean_code, {"label": clean_code, "rules": "", "enabled": False, "language_role_id": ""})
            if isinstance(item, BaseModel):
                item = model_to_dict(item)
            if isinstance(item, dict):
                config["languages"][clean_code] = {
                    "label": str(item.get("label", base.get("label", clean_code)) or ""),
                    "rules": str(item.get("rules", base.get("rules", "")) or ""),
                    "enabled": bool(item.get("enabled", base.get("enabled", False))),
                    "language_role_id": str(item.get("language_role_id", base.get("language_role_id", "")) or ""),
                }
    return config


def enabled_onboarding_languages(config):
    rows = []
    for code, item in (config.get("languages") or {}).items():
        label = str(item.get("label") or code).strip()
        rules = str(item.get("rules") or "").strip()
        if item.get("enabled") and label and rules:
            rows.append((str(code), label, rules))
    return rows[:25]


def onboarding_panel_payload(guild_id, config):
    # Publish one public selector; Discord sends the rules privately after interaction.
    languages = enabled_onboarding_languages(config)
    if not languages:
        raise HTTPException(status_code=400, detail="Enable at least one language with rules text")
    options = [
        {
            "label": label[:100],
            "value": code[:100],
            "description": "Select multiple to receive English rules"[:100],
        }
        for code, label, _ in languages
    ]
    return {
        "content": None,
        "embeds": [
            {
                "title": str(config.get("panel_title") or DEFAULT_ONBOARDING_TEXT["panel_title"])[:256],
                "description": str(config.get("panel_description") or DEFAULT_ONBOARDING_TEXT["panel_description"])[:4096],
                "color": COLOR_MAP.get(config.get("panel_color"), COLOR_MAP["Blurple"]),
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": f"onboarding_language:{guild_id}",
                        "placeholder": str(config.get("panel_placeholder") or DEFAULT_ONBOARDING_TEXT["panel_placeholder"])[:150],
                        "min_values": 1,
                        "max_values": min(len(options), 25),
                        "options": options,
                    }
                ],
            }
        ],
        "allowed_mentions": {"parse": []},
    }


def server_rules_onboarding_defaults():
    payload = dict(SERVER_RULES_ONBOARDING_TEXT)
    payload["languages"] = {code: dict(item) for code, item in SERVER_RULES_LANGUAGES.items()}
    return payload


def next_case_id(config, guild_id):
    cases = config.get("moderation_cases", {}).get(str(guild_id), [])
    max_seen = 0
    for item in cases:
        raw = str(item.get("case_id", "")).removeprefix("CASE-")
        if raw.isdigit():
            max_seen = max(max_seen, int(raw))
    return f"CASE-{max_seen + 1:04d}"


def normalize_moderation_settings(value):
    if not isinstance(value, dict):
        return {"probation_role_id": "", "log_channel_id": ""}
    return {
        "probation_role_id": str(value.get("probation_role_id") or ""),
        "log_channel_id": str(value.get("log_channel_id") or ""),
    }


def moderation_case_embed(case):
    lines = [
        f"Target: <@{case.get('target_user_id')}>",
        f"Action: {case.get('action')}",
        f"Rule: {case.get('rule_number') or 'unspecified'}",
        f"Severity: {case.get('severity')}",
        f"Status: {case.get('status')}",
        "",
        str(case.get("reason") or ""),
    ]
    if case.get("evidence_url"):
        lines.append(f"Evidence: {case['evidence_url']}")
    if case.get("notes"):
        lines.append(f"Notes: {case['notes']}")
    return {
        "title": f"Moderation {case.get('case_id')}",
        "description": "\n".join(lines)[:4096],
        "color": COLOR_MAP["Yellow"] if case.get("severity") != "red_line" else COLOR_MAP["Red"],
        "footer": {"text": f"Actor: {case.get('actor') or 'dashboard'}"},
    }


def send_moderation_log(case, channel_id):
    if not str(channel_id or "").isdigit():
        return
    discord_request(
        "POST",
        f"/channels/{channel_id}/messages",
        {"embeds": [moderation_case_embed(case)], "allowed_mentions": {"parse": []}},
    )


def normalize_ticket_settings(value):
    if not isinstance(value, dict):
        value = {}
    return {
        "ticket_channel_id": str(value.get("ticket_channel_id") or ""),
        "log_channel_id": str(value.get("log_channel_id") or ""),
        "panel_message_id": str(value.get("panel_message_id") or ""),
        "panel_title": str(value.get("panel_title") or "Need help?")[:256],
        "panel_description": str(
            value.get("panel_description")
            or "Open a private ticket for staff review. Your message will be visible to staff only."
        )[:4096],
        "button_label": str(value.get("button_label") or "Open Ticket")[:80],
        "panel_color": str(value.get("panel_color") or "Blurple"),
    }


def ticket_panel_payload(guild_id, settings):
    # Public entry point; the ticket content is collected later in a private Discord modal.
    return {
        "content": None,
        "embeds": [
            {
                "title": settings["panel_title"],
                "description": settings["panel_description"],
                "color": COLOR_MAP.get(settings.get("panel_color"), COLOR_MAP["Blurple"]),
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 1,
                        "label": settings["button_label"],
                        "custom_id": f"ticket_open:{guild_id}",
                    }
                ],
            }
        ],
        "allowed_mentions": {"parse": []},
    }


def apply_moderation_action(payload, settings):
    action = str(payload.action or "warning")
    guild_id = str(payload.guild_id)
    user_id = str(payload.target_user_id)
    if action == "probation":
        role_id = str(payload.probation_role_id or settings.get("probation_role_id") or "")
        if not role_id.isdigit():
            raise HTTPException(status_code=400, detail="Choose a probation role")
        discord_request("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}")
    elif action == "timeout":
        minutes = int(payload.timeout_minutes or 0)
        if minutes <= 0:
            raise HTTPException(status_code=400, detail="Timeout minutes must be greater than 0")
        until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        discord_request("PATCH", f"/guilds/{guild_id}/members/{user_id}", {"communication_disabled_until": until})
    elif action == "remove_role":
        role_id = str(payload.remove_role_id or "")
        if not role_id.isdigit():
            raise HTTPException(status_code=400, detail="Choose a role to remove")
        discord_request("DELETE", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}")
    return action


@app.get("/api/health")
def health():
    return {"ok": True, "storage": storage_name(), "bot": bot_status_payload()}


@app.get("/api/bot/status", dependencies=[Depends(require_admin)])
def get_bot_status():
    return bot_status_payload()


@app.post("/api/bot/start", dependencies=[Depends(require_admin)])
def start_bot():
    return start_bot_process()


@app.post("/api/bot/stop", dependencies=[Depends(require_admin)])
def stop_bot():
    return stop_bot_process()


@app.post("/api/login")
def login(payload: LoginPayload, request: Request):
    require_configured_auth()
    username = env("ADMIN_USERNAME", "admin")
    password = env("ADMIN_PASSWORD")
    if secrets.compare_digest(payload.username, username) and secrets.compare_digest(payload.password, password):
        request.session["admin"] = True
        return {"ok": True, "access_token": create_access_token(username)}
    raise HTTPException(status_code=401, detail="Invalid username or password")


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"logged_in": is_admin_request(request)}


@app.get("/api/discord/guilds", dependencies=[Depends(require_admin)])
def guilds():
    return discord_request("GET", "/users/@me/guilds")


@app.get("/api/discord/guilds/{guild_id}/channels", dependencies=[Depends(require_admin)])
def channels(guild_id: str):
    data = discord_request("GET", f"/guilds/{guild_id}/channels")
    return [item for item in data if item.get("type") in (0, 5)]


@app.get("/api/discord/guilds/{guild_id}/roles", dependencies=[Depends(require_admin)])
def roles(guild_id: str):
    data = discord_request("GET", f"/guilds/{guild_id}/roles")
    return [item for item in data if item.get("name") != "@everyone" and not item.get("managed")]


@app.get("/api/discord/guilds/{guild_id}/members/search", dependencies=[Depends(require_admin)])
def search_members(guild_id: str, q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=25)):
    # Search guild members for the dashboard mention picker.
    query = urllib.parse.urlencode({"query": q.strip(), "limit": limit})
    data = discord_request("GET", f"/guilds/{guild_id}/members/search?{query}")
    rows = []
    for item in data:
        user = item.get("user") or {}
        user_id = user.get("id")
        if not user_id:
            continue
        username = user.get("global_name") or user.get("username") or user_id
        display_name = item.get("nick") or username
        rows.append(
            {
                "id": user_id,
                "username": username,
                "display_name": display_name,
                "avatar": user.get("avatar"),
            }
        )
    return rows


@app.get("/api/discord/guilds/{guild_id}/emojis", dependencies=[Depends(require_admin)])
def emojis(guild_id: str):
    return discord_request("GET", f"/guilds/{guild_id}/emojis")


@app.get("/api/discord/guilds/{guild_id}/emojis/resolve", dependencies=[Depends(require_admin)])
def resolve_emoji(guild_id: str, value: str = Query(..., min_length=1)):
    return resolve_emoji_detail(guild_id, value)


@app.get("/api/saved", dependencies=[Depends(require_admin)])
def saved():
    return load_config()


@app.get("/api/audit-logs", dependencies=[Depends(require_admin)])
def audit_logs(limit: int = Query(50, ge=1, le=100)):
    return load_config().get("audit_logs", [])[:limit]


@app.get("/api/onboarding/{guild_id}", dependencies=[Depends(require_admin)])
def get_onboarding(guild_id: str):
    # Load the fan-role gate settings for the selected Discord server.
    config = load_config()
    return normalize_onboarding_config(config.get("onboarding", {}).get(str(guild_id), {}))


@app.put("/api/onboarding/{guild_id}", dependencies=[Depends(require_admin)])
def save_onboarding(guild_id: str, payload: OnboardingPayload):
    existing = load_config().get("onboarding", {}).get(str(guild_id), {})
    data = model_to_dict(payload)
    if not data.get("panel_message_id"):
        data["panel_message_id"] = existing.get("panel_message_id", "")
    # Save the dashboard-facing gate without dropping older panel metadata.
    config = normalize_onboarding_config(data)
    upsert_onboarding(guild_id, config)
    append_audit_log(
        "saved",
        "onboarding",
        guild_id,
        config.get("panel_message_id", ""),
        {"channel_id": config.get("channel_id"), "enabled": config.get("enabled")},
        request_actor(),
    )
    return config


@app.post("/api/onboarding/{guild_id}/server-rules-defaults", dependencies=[Depends(require_admin)])
def apply_server_rules_defaults(guild_id: str):
    existing = normalize_onboarding_config(load_config().get("onboarding", {}).get(str(guild_id), {}))
    defaults = server_rules_onboarding_defaults()
    existing.update({key: value for key, value in defaults.items() if key != "languages"})
    existing["languages"] = defaults["languages"]
    config = normalize_onboarding_config(existing)
    upsert_onboarding(guild_id, config)
    append_audit_log("loaded_defaults", "onboarding", guild_id, config.get("panel_message_id", ""), {}, request_actor())
    return config


@app.post("/api/onboarding/{guild_id}/publish", dependencies=[Depends(require_admin)])
def publish_onboarding(guild_id: str):
    config = normalize_onboarding_config(load_config().get("onboarding", {}).get(str(guild_id), {}))
    if not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Enable onboarding before publishing")
    channel_id = str(config.get("channel_id") or "")
    if not channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Choose a rules channel")
    if not str(config.get("fan_role_id") or config.get("member_role_id") or "").isdigit():
        raise HTTPException(status_code=400, detail="Choose the fan role to assign")
    channel = discord_request("GET", f"/channels/{channel_id}")
    if str(channel.get("guild_id")) != str(guild_id):
        raise HTTPException(status_code=400, detail="Selected channel does not belong to this server")

    payload = onboarding_panel_payload(guild_id, config)
    panel_message_id = str(config.get("panel_message_id") or "")
    if panel_message_id:
        try:
            discord_request("PATCH", f"/channels/{channel_id}/messages/{panel_message_id}", payload)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            panel_message_id = ""
    if not panel_message_id:
        message = discord_request("POST", f"/channels/{channel_id}/messages", payload)
        panel_message_id = message["id"]

    config["panel_message_id"] = panel_message_id
    upsert_onboarding(guild_id, config)
    append_audit_log(
        "published",
        "onboarding",
        guild_id,
        panel_message_id,
        {"channel_id": channel_id, "languages": [code for code, _, _ in enabled_onboarding_languages(config)]},
        request_actor(),
    )
    return {"ok": True, "message_id": panel_message_id, "guild_id": guild_id, "record": config}


@app.get("/api/moderation/{guild_id}", dependencies=[Depends(require_admin)])
def get_moderation(guild_id: str, limit: int = Query(50, ge=1, le=100)):
    config = load_config()
    return {
        "settings": normalize_moderation_settings(config.get("moderation_settings", {}).get(str(guild_id), {})),
        "cases": config.get("moderation_cases", {}).get(str(guild_id), [])[:limit],
    }


@app.put("/api/moderation/{guild_id}/settings", dependencies=[Depends(require_admin)])
def save_moderation_settings(guild_id: str, payload: ModerationSettingsPayload):
    settings = normalize_moderation_settings(model_to_dict(payload))
    set_moderation_settings(guild_id, settings)
    append_audit_log("saved_settings", "moderation", guild_id, "", settings, request_actor())
    return settings


@app.post("/api/moderation/cases", dependencies=[Depends(require_admin)])
def create_moderation_case(payload: ModerationCasePayload):
    if not str(payload.guild_id).isdigit():
        raise HTTPException(status_code=400, detail="Choose a server")
    if not str(payload.target_user_id).isdigit():
        raise HTTPException(status_code=400, detail="Target user ID must be numeric")
    if not payload.reason.strip():
        raise HTTPException(status_code=400, detail="Reason is required")
    config = load_config()
    settings = normalize_moderation_settings(config.get("moderation_settings", {}).get(str(payload.guild_id), {}))
    action = apply_moderation_action(payload, settings)
    case = {
        "case_id": next_case_id(config, payload.guild_id),
        "guild_id": str(payload.guild_id),
        "target_user_id": str(payload.target_user_id),
        "target_display": payload.target_display.strip(),
        "rule_number": payload.rule_number.strip(),
        "violation_type": payload.violation_type.strip(),
        "severity": payload.severity if payload.severity in ("normal", "serious", "red_line") else "normal",
        "action": action,
        "reason": payload.reason.strip(),
        "evidence_url": payload.evidence_url.strip(),
        "notes": payload.notes.strip(),
        "status": payload.status if payload.status in ("open", "accepted", "rejected", "escalated", "resolved") else "open",
        "actor": request_actor(),
        "ts": int(time.time()),
    }
    append_moderation_case(payload.guild_id, case)
    log_channel_id = payload.log_channel_id or settings.get("log_channel_id")
    if log_channel_id:
        send_moderation_log(case, log_channel_id)
    append_audit_log("created_case", "moderation", payload.guild_id, case["case_id"], {"action": action, "target": case["target_user_id"]}, request_actor())
    return case


@app.patch("/api/moderation/{guild_id}/cases/{case_id}", dependencies=[Depends(require_admin)])
def resolve_moderation_case(guild_id: str, case_id: str, payload: ModerationResolvePayload):
    status = payload.status if payload.status in ("open", "accepted", "rejected", "escalated", "resolved") else "resolved"
    updated = update_moderation_case(
        guild_id,
        case_id,
        {"status": status, "resolution_notes": payload.notes.strip(), "resolved_ts": int(time.time()), "resolved_by": request_actor()},
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Moderation case not found")
    append_audit_log("resolved_case", "moderation", guild_id, case_id, {"status": status}, request_actor())
    return updated


@app.get("/api/tickets/{guild_id}", dependencies=[Depends(require_admin)])
def get_tickets(guild_id: str, limit: int = Query(50, ge=1, le=100)):
    config = load_config()
    return {
        "settings": normalize_ticket_settings(config.get("ticket_settings", {}).get(str(guild_id), {})),
        "tickets": config.get("tickets", {}).get(str(guild_id), [])[:limit],
    }


@app.put("/api/tickets/{guild_id}/settings", dependencies=[Depends(require_admin)])
def save_ticket_settings(guild_id: str, payload: TicketSettingsPayload):
    existing = normalize_ticket_settings(load_config().get("ticket_settings", {}).get(str(guild_id), {}))
    data = model_to_dict(payload)
    if not data.get("panel_message_id"):
        data["panel_message_id"] = existing.get("panel_message_id", "")
    settings = normalize_ticket_settings(data)
    set_ticket_settings(guild_id, settings)
    append_audit_log("saved_settings", "tickets", guild_id, settings.get("panel_message_id", ""), settings, request_actor())
    return settings


@app.post("/api/tickets/{guild_id}/publish", dependencies=[Depends(require_admin)])
def publish_ticket_panel(guild_id: str):
    settings = normalize_ticket_settings(load_config().get("ticket_settings", {}).get(str(guild_id), {}))
    channel_id = str(settings.get("ticket_channel_id") or "")
    if not channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Choose a ticket channel")
    channel = discord_request("GET", f"/channels/{channel_id}")
    if str(channel.get("guild_id")) != str(guild_id):
        raise HTTPException(status_code=400, detail="Selected ticket channel does not belong to this server")

    payload = ticket_panel_payload(guild_id, settings)
    panel_message_id = str(settings.get("panel_message_id") or "")
    if panel_message_id:
        try:
            discord_request("PATCH", f"/channels/{channel_id}/messages/{panel_message_id}", payload)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            panel_message_id = ""
    if not panel_message_id:
        message = discord_request("POST", f"/channels/{channel_id}/messages", payload)
        panel_message_id = message["id"]

    settings["panel_message_id"] = panel_message_id
    set_ticket_settings(guild_id, settings)
    append_audit_log("published_panel", "tickets", guild_id, panel_message_id, {"channel_id": channel_id}, request_actor())
    return {"ok": True, "message_id": panel_message_id, "settings": settings}


@app.patch("/api/tickets/{guild_id}/{ticket_id}", dependencies=[Depends(require_admin)])
def update_ticket_status(guild_id: str, ticket_id: str, payload: TicketStatusPayload):
    status = payload.status if payload.status in ("open", "resolved", "rejected", "escalated") else "resolved"
    updated = update_ticket(
        guild_id,
        ticket_id,
        {"status": status, "resolution_notes": payload.notes.strip(), "updated_ts": int(time.time()), "updated_by": request_actor()},
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Ticket not found")
    append_audit_log("updated_ticket", "tickets", guild_id, ticket_id, {"status": status}, request_actor())
    return updated


@app.post("/api/messages", dependencies=[Depends(require_admin)])
def send_message(payload: MessagePayload):
    if not payload.channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Channel ID must be numeric")
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    channel = discord_request("GET", f"/channels/{payload.channel_id}")
    guild_id = channel.get("guild_id", "dm")
    body = {}
    record = {
        "channel_id": payload.channel_id,
        "type": "embed" if payload.use_embed else "plain",
        "title": payload.title if payload.use_embed else "",
        "content": payload.content,
        "color": payload.color if payload.use_embed else "",
        "footer": payload.footer if payload.use_embed else "",
    }
    if payload.use_embed:
        embed = {
            "description": payload.content,
            "color": COLOR_MAP.get(payload.color, COLOR_MAP["Blurple"]),
        }
        if payload.title:
            embed["title"] = payload.title
        if payload.footer:
            embed["footer"] = {"text": payload.footer}
        body["embeds"] = [embed]
    else:
        body["content"] = payload.content

    body["allowed_mentions"] = {"parse": ["users", "roles"]}
    result = discord_request("POST", f"/channels/{payload.channel_id}/messages", body)
    upsert_message(guild_id, result["id"], record)
    append_audit_log(
        "sent",
        "messages",
        guild_id,
        result["id"],
        {"channel_id": payload.channel_id, "title": record["title"], "type": record["type"]},
        request_actor(),
    )
    return {"message_id": result["id"], "guild_id": guild_id, "record": record}


@app.post("/api/reaction-roles", dependencies=[Depends(require_admin)])
def create_reaction_role(payload: ReactionRolePayload):
    if not payload.channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Channel ID must be numeric")
    if not payload.mappings:
        raise HTTPException(status_code=400, detail="Add at least one role mapping")
    channel = discord_request("GET", f"/channels/{payload.channel_id}")
    guild_id = channel.get("guild_id")
    if not guild_id:
        raise HTTPException(status_code=400, detail="Reaction roles must be in a server channel")

    mappings = []
    for item in payload.mappings:
        mappings.append(
            {
                "emoji": resolve_emoji_value(guild_id, item.emoji),
                "role_id": str(item.role_id),
                "role_name": item.role_name or str(item.role_id),
            }
        )
    if payload.mode == "button":
        mappings = mappings[:1]

    # Mapping data controls the role component only; visible panel text is written manually.
    footer_text = payload.description.strip()
    description = footer_text
    body = {}
    if payload.use_embed:
        embed_payload = {
            "description": description,
            "color": COLOR_MAP.get(payload.color, COLOR_MAP["Blurple"]),
        }
        if payload.title.strip():
            embed_payload["title"] = payload.title.strip()
        body["embeds"] = [embed_payload]
    else:
        body["content"] = f"# {payload.title.strip()}\n{description}" if payload.title.strip() else description
    body["allowed_mentions"] = {"parse": ["users", "roles"]}

    message = discord_request("POST", f"/channels/{payload.channel_id}/messages", body)
    message_id = message["id"]
    failed_reactions = []
    mode = payload.mode if payload.mode in ("reaction", "button") else "dropdown"
    if mode == "reaction":
        for item in mappings:
            route_emoji = urllib.parse.quote(reaction_route_emoji(item["emoji"]), safe="")
            try:
                discord_request("PUT", f"/channels/{payload.channel_id}/messages/{message_id}/reactions/{route_emoji}/@me")
            except HTTPException as exc:
                failed_reactions.append(f"{item['emoji']}: {exc.detail}")
    elif mode == "dropdown":
        discord_request(
            "PATCH",
            f"/channels/{payload.channel_id}/messages/{message_id}",
            {"components": role_select_components(message_id, mappings)},
        )
    else:
        discord_request(
            "PATCH",
            f"/channels/{payload.channel_id}/messages/{message_id}",
            {"components": role_button_components(message_id, mappings)},
        )

    if mode == "reaction" and len(failed_reactions) == len(mappings):
        raise HTTPException(
            status_code=400,
            detail="Message was sent, but no reactions could be added. Check Add Reactions, Read Message History, and Use External Emoji.",
        )

    record = {
        "channel_id": payload.channel_id,
        "title": payload.title.strip(),
        "panel_name": payload.panel_name.strip() or first_non_empty_line(payload.description) or "Untitled role panel",
        "description": description,
        "include_role_mentions": False,
        "mode": mode,
        "kind": "reaction_role",
        "mappings": {item["emoji"]: item["role_id"] for item in mappings},
    }
    upsert_reaction_role(guild_id, message_id, record)
    append_audit_log(
        "posted",
        "reaction_roles",
        guild_id,
        message_id,
        {"channel_id": payload.channel_id, "panel_name": record["panel_name"], "mode": mode},
        request_actor(),
    )
    return {"message_id": message_id, "guild_id": guild_id, "record": record, "failed_reactions": failed_reactions}


@app.patch("/api/messages/{guild_id}/{message_id}", dependencies=[Depends(require_admin)])
def edit_message(guild_id: str, message_id: str, payload: MessagePayload):
    config = load_config()
    existing = config.get("messages", {}).get(str(guild_id), {}).get(str(message_id))
    if not existing:
        raise HTTPException(status_code=404, detail="Saved message not found")
    body = {"allowed_mentions": {"parse": ["users", "roles"]}}
    record = {
        "channel_id": existing.get("channel_id", payload.channel_id),
        "type": "embed" if payload.use_embed else "plain",
        "title": payload.title if payload.use_embed else "",
        "content": payload.content,
        "color": payload.color if payload.use_embed else "",
        "footer": payload.footer if payload.use_embed else "",
    }
    if payload.use_embed:
        embed = {
            "description": payload.content,
            "color": COLOR_MAP.get(payload.color, COLOR_MAP["Blurple"]),
        }
        if payload.title:
            embed["title"] = payload.title
        if payload.footer:
            embed["footer"] = {"text": payload.footer}
        body["content"] = None
        body["embeds"] = [embed]
    else:
        body["content"] = payload.content
        body["embeds"] = []
    discord_request("PATCH", f"/channels/{record['channel_id']}/messages/{message_id}", body)
    upsert_message(guild_id, message_id, record)
    append_audit_log(
        "updated",
        "messages",
        guild_id,
        message_id,
        {"channel_id": record["channel_id"], "title": record["title"], "type": record["type"]},
        request_actor(),
    )
    return {"message_id": message_id, "guild_id": guild_id, "record": record}


@app.patch("/api/reaction-roles/{guild_id}/{message_id}", dependencies=[Depends(require_admin)])
def edit_reaction_role(guild_id: str, message_id: str, payload: ReactionRolePayload):
    config = load_config()
    existing = config.get("reaction_roles", {}).get(str(guild_id), {}).get(str(message_id))
    if not existing:
        raise HTTPException(status_code=404, detail="Saved role panel not found")

    mappings = []
    for item in payload.mappings:
        mappings.append(
            {
                "emoji": resolve_emoji_value(guild_id, item.emoji),
                "role_id": str(item.role_id),
                "role_name": item.role_name or str(item.role_id),
            }
        )
    if payload.mode == "button":
        mappings = mappings[:1]

    # Mapping data controls the role component only; visible panel text is written manually.
    footer_text = payload.description.strip()
    description = footer_text
    mode = payload.mode if payload.mode in ("reaction", "button") else "dropdown"
    channel_id = existing.get("channel_id", payload.channel_id)

    body = {"allowed_mentions": {"parse": ["users", "roles"]}}
    if payload.use_embed:
        embed_payload = {
            "description": description,
            "color": COLOR_MAP.get(payload.color, COLOR_MAP["Blurple"]),
        }
        if payload.title.strip():
            embed_payload["title"] = payload.title.strip()
        body["content"] = None
        body["embeds"] = [embed_payload]
    else:
        body["content"] = f"# {payload.title.strip()}\n{description}" if payload.title.strip() else description
        body["embeds"] = []

    if mode == "dropdown":
        body["components"] = role_select_components(message_id, mappings)
    elif mode == "button":
        body["components"] = role_button_components(message_id, mappings)
    else:
        body["components"] = []

    discord_request("PATCH", f"/channels/{channel_id}/messages/{message_id}", body)

    failed_reactions = []
    if mode == "reaction":
        for item in mappings:
            route_emoji = urllib.parse.quote(reaction_route_emoji(item["emoji"]), safe="")
            try:
                discord_request("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{route_emoji}/@me")
            except HTTPException as exc:
                failed_reactions.append(f"{item['emoji']}: {exc.detail}")

    record = {
        "channel_id": channel_id,
        "title": payload.title.strip(),
        "panel_name": payload.panel_name.strip() or first_non_empty_line(payload.description) or "Untitled role panel",
        "description": description,
        "include_role_mentions": False,
        "mode": mode,
        "kind": "reaction_role",
        "mappings": {item["emoji"]: item["role_id"] for item in mappings},
    }
    upsert_reaction_role(guild_id, message_id, record)
    append_audit_log(
        "updated",
        "reaction_roles",
        guild_id,
        message_id,
        {"channel_id": channel_id, "panel_name": record["panel_name"], "mode": mode},
        request_actor(),
    )
    return {"message_id": message_id, "guild_id": guild_id, "record": record, "failed_reactions": failed_reactions}


@app.patch("/api/saved", dependencies=[Depends(require_admin)])
def update_saved(payload: SavedUpdatePayload):
    config = load_config()
    section = "messages" if payload.section == "messages" else "reaction_roles"
    config.setdefault(section, {}).setdefault(str(payload.guild_id), {})[str(payload.message_id)] = payload.payload
    save_config(config)
    append_audit_log("updated_record", section, payload.guild_id, payload.message_id, {}, request_actor())
    return {"ok": True}


@app.delete("/api/saved/{section}/{guild_id}/{message_id}", dependencies=[Depends(require_admin)])
def delete_saved(section: str, guild_id: str, message_id: str, delete_discord: bool = False):
    config = load_config()
    table = "messages" if section == "messages" else "reaction_roles"
    item = config.get(table, {}).get(str(guild_id), {}).get(str(message_id))
    if delete_discord and item:
        discord_request("DELETE", f"/channels/{item.get('channel_id')}/messages/{message_id}")
    delete_record(table, guild_id, message_id)
    append_audit_log(
        "deleted" if delete_discord else "deleted_record",
        table,
        guild_id,
        message_id,
        {"channel_id": item.get("channel_id") if item else "", "deleted_discord": delete_discord},
        request_actor(),
    )
    return {"ok": True}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
