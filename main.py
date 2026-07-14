try:
    import readline
except ImportError:
    # Windows 环境下没有原生的 readline，直接忽略即可
    pass
import os
import json
import re
import datetime
from dotenv import load_dotenv

# 引入我们拆分出来的核心模块
import config
from brain import memory_manager, llm_brain
from skills import bgi_controller
from skills.config_recovery import list_transactions, load_transaction, restore_transaction
from skills.config_transaction import ConfigTransactionError
from skills.env_reader import fetch_enka_data

load_dotenv()

def refresh_env_context(uid):
    """拉取最新展柜数据并组装上下文字符串。"""
    env_data = fetch_enka_data(uid)
    data_str = json.dumps(env_data, ensure_ascii=False)
    return f"以下是玩家 UID {uid} 的最新展柜数据（JSON）：\n{data_str}"

def handle_rollback_command(user_input):
    """Handle local BetterGI configuration recovery without involving the LLM."""
    parts = user_input.strip().split(maxsplit=1)
    command = parts[0] if parts else ""
    supplied_id = parts[1] if len(parts) == 2 else ""
    if command.lower() != "rollback":
        return False

    transaction_id = supplied_id.strip()
    transactions = list_transactions(config.BGI_BACKUP_DIR)
    if not transactions:
        print("没有找到可用的 BetterGI 配置事务记录。")
        return True

    if not transaction_id:
        print("\n可恢复的 BetterGI 配置事务：")
        for item in transactions:
            print(
                f"- {item['id']} | 状态: {item['status']} | "
                f"文件数: {item['updates']} | 时间: {item['created_at']}"
            )
        transaction_id = input("输入要恢复的事务 ID；直接回车取消：").strip()
        if not transaction_id:
            print("已取消配置恢复。")
            return True

    try:
        transaction_dir, manifest = load_transaction(config.BGI_BACKUP_DIR, transaction_id)
    except ConfigTransactionError as exc:
        print(f"无法读取恢复记录：{exc}")
        return True

    updates = manifest.get("updates", [])
    print(f"\n将恢复到事务 {transaction_dir.name} 执行前的 BetterGI 本地配置：")
    for update in updates:
        print(f"- {update.get('label', 'unknown')}: {update.get('path', 'unknown')}")
    print("注意：此操作只恢复 BetterGI 配置，不会撤销已经发生的游戏内操作。")
    print("建议先关闭 BetterGI，避免它同时写入配置文件。")

    confirmation = input("确认恢复请输入 RESTORE；其他任意输入取消：").strip()
    if confirmation != "RESTORE":
        print("已取消配置恢复。")
        return True

    try:
        result = restore_transaction(config.BGI_BACKUP_DIR, transaction_id)
    except ConfigTransactionError as exc:
        print(f"配置恢复失败：{exc}")
        return True
    except Exception as exc:
        print(f"配置恢复发生未预期错误：{exc}")
        return True

    restored_names = ", ".join(path.name for path in result.restored_files)
    print(f"配置恢复完成：{restored_names}")
    print(f"本次恢复的备份与 Diff：{result.backup_dir}")
    return True


def main():
    print("🚀 原神智能体 CLI 终端版启动中...")

    # 验证 LLM_PROVIDER 配置
    provider = os.getenv("LLM_PROVIDER", "github").lower()
    print(f"🔧 已配置 LLM 提供商: {provider}")
    
    try:
        client = llm_brain._make_client()
        print(f"✅ {provider.upper()} 客户端初始化成功")
    except ValueError as e:
        print(f"❌ {e}")
        return

    # 通过记忆管家加载存储
    store = memory_manager.load_chat_store()
    uid = store.get("uid", config.DEFAULT_UID)
    messages = store.get("messages", [])

    if not store.get("env_context"):
        print("🔄 检测到展柜为空，正在自动拉取首次展柜数据...")
        try:
            store["env_context"] = refresh_env_context(uid)
            print("✅ 初始展柜数据拉取成功！")
        except Exception as e:
            store["env_context"] = f"展柜数据暂不可用：{e}"
            print(f"❌ 展柜初始数据拉取失败: {e}")

    messages = memory_manager.trim_history(messages)
    store["messages"] = messages
    memory_manager.save_chat_store(store)

    if messages:
        print(f"📜 已加载聊天记忆：{len(messages)} 条消息")

    print("\n" + "="*40)
    print("✨ Agent 终端模式已准备就绪！")
    print("命令：exit 退出 | clear 清空记忆 | refresh 刷新展柜上下文 | history 查看历史")
    print("="*40 + "\n")
    print("配置恢复命令：rollback [事务ID]（只恢复 BetterGI 本地配置）\n")

    if not messages:
        opening = "请基于当前展柜数据，先给我今天最优先的一条养成建议。"
        messages.append({"role": "user", "content": opening})
        store["messages"] = memory_manager.trim_history(messages)
        memory_manager.save_chat_store(store)

    # 终端多轮交互循环
    while True:
        try:
            if messages and messages[-1]["role"] == "user":
                print("🧠 大脑正在思考...")
                
                # --- 日期偏移逻辑 ---
                business_time = datetime.datetime.now() - datetime.timedelta(hours=4)
                weekday_num = business_time.isoweekday()
                weekday_map = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
                today_str = f"星期{weekday_map[weekday_num]}"
                
                time_notice = f"\n\n【系统实时时间注入】：今天是{today_str}（已对齐凌晨4点刷新）。请严格核对材料的 schedule，不包含今天的绝对不能排期！同时严禁提及任何不在展柜 JSON 数据中的角色。"
                
                # 虚拟账本与仓库注入
                wallet = store.get("wallet", {"mora": 0, "exp_books": 0, "boss_mats": {}})
                boss_mats_str = "、".join([f"{k}: {v}个" for k, v in wallet.get("boss_mats", {}).items()]) or "无"
                
                wallet_notice = f"\n\n💰 【Agent 虚拟账本】当前已攒下：摩拉 {wallet['mora']}，经验书 {wallet['exp_books']} 本。\n📦 【已刷取Boss材料】：{boss_mats_str}\n（注：这仅代表系统近期的打工收益。规划前请严格对比材料缺口与已刷取数量，若已刷取数量 >= 缺口，必须停止安排该任务！）"

                # 调用拆分出来的 llm_brain 工具函数组装 Prompt
                model_messages = llm_brain.build_model_messages(
                    system_prompt=llm_brain.load_system_prompt(),
                    env_context=store.get("env_context", "") + time_notice + wallet_notice,
                    history_messages=messages,
                )
                
                response = client.chat.completions.create(
                    model=os.getenv("MODEL_NAME", "gpt-4o-mini"),
                    messages=model_messages,
                    temperature=0.7
                )
                ai_reply = response.choices[0].message.content
                print(f"\n🤖 Agent: \n{ai_reply}\n")

                messages.append({"role": "assistant", "content": ai_reply})
                messages = memory_manager.trim_history(messages)
                store["messages"] = messages
                memory_manager.save_chat_store(store)

                # ==========================================
                # 🚀 自动化执行拦截层 (终端专属 HITL 同步阻塞流)
                # ==========================================
                json_match = re.search(r'```json\n(.*?)\n```', ai_reply, re.DOTALL)
                
                if json_match:
                    try:
                        bgi_cmd = json.loads(json_match.group(1))
                        
                        # ==========================================
                        # 🌟 核心拦截层：终端专属的圣遗物意图转化
                        # ==========================================
                        if "energy_task" in bgi_cmd and bgi_cmd["energy_task"].get("action") == "run_artifact":
                            raw_target = bgi_cmd["energy_task"].get("target", "")
                            
                            from skills.artifact_match import get_domain_by_user_intent
                            raw_data_path = os.path.join("memory", "artifact_get_methods_raw.json")
                            real_domain = "未找到对应副本"
                            
                            try:
                                if os.path.exists(raw_data_path):
                                    with open(raw_data_path, "r", encoding="utf-8") as f:
                                        raw_json_data = json.load(f)
                                    real_domain = get_domain_by_user_intent(raw_target, raw_json_data)
                            except Exception as e:
                                print(f"❌ 圣遗物映射发生错误: {e}")
                            
                            if real_domain != "未找到对应副本":
                                print(f"🔄 字典映射触发：将大模型推测的【{raw_target}】纠正为副本【{real_domain}】")
                                bgi_cmd["energy_task"]["target"] = real_domain
                                bgi_cmd["energy_task"]["action"] = "run_domain"
                        # ==========================================
                        
                        energy_task = bgi_cmd.get("energy_task", {})
                        free_tasks = bgi_cmd.get("free_task", [])
                        
                        target_domain = energy_task.get("target", "无")
                        gather_items = [t.get("target") for t in free_tasks if t.get("action") == "gather"]
                        gather_str = "、".join(gather_items) if gather_items else "无"

                        print("\n" + "="*50)
                        print(f"⚡ Agent 申请接管键鼠执行自动化流水线：")
                        print(f"⚔️ 体力目标：{target_domain}")
                        print(f"🌿 采集目标：{gather_str}")
                        print("="*50)
                        
                        # 终端实时人工拦截
                        user_decision = input(f"🛑 [系统拦截] 请确认是否执行上述计划？\n👉 输入 'y' 批准执行并启动 BetterGI\n👉 输入 't' 仅测试覆写配置\n👉 输入 'exit' 或 'clear' 退出/清空\n👉 或直接输入反驳意见 (例如: '不想刷这个，去打地脉')\n请决定: ").strip()
                        
                        if not user_decision:
                            continue
                            
                        decision_lower = user_decision.lower()

                        if handle_rollback_command(user_decision):
                            continue
                        
                        if decision_lower in ['exit', 'quit', '退出']:
                            print("👋 记忆已保存，再见！")
                            break
                            
                        if decision_lower == 'clear':
                            messages = []
                            store = {"uid": uid, "env_context": "", "messages": [], "wallet": {"mora": 0, "exp_books": 0, "boss_mats": {}}, "pending_task": None}
                            if os.path.exists(config.HISTORY_FILE):
                                os.remove(config.HISTORY_FILE)
                            print("🧹 记忆已清空，请重新运行程序。")
                            break

                        if decision_lower in ['y', 't']:
                            # 🌟 直接调用复写好的物理外挂模块
                            bgi_controller.execute_bgi_task(bgi_cmd, decision_lower, store, open_id="CLI_USER", uid=uid)
                            
                            # 执行完毕后，重新拉取可能被记账更新过的最新进度
                            store = memory_manager.load_chat_store()
                            messages = store.get("messages", [])
                            continue # 继续下一轮循环
                        else:
                            print(f"\n🚫 审批已驳回。正在将你的要求反馈给大脑重新规划...")
                            feedback_msg = f"我拒绝了刚才的执行申请。我的新要求是：{user_decision}。请根据我的新要求重新评估，并输出新的 JSON 指令。"
                            messages.append({"role": "user", "content": feedback_msg})
                            messages = memory_manager.trim_history(messages)
                            store["messages"] = messages
                            memory_manager.save_chat_store(store)
                            continue # 直接跳转回大脑思考阶段
                            
                    except json.JSONDecodeError:
                        print("⚠️ Agent 输出的 JSON 格式有误，跳过自动化拦截。")

            # 正常用户聊天输入环节
            user_input = input("👤 旅行者 (你): ").strip()

            if not user_input:
                continue

            if handle_rollback_command(user_input):
                continue

            if user_input.lower() in ['exit', 'quit', '退出']:
                print("👋 记忆已保存，再见！")
                break

            if user_input.lower() == 'clear':
                messages = []
                store = {"uid": uid, "env_context": "", "messages": [], "wallet": {"mora": 0, "exp_books": 0, "boss_mats": {}}, "pending_task": None}
                if os.path.exists(config.HISTORY_FILE):
                    os.remove(config.HISTORY_FILE)
                print("🧹 记忆已清空，请重新运行程序。")
                break

            if user_input.lower() == 'refresh':
                print("🔄 正在刷新最新展柜上下文...")
                try:
                    store["env_context"] = refresh_env_context(uid)
                    memory_manager.save_chat_store(store)
                    print("✅ 展柜上下文已刷新。")
                except Exception as e:
                    print(f"❌ 刷新失败: {e}")
                continue

            if user_input.lower() == 'history':
                print(f"📚 当前历史消息数：{len(messages)}")
                continue

            messages.append({"role": "user", "content": user_input})
            messages = memory_manager.trim_history(messages)
            store["messages"] = messages
            memory_manager.save_chat_store(store)

        except KeyboardInterrupt:
            print("\n👋 强制退出。")
            break
        except Exception as e:
            print(f"❌ 发生错误: {e}")
            break

if __name__ == "__main__":
    main()
