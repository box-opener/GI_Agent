import json
import os
import requests
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def load_yatta_dict_safely():
    """按需精准提取：168特产 + 严格锁定最高级(Ⅳ)的秘境材料 + 自动补全秘境名称"""
    dict_path = "memory/game_dict_baike_full.json"
    boss_dict_path = "memory/boss_drops_dict.json" # 🌟 新增 Boss 掉落字典路径
    id_to_zh = {}

    # 🌟 新增：读取 Boss 掉落字典并构建【反向查询表】 (材料名 -> Boss名)
    mat_to_boss = {}
    if os.path.exists(boss_dict_path):
        try:
            with open(boss_dict_path, "r", encoding="utf-8") as f:
                boss_drops = json.load(f)
                for boss, drops in boss_drops.items():
                    for drop in drops:
                        mat_to_boss[drop] = boss
        except Exception as e:
            print(f"⚠️ 读取 Boss 掉落字典失败: {e}")
    else:
        print(f"⚠️ 未找到 Boss 掉落字典: {boss_dict_path}，将降级使用正则匹配。")
    
    if not os.path.exists(dict_path):
        print(f"⚠️ 未找到字典文件: {dict_path}")
        return id_to_zh
        
    schedule_pattern = re.compile(r'周[一二三四五六日]')
    
    DOMAIN_FIX_MAP = {
        "水光之城": "塞西莉亚苗圃", "深没之谷": "塞西莉亚苗圃", "渴水的废都": "塞西莉亚苗圃",
        "雷云祭坛": "震雷连山密宫", "鸣雷废墟": "震雷连山密宫", "古雷试炼场": "震雷连山密宫",
        "沉沙之渊": "砂流之庭", "砂之祭场": "砂流之庭", "流沙之葬": "砂流之庭",
        "云垢": "有顶塔", "思惑": "有顶塔", "引业": "有顶塔",
        "机思": "深潮的余响", "匠理": "深潮的余响", "奇械": "深潮的余响",
        "冥见": "深古瞭望所", "究观": "深古瞭望所", "测度": "深古瞭望所",
        "明辉": "失落的月庭", "祷颂": "失落的月庭", "祭月": "失落的月庭",
        
        "霜凝祭坛": "忘却之峡", "冰封废渊": "忘却之峡", "沉睡之国": "忘却之峡",
        "炽炎祭场": "太山府", "深炎之底": "太山府", "焚尽之环": "太山府",
        "初雷幽谷": "菫色之庭", "真葛废都": "菫色之庭", "菫染之国": "菫色之庭",
        "圆镜": "昏识塔", "律藏": "昏识塔", "妙语": "昏识塔",
        "旋韵": "苍白的遗荣", "箴铭": "苍白的遗荣", "琅诵": "苍白的遗荣",
        "转竟": "蕴火的幽墟", "旋复": "蕴火的幽墟", "空华": "蕴火的幽墟",
        "墟都": "无光的深都", "覆巢": "无光的深都", "遗荫": "无光的深都"
    }
        
    try:
        with open(dict_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        def dig_data(node):
            if isinstance(node, dict):
                if "url" in node and "name_zh" in node:
                    for part in str(node["url"]).split("/"):
                        if part.isdigit():
                            final_materials = []
                            
                            # 1. 角色特产 (168 锚定)
                            total_cost = node.get("ascension_total_cost", {}).get("materials", {})
                            for m_name, m_count in total_cost.items():
                                if m_count == 168:
                                    final_materials.append({
                                        "name": m_name,
                                        "type": "特产",
                                        "schedule": "大世界采集"
                                    })
                                    break 

                            # 2. 秘境材料扫描 (修复双黄蛋：引入 seen_mats 去重)
                            source_categories = [
                                node.get("talent_materials", {}).get("sources", {}),
                                node.get("ascension_material_sources", {})
                            ]
                            
                            seen_mats = set()
                            for source_dict in source_categories:
                                for m_name, m_info in source_dict.items():
                                    # 去重逻辑
                                    if m_name in seen_mats:
                                        continue
                                        
                                    sched = m_info.get("schedule")
                                    if sched and schedule_pattern.search(sched):
                                        sched = sched.strip()
                                        # 🌟 核心修复：放宽匹配条件！
                                        # 只要字符串里【包含】中文的 Ⅳ 或者英文的 IV，且不是泛指的 /Ⅳ 即可
                                        if ('Ⅳ' in sched or 'IV' in sched) and ('/Ⅳ' not in sched):
                                            seen_mats.add(m_name) # 记录已处理的材料
                                            
                                            found_fix = False
                                            for keyword, real_name in DOMAIN_FIX_MAP.items():
                                                if keyword in sched:
                                                    sched = f"【{real_name}】{sched}"
                                                    found_fix = True
                                                    break
                                            
                                            final_materials.append({
                                                "name": m_name,
                                                "type": "秘境材料",
                                                "schedule": sched
                                            })

                            # 3. 提取大世界 Boss 名称及材料 (增强版正则与回退机制)
                            # 🌟 3. 提取大世界 Boss 名称及材料 (反向查表优先，正则兜底)
                            boss_mat_name = None
                            boss_name = None
                            try:
                                tiers = node.get("ascension_materials_detail", {}).get("tiers", [])
                                if len(tiers) > 1:
                                    tier_mats = tiers[1].get("materials", {})
                                    mat_keys = list(tier_mats.keys())
                                    if len(mat_keys) > 1:
                                        boss_mat_name = mat_keys[1] 
                                        
                                        # 🌟 第一优先级：通过 boss_drops_dict 反向精确查找
                                        if boss_mat_name in mat_to_boss:
                                            boss_name = mat_to_boss[boss_mat_name]
                                        else:
                                            # 第二优先级：旧版正则回退匹配（万一新字典没更新全）
                                            sources = node.get("ascension_material_sources", {}).get(boss_mat_name, {}).get("source_lines", [])
                                            for line in sources:
                                                match = re.search(r'(?:【?\d+级以上】?)(.*?)掉落', line)
                                                if match:
                                                    boss_name = match.group(1).strip()
                                                    break
                                            
                                            if not boss_name:
                                                for line in sources:
                                                    if "掉落" in line and "获得" not in line:
                                                        boss_name = line.split("掉落")[0].replace("获得方式：", "").strip("【】 ")
                                                        break
                                                        
                                            if not boss_name:
                                                boss_name = f"{boss_mat_name}的来源Boss"
                            except Exception:
                                pass

                            id_to_zh[part] = {
                                "name": node["name_zh"],
                                "materials": final_materials,
                                "boss_mat_name": boss_mat_name, 
                                "boss_name": boss_name          
                            }
                            break
                for value in node.values():
                    dig_data(value)
            elif isinstance(node, list):
                for item in node:
                    dig_data(item)
                    
        dig_data(data)
        print(f"📚 百科字典加载完毕：已根据七国规则完成秘境名称自动校准与Boss寻址。")
        return id_to_zh
        
    except Exception as e:
        print(f"❌ 解析百科字典失败: {e}")
        return {}


def _build_retry_session():
    retry = Retry(total=3, connect=3, read=3, backoff_factor=0.8, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=frozenset(["GET"]), raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def fetch_enka_data(uid):
    url = f"https://enka.network/api/uid/{uid}"
    headers = {"User-Agent": "GenshinAgent/1.0 (Testing)"}
    proxy_url = os.getenv("GENSHIN_PROXY", "http://127.0.0.1:7890")
    proxies = {"http": proxy_url, "https": proxy_url}
    use_proxy = os.getenv("ENKA_USE_PROXY", "1") != "0"
    
    yatta_dict = load_yatta_dict_safely()
    print(f"🔄 正在拉取 UID {uid} 的展柜数据...")

    try:
        data = None
        plans = [(proxies, "代理")] if use_proxy else []
        plans.append((None, "直连"))
        
        for p_proxies, p_name in plans:
            try:
                with _build_retry_session() as session:
                    response = session.get(url, headers=headers, proxies=p_proxies, timeout=(5, 20))
                response.raise_for_status()
                data = response.json()
                break
            except: continue
        
        if not data: return {"error": "网络连通失败"}
        
        raw_list = data.get("avatarInfoList", [])
        clean_avatars = []
        for avatar in raw_list:
            avatar_id = str(avatar.get("avatarId", ""))
            dict_data = yatta_dict.get(avatar_id, {"name": f"未知角色({avatar_id})", "materials": []})
            
            level = avatar.get("propMap", {}).get("4001", {}).get("val", "0")
            
            weapon_info = {"name": "未装备", "level": 0, "materials": []}
            for equip in avatar.get("equipList", []):
                if "weapon" in equip:
                    w_id = str(equip.get("itemId", ""))
                    w_dict = yatta_dict.get(w_id, {"name": f"未知武器({w_id})", "materials": []})
                    weapon_info = {
                        "name": w_dict["name"], 
                        "level": int(equip.get("weapon", {}).get("level", 0)),
                        "materials": w_dict.get("materials", [])
                    }
                    break 

            # 🌟 核心修复：深拷贝材料列表，避免污染全局 yatta_dict 缓存
            char_materials = list(dict_data.get("materials", []))
            
            # 追加 Boss 材料逻辑
            level_int = int(level)
            b_name = dict_data.get("boss_name")
            if b_name and level_int < 81:
                needed = 0
                if level_int < 40: needed = 46
                elif level_int < 50: needed = 44
                elif level_int < 60: needed = 40
                elif level_int < 70: needed = 32
                elif level_int < 81: needed = 20
                
                if needed > 0:
                    char_materials.append({
                        "name": dict_data.get("boss_mat_name", "Boss材料"),
                        "type": "Boss材料",
                        "schedule": f"【{b_name}】",
                        "needed": needed
                    })

            clean_avatars.append({
                "name": dict_data.get("name"),
                "level": level_int,
                "materials": char_materials, # 使用独立的深拷贝列表
                "weapon": weapon_info,
                "skills": avatar.get("skillLevelMap", {})
            })
            
        print(f"✅ 成功读取 {len(clean_avatars)} 名角色及装备数据。")
        return {"avatars": clean_avatars}
        
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import config

    res = fetch_enka_data(config.DEFAULT_UID)
    print(json.dumps(res, ensure_ascii=False, indent=4))
