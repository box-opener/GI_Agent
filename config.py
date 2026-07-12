import os
import platform
from dotenv import load_dotenv

# 🌟 强制在此处加载一次环境变量，防止被 main.py 的导入顺序坑到
load_dotenv()

HISTORY_FILE = os.path.join("memory", "chat_context.json")
SYSTEM_RULES_FILE = os.path.join("prompts", "system_rules.md")


def get_env(name, default=""):
    """Return an environment variable, falling back when it is unset or empty."""
    return os.getenv(name) or default


def get_int_env(name, default):
    """Read a positive integer setting without letting an invalid .env value crash startup."""
    raw_value = get_env(name)
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default

# ==========================================
# 🌟 智能路径解析逻辑
# ==========================================
def get_default_bgi_dir():
    """根据系统环境自动推断 BetterGI 的默认安装路径"""
    # 检查是否在 WSL 环境中
    if "microsoft" in platform.uname().release.lower():
        return "/mnt/c/Program Files/BetterGI"
    else:
        # 纯 Windows 环境的默认路径
        return r"C:\Program Files\BetterGI"

# 优先读取 .env 中用户自定义的 BGI_DIR，如果没有配置，则使用智能判断的默认值
BGI_DIR = get_env("BGI_DIR", get_default_bgi_dir())
BGI_EXE = get_env("BGI_EXE", "BetterGI.exe")

BGI_ONE_DRAGON_CONFIG_NAME = get_env("BGI_ONE_DRAGON_CONFIG_NAME", "默认配置.json")
BGI_MAP_CONFIG_NAME = get_env("BGI_MAP_CONFIG_NAME", "地图素材.json")
BGI_GLOBAL_CONFIG_RELATIVE_PATH = get_env(
    "BGI_GLOBAL_CONFIG_RELATIVE_PATH", os.path.join("User", "config.json")
)
BGI_BOSS_CONFIG_RELATIVE_PATH = get_env(
    "BGI_BOSS_CONFIG_RELATIVE_PATH",
    os.path.join(
        "User",
        "JsScript",
        "批量讨伐角色养成材料BOSS",
        "assets",
        "config",
        "config.json",
    ),
)

BGI_ONE_DRAGON_CONFIG = os.path.join(BGI_DIR, "User", "OneDragon", BGI_ONE_DRAGON_CONFIG_NAME)
BGI_MAP_CONFIG = os.path.join(BGI_DIR, "User", "ScriptGroup", BGI_MAP_CONFIG_NAME)
BGI_GLOBAL_CONFIG = os.path.join(BGI_DIR, BGI_GLOBAL_CONFIG_RELATIVE_PATH)
BGI_BOSS_CONFIG = os.path.join(BGI_DIR, BGI_BOSS_CONFIG_RELATIVE_PATH)
BGI_BACKUP_DIR = get_env("BGI_BACKUP_DIR", os.path.join(BGI_DIR, "User", "GI_AgentBackups"))

DEFAULT_UID = get_env("DEFAULT_UID")
MAX_HISTORY_MESSAGES = get_int_env("MAX_HISTORY_MESSAGES", 20)
