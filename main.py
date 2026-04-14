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
    增强版：包含管理员免疫、机器人自保、顺序解析及接口修复
    """

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.threshold = self.config.get("threshold", 2)
        self.report_keyword = self.config.get("reportKeyword", "有内鬼")

        # 举报记录：target_id -> Set[reporter_id]
        self.reports: Dict[str, Set[str]] = {}

        logger.info(
            f"[群防御] 插件加载成功：阈值={self.threshold}，关键词='{self.report_keyword}'"
        )

    def _extract_target_from_message(self, event: AstrMessageEvent) -> str | None:
        """
        按顺序从消息中提取被举报的目标
        """
        message_chain = event.message_obj.message
        if not message_chain:
            return None

        keyword_seen = False
        
        for comp in message_chain:
            # 1. 检测关键词
            if isinstance(comp, Plain):
                if self.report_keyword in comp.text:
                    keyword_seen = True
                    # 检查紧跟关键词后的数字 ID (例如: 有内鬼 12345)
                    after_text = comp.text[comp.text.find(self.report_keyword) + len(self.report_keyword):]
                    match_num = re.search(r"^\s*(\d+)", after_text)
                    if match_num:
                        return match_num.group(1)
            
            # 2. 只有在关键词之后出现的 @ 才是有效的举报目标
            elif isinstance(comp, At):
                if keyword_seen:
                    return str(comp.qq)
        
        # 3. 兜底正则匹配 (兼容某些特殊序列化情况)
        message_raw = event.message_str.strip()
        k_idx = message_raw.find(self.report_keyword)
        if k_idx != -1:
            after_keyword = message_raw[k_idx + len(self.report_keyword):]
            match_at = re.search(r"\[At:(\d+)\]", after_keyword)
            if match_at:
                return match_at.group(1)

        return None

    async def _kick_group_member(
        self, event: AstrMessageEvent, group_id: str, user_id: str, reason: str = "多人举报"
    ) -> bool:
        """
        调用 API 踢出群成员 (兼容 Napcat/OneBot)
        """
        try:
            # 优先使用底层 call_action，这是最直接有效的方式
            if hasattr(event, "bot") and hasattr(event.bot, "call_action"):
                await event.bot.call_action(
                    "set_group_kick",
                    group_id=int(group_id),
                    user_id=int(user_id),
                    reject_add_request=False
                )
                return True
                
            # 备选：通过 platform 发送
            platform_name = event.get_platform_name()
            platform = self.context.get_platform(platform_name)
            if platform and hasattr(platform, "send"):
                payload = {
                    "type": "set_group_kick",
                    "group_id": int(group_id),
                    "user_id": int(user_id),
                    "reject_add_request": False,
                }
                result = await platform.send(payload)
                return result and result.get("status") == "ok"
                
            return False
        except Exception as e:
            logger.error(f"[群防御] 踢人接口调用失败: {e}")
            return False

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        """
        监听并处理举报逻辑
        """
        message_obj = event.message_obj
        group_id = message_obj.group_id
        sender_id = message_obj.sender.user_id
        self_id = event.bot_id # 获取机器人自身QQ

        if not group_id: return
        if not event.message_str.strip().startswith(self.report_keyword): return

        # 1. 提取目标 ID
        target_id = self._extract_target_from_message(event)
        if not target_id:
            yield event.plain_result(f"❌ 请使用正确格式：{self.report_keyword} @用户")
            return

        # 2. 逻辑保护：机器人自保
        if target_id == self_id:
            yield event.plain_result("😅 怎么滴，还想把我也端了？")
            return
            
        # 3. 逻辑保护：防止自残
        if target_id == sender_id:
            yield event.plain_result("❌ 为什么要举报你自己？")
            return

        # 4. 逻辑保护：管理员/群主免疫
        try:
            # 调用 OneBot API 获取目标成员信息
            target_info = await event.bot.get_group_member_info(
                group_id=int(group_id), 
                user_id=int(target_id)
            )
            if target_info and target_info.get("role") in ["admin", "owner"]:
                yield event.plain_result("⚠️ 对方是管理员或群主，我踢不动。")
                return
        except Exception as e:
            logger.debug(f"无法获取目标权限信息（可能非管理）: {e}")

        # --- 5. 计数逻辑 ---
        if target_id not in self.reports:
            self.reports[target_id] = set()

        reporters = self.reports[target_id]
        if sender_id in reporters:
            yield event.plain_result("⚠️ 你已经举报过该用户了。")
            return

        reporters.add(sender_id)
        current_count = len(reporters)

        yield event.plain_result(
            f"📢 收到针对 {target_id} 的举报！\n进度：{current_count}/{self.threshold}"
        )

        # --- 6. 触发踢人 ---
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
