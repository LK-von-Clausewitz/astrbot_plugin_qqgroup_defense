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
        logger.info(f"[群防御] 插件加载：阈值={self.threshold}")

    def _extract_target_from_message(self, event: AstrMessageEvent) -> str | None:
        """顺序解析举报目标"""
        message_chain = event.message_obj.message
        if not message_chain: return None
        keyword_seen = False
        for comp in message_chain:
            if isinstance(comp, Plain):
                if self.report_keyword in comp.text:
                    keyword_seen = True
                    after_text = comp.text[comp.text.find(self.report_keyword) + len(self.report_keyword):]
                    match_num = re.search(r"^\s*(\d+)", after_text)
                    if match_num: return str(match_num.group(1)).strip()
            elif isinstance(comp, At):
                if keyword_seen: return str(comp.qq).strip()
        return None

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        if not message_obj.group_id: return
        
        # 严格开头匹配
        if not event.message_str.strip().startswith(self.report_keyword):
            return

        target_id = self._extract_target_from_message(event)
        sender_id = str(message_obj.sender.user_id).strip()
        
        if not target_id:
            yield event.plain_result(f"❌ 请使用正确格式：{self.report_keyword} @用户")
            return

        target_id = str(target_id).strip()

        # 1. 自残拦截：防止自己举报自己
        if target_id == sender_id:
            yield event.plain_result("❌ 为什么要举报你自己？")
            return

        # 2. 管理员/群主/机器人自身 免疫
        # 只要机器人自己是管理，举报机器人就会命中这一条
        try:
            target_info = await event.bot.get_group_member_info(
                group_id=int(message_obj.group_id), 
                user_id=int(target_id)
            )
            if target_info and target_info.get("role") in ["admin", "owner"]:
                yield event.plain_result("⚠️ 对方是管理人员，无法触发举报机制。")
                return
        except Exception as e:
            logger.debug(f"[群防御] 权限检查异常: {e}")

        # 3. 计数逻辑
        if target_id not in self.reports:
            self.reports[target_id] = set()

        reporters = self.reports[target_id]
        if sender_id in reporters:
            yield event.plain_result("⚠️ 你已经举报过该用户了。")
            return

        reporters.add(sender_id)
        current_count = len(reporters)
        yield event.plain_result(f"📢 收到举报！目标：{target_id}\n进度：{current_count}/{self.threshold}")

        # 4. 触发踢人
        if current_count >= self.threshold:
            try:
                # 使用 NapCat/OneBot 标准 API
                await event.bot.call_action(
                    "set_group_kick",
                    group_id=int(message_obj.group_id),
                    user_id=int(target_id),
                    reject_add_request=False
                )
                self.reports.pop(target_id, None)
                yield event.plain_result(f"🚫 用户 {target_id} 举报达标，已被移出群聊。")
            except Exception as e:
                logger.error(f"[群防御] 执行踢人失败: {e}")
                yield event.plain_result("❌ 踢出失败，请确认我是否有管理员权限。")

    async def terminate(self):
        self.reports.clear()
