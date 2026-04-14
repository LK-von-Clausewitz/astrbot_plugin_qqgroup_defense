import re
from typing import Dict, Set

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import At, Plain
from astrbot.core.star.filter.event_message_type import EventMessageType


class GroupDefensePlugin(Star):
    """
    QQ 群防御插件
    最终修正版：调整逻辑优先级（自保优先）
    """

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.threshold = self.config.get("threshold", 2)
        self.report_keyword = self.config.get("reportKeyword", "有内鬼")
        self.reports: Dict[str, Set[str]] = {}
        logger.info(f"[群防御] 插件加载：阈值={self.threshold}，关键词='{self.report_keyword}'")

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
                    if match_num: return match_num.group(1)
            elif isinstance(comp, At):
                if keyword_seen: return str(comp.qq)
        return None

    async def _kick_group_member(self, event: AstrMessageEvent, group_id: str, user_id: str) -> bool:
        """调用踢人 API"""
        try:
            if hasattr(event, "bot") and hasattr(event.bot, "call_action"):
                await event.bot.call_action("set_group_kick", group_id=int(group_id), user_id=int(user_id), reject_add_request=False)
                return True
            return False
        except Exception as e:
            logger.error(f"[群防御] 踢人失败: {e}")
            return False

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        group_id = message_obj.group_id
        sender_id = str(message_obj.sender.user_id)
        
        # 1. 动态获取机器人 ID
        self_id = None
        if hasattr(event, "bot") and hasattr(event.bot, "self_id"):
            self_id = str(event.bot.self_id)
        elif hasattr(message_obj, "self_id"):
            self_id = str(message_obj.self_id)

        if not group_id or not event.message_str.strip().startswith(self.report_keyword):
            return

        # 2. 提取目标
        target_id = self._extract_target_from_message(event)
        if not target_id:
            yield event.plain_result(f"❌ 请使用正确格式：{self.report_keyword} @用户")
            return

        # --- 优先级调整开始 ---

        # 优先级 1: 机器人自保（必须放在最前面，防止被管理员逻辑拦截）
        if self_id and target_id == self_id:
            yield event.plain_result("😅 怎么滴，还想把我也端了？")
            return
            
        # 优先级 2: 禁止自举
        if target_id == sender_id:
            yield event.plain_result("❌ 为什么要举报你自己？")
            return

        # 优先级 3: 管理员免疫判断
        try:
            target_info = await event.bot.get_group_member_info(
                group_id=int(group_id), 
                user_id=int(target_id)
            )
            if target_info and target_info.get("role") in ["admin", "owner"]:
                yield event.plain_result("⚠️ 对方是管理员或群主，我踢不动。")
                return
        except:
            pass

        # --- 优先级调整结束 ---

        # 计数与踢人逻辑
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
            success = await self._kick_group_member(event, group_id, target_id)
            if success:
                self.reports.pop(target_id, None)
                yield event.plain_result(f"🚫 用户 {target_id} 达到举报上限，已被移出群聊。")
            else:
                yield event.plain_result(f"❌ 踢出失败。请确认我是否有群管理权限。")

    @filter.command_group("defense")
    def defense_group(self): pass

    @defense_group.command("status")
    async def show_status(self, event: AstrMessageEvent):
        if not self.reports:
            yield event.plain_result("📊 当前暂无举报记录。")
            return
        msg = "📊 举报统计：\n" + "\n".join([f"• {k}: {len(v)}人" for k,v in self.reports.items()])
        yield event.plain_result(msg)

    @defense_group.command("clear")
    async def clear_reports(self, event: AstrMessageEvent):
        self.reports.clear()
        yield event.plain_result("✅ 举报记录已清空。")

    async def terminate(self):
        self.reports.clear()
