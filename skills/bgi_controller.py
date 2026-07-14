import os
import json
import subprocess
import config
from api import feishu_api
from skills.config_transaction import ConfigTransactionError, JsonConfigTransaction


def execute_bgi_task(bgi_cmd, decision_lower, store, open_id, uid):
    """异步执行 BGI 逻辑（从原 feishu_main.py 拷贝，路径改为 config 常量）。"""
    try:
        bgi_cmd = bgi_cmd or {}
        energy_task = bgi_cmd.get("energy_task", {})
        free_tasks = bgi_cmd.get("free_task", [])

        target_domain = energy_task.get("target", "无")
        gather_items = [t.get("target") for t in free_tasks if t.get("action") == "gather"]
        gather_str = "、".join(gather_items) if gather_items else "无"

        config_path = config.BGI_ONE_DRAGON_CONFIG
        map_config_path = config.BGI_MAP_CONFIG
        transaction = JsonConfigTransaction(config.BGI_BACKUP_DIR)

        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                bgi_config = json.load(f)

            if energy_task.get("action") == "run_domain":
                bgi_config["DomainName"] = target_domain
                bgi_config["TaskEnabledList"]["自动秘境"] = True
                bgi_config["TaskEnabledList"]["自动地脉花"] = False
                bgi_config["TaskEnabledList"]["突破材料"] = False
            # 🌟 新增：解析并覆写周日/全开时的材料顺位 (1, 2, 或 3)
                # 如果没有传这个值（比如圣遗物本），默认给 "1" 防呆
                domain_index = energy_task.get("domain_index", "1")
                bgi_config["SundayEverySelectedValue"] = str(domain_index)

            elif energy_task.get("action") == "run_leyline":
                bgi_config["TaskEnabledList"]["自动秘境"] = False
                bgi_config["TaskEnabledList"]["自动地脉花"] = True
                bgi_config["TaskEnabledList"]["突破材料"] = False
                bgi_config["LeyLineOneDragonMode"] = True

                global_config_path = config.BGI_GLOBAL_CONFIG
                if os.path.exists(global_config_path):
                    with open(global_config_path, "r", encoding="utf-8") as f:
                        global_config = json.load(f)

                    if "autoLeyLineOutcropConfig" not in global_config:
                        global_config["autoLeyLineOutcropConfig"] = {}

                    global_config["autoLeyLineOutcropConfig"]["leyLineOutcropType"] = target_domain

                    transaction.stage_json(global_config_path, global_config, "global_config")
                    print(f"🌍 全局配置已更新：今日地脉目标锁定为【{target_domain}】")
                else:
                    print(f"⚠️ 找不到全局配置文件 {global_config_path}，无法设置地脉种类！")

            elif energy_task.get("action") == "run_boss":
                bgi_config["TaskEnabledList"]["自动秘境"] = False
                bgi_config["TaskEnabledList"]["自动地脉花"] = False
                bgi_config["TaskEnabledList"]["突破材料"] = True

                boss_config_path = config.BGI_BOSS_CONFIG
                
                # 🌟 修复：带默认值的“自愈型”配置逻辑
                user_team = ""
                # 默认使用 README 中推荐的策略兜底，防止小白连外挂都没打开过
                user_strategy = "万能战斗策略（萌新推荐）" 
                user_timeout = 240
                
                if os.path.exists(boss_config_path):
                    try:
                        with open(boss_config_path, "r", encoding="utf-8") as f:
                            existing_data = json.load(f)
                            if isinstance(existing_data, list) and len(existing_data) > 0:
                                first_boss = existing_data[0]
                                user_team = first_boss.get("team", "")
                                fight_param = first_boss.get("fightParam", {})
                                user_strategy = fight_param.get("strategyName", user_strategy)
                                user_timeout = fight_param.get("timeout", 240)
                    except Exception as e:
                        print(f"⚠️ 读取原有 Boss 配置失败，将使用默认推荐配置兜底: {e}")
                else:
                    print("💡 未检测到 Boss 配置文件，正在为您自动创建默认兜底配置...")

                # 动态生成新配置
                boss_data = [{
                    "name": target_domain,
                    "totalCount": 100,
                    "remainingCount": 100,
                    "team": user_team,
                    "returnToStatueAfterEachRound": True,
                    "farmMode": "一次性",
                    "lastFarmTime": None,
                    "dailyLimitCount": 100,
                    "dailyRemainingCount": 100,
                    "fightParam": {
                        "timeout": user_timeout, 
                        "strategyName": user_strategy 
                    },
                }]

                # 🌟 修复：不再暴力建文件夹！先检查外挂脚本的根基在不在
                boss_dir = os.path.dirname(boss_config_path)
                if os.path.exists(boss_dir):
                    transaction.stage_json(boss_config_path, boss_data, "boss_config")
                        
                    display_team = user_team if user_team else "当前驻场队伍"
                    print(f"👹 Boss 模块接管：已生成 {target_domain} 的讨伐配置（队伍: '{display_team}', 策略: '{user_strategy}'）。")
                else:
                    # 如果连文件夹都没有，说明他根本没下载这个脚本，或者路径不对
                    raise ConfigTransactionError(
                        "未找到 Boss 脚本的运行环境；请先在 BetterGI 中订阅《批量讨伐角色养成材料BOSS》脚本，"
                        "并至少手动运行一次。"
                    )

            # 覆写地图素材
            if gather_items and os.path.exists(map_config_path):
                bgi_config["TaskEnabledList"]["地图素材"] = True
                with open(map_config_path, "r", encoding="utf-8") as f:
                    map_data = json.load(f)

                enabled_count = 0
                consecutive_disabled = 0
                for proj in map_data.get("projects", []):
                    folder_name = proj.get("folderName", "")
                    if any(item in folder_name for item in gather_items):
                        proj["status"] = "Enabled"
                        enabled_count += 1
                        consecutive_disabled = 0
                    else:
                        if consecutive_disabled >= 150:
                            proj["status"] = "Enabled"
                            enabled_count += 1
                            consecutive_disabled = 0
                            print(f"🛡️ [防闪退机制触发] 强制开启隔离路径: {proj.get('name')}")
                        else:
                            proj["status"] = "Disabled"
                            consecutive_disabled += 1

                transaction.stage_json(map_config_path, map_data, "map_materials")

                print(f"🗺️ 地图素材路线已重置，共激活 {enabled_count} 条跑图路线 (含防闪退隔离带)。")
            else:
                bgi_config["TaskEnabledList"]["地图素材"] = False

            transaction.stage_json(config_path, bgi_config, "one_dragon")
            transaction_result = transaction.commit()
            changed_files = ", ".join(path.name for path in transaction_result.changed_files)
            transaction_notice = (
                f"🧾 配置事务已提交：{changed_files}\n"
                f"🗂️ 备份与 Diff：{transaction_result.backup_dir}"
            )
            print(transaction_notice)
            feishu_api.send_feishu_msg(open_id, transaction_notice)

            print(f"\n📝 BetterGI 配置已动态覆写！今日死磕：{target_domain}，顺路采集：{gather_str}")

            # 发工资逻辑
            wallet = store.get("wallet", {"mora": 0, "exp_books": 0, "boss_mats": {}})
            if energy_task.get("action") == "run_leyline":
                if target_domain == "藏金之花":
                    wallet["mora"] += 480000
                    print(f"💰 记账成功：虚拟钱包入账 480,000 摩拉！当前存款：{wallet['mora']}")
                else:
                    wallet["exp_books"] += 40
                    print(f"📕 记账成功：虚拟钱包入账 40 本经验书！当前存款：{wallet['exp_books']}")
            elif energy_task.get("action") == "run_boss":
                boss_name = target_domain
                wallet["boss_mats"][boss_name] = wallet["boss_mats"].get(boss_name, 0) + 12
                print(f"👹 记账成功：虚拟仓库入账 12 个 {boss_name} 掉落材料！当前已积攒：{wallet['boss_mats'][boss_name]} 个")

            store["wallet"] = wallet
            # Persist store is responsibility of caller if needed

            # 启动或测试
            if decision_lower == 'y':
                print("🚀 正在通过任务计划启动 BetterGI 一条龙...")
                feishu_api.send_feishu_msg(open_id, "🚀 正在通过任务计划启动 BetterGI 一条龙...")
                cmd_primary = ["schtasks.exe", "/run", "/tn", r"\StartBetterGI"]
                cmd_fallback = ["schtasks.exe", "/run", "/tn", "StartBetterGI"]
                try:
                    result = subprocess.run(cmd_primary, capture_output=True)
                    if result.returncode != 0:
                        result = subprocess.run(cmd_fallback, capture_output=True)

                    if result.returncode == 0:
                        print("🎉 任务计划已触发，BetterGI 正在执行一条龙。")
                        feishu_api.send_feishu_msg(open_id, "🎉 任务计划已触发，BetterGI 正在执行一条龙。")
                    else:
                        def _decode(raw: bytes) -> str:
                            if not raw:
                                return ""
                            for enc in ("utf-8", "gbk", "cp936"):
                                try:
                                    return raw.decode(enc)
                                except UnicodeDecodeError:
                                    continue
                            return raw.decode("utf-8", errors="replace")

                        err = (_decode(result.stderr) or _decode(result.stdout) or "未知错误").strip()
                        print(f"❌ 任务计划启动失败: {err}")
                        feishu_api.send_feishu_msg(open_id, f"❌ 任务计划启动失败: {err}")
                except Exception as e:
                    print(f"❌ 启动 BetterGI 失败: {e}")
                    feishu_api.send_feishu_msg(open_id, f"❌ 启动 BetterGI 失败: {e}")
            else:
                print("🛠️ [测试模式] 配置文件覆写与虚拟账本更新已完成！成功跳过游戏启动环节。")
                feishu_api.send_feishu_msg(open_id, "🛠️ [测试模式] 配置文件覆写与虚拟账本更新已完成！成功跳过游戏启动环节。")

        else:
            print(f"❌ 找不到配置文件: {config_path}，请检查路径。跳过执行。")
            feishu_api.send_feishu_msg(open_id, f"❌ 找不到配置文件: {config_path}，请检查路径。跳过执行。")

    except Exception as e:
        print(f"❌ 发生错误: {e}")
        feishu_api.send_feishu_msg(open_id, f"❌ 执行配置时发生错误: {str(e)}")
