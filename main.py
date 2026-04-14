import re
from typing import Dict, Set

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import At, Plain
from astrbot.core.star.filter.event_message_type import EventMessageType


class GroupDefensePlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.threshold = self.config.get("threshold", 2)
        self.report_keyword = self.config.get("reportKeyword", "有内鬼")
        self.reports: Dict[str, Set[str]] = {}
        logger.info(f"[群防御] 插件加载：阈值={self.threshold}，关键词='{self.report_keyword}'")

    def _extract_target_from_message(self, event: AstrMessageEvent) -> str | None:
        """顺序解析：确保先识别关键词，再识别其后的@"""
        message_chain = event.message_obj.message
        if not message_chain: return None
        
        keyword_seen = False
        for comp in message_chain:
            # 1. 找文本组件里的关键词
            if isinstance(comp, Plain):
                if self.report_keyword in comp.text:
                    keyword_seen = True
                    # 尝试提取关键词后的数字
                    after_text = comp.text[comp.text.find(self.report_keyword) + len(self.report_keyword):]
                    match_num = re.search(r"^\s*(\d+)", after_text)
                    if match_num: return str(match_num.group(1))
            
            # 2. 只有关键词出现后，碰到的第一个@才算目标
            elif isinstance(comp, At):
                if keyword_seen:
                    return str(comp.qq)
        return None

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        if not message_obj.group_id: return
        
        # 严格校验：必须以关键词开头，否则不处理（防止误触其他对话）
        if not event.message_str.strip().startswith(self.report_keyword):
            return

        # --- 重点：全路径获取机器人 ID ---
        self_id = None
        try:
            # 路径A: event.bot 对象
            if hasattr(event, "bot") and hasattr(event.bot, "self_id"):
                self_id = str(event.bot.self_id)
            # 路径B: 消息对象的 self_id
            elif hasattr(message_obj, "self_id"):
                self_id = str(message_obj.self_id)
            # 路径C: 从 context 尝试
            elif hasattr(self.context, "get_main_bot"):
                bot = self.context.get_main_bot()
                if bot: self_id = str(bot.self_id)
        except:
            pass

        # 提取目标并统一转为字符串
        target_id = self._extract_target_from_message(event)
        sender_id = str(message_obj.sender.user_id)
        
        if not target_id:
            yield event.plain_result(f"❌ 请使用正确格式：{self.report_keyword} @用户")
            return

        # --- 核心拦截逻辑：顺序非常重要 ---

        # 1. 自保拦截 (即便机器人是管理，也要先触发这句)
        if self_id and str(target_id) == self_id:
            yield event.plain_result("😅 怎么滴，还想把我也端了？")
            return
            
        # 2. 自残拦截
        if str(target_id) == sender_id:
            yield event.plain_result("❌ 为什么要举报你自己？")
            return

        # 3. 管理员拦截
        try:
            # 注意：这里调用 API 可能需要一点时间，放在自检后面能提高响应速度
            target_info = await event.bot.get_group_member_info(
                group_id=int(message_obj.group_id), 
                user_id=int(target_id)
            )
            if target_info and target_info.get("role") in ["admin", "owner"]:
                yield event.plain_result("⚠️ 对方是管理员或群主，我踢不动。")
                return
        except Exception as e:
            logger.debug(f"[群防御] 权限检查跳过: {e}")

        # --- 4. 计数与执行 ---
        if target_id not in self.reports:
            self.reports[target_id] = set()

        reporters = self.reports[target_id]
        if sender_id in reporters:
            yield event.plain_result("⚠️ 你已经举报过该用户了。")
            return

        reporters.add(sender_id)
        current_count = len(reporters)
        yield event.plain_result(f"📢 收到针对 {target_id} 的举报！\n进度：{current_count}/{self.threshold}")

        if current_count >= self.threshold:
            # 踢人动作
            try:
                await event.bot.call_action(
                    "set_group_kick",
                    group_id=int(message_obj.group_id),
                    user_id=int(target_id),
                    reject_add_request=False
                )
                self.reports.pop(target_id, None)
                yield event.plain_result(f"🚫 用户 {target_id} 达到上限，已被移出。")
            except Exception as e:
                logger.error(f"踢人失败: {e}")
                yield event.plain_result("❌ 踢出失败，可能我不是管理员。")

    async def terminate(self):
        self.reports.clear()
